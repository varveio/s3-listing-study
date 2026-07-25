"""``--scope union``: fan-out completeness across shard receipts.

Each shard is a wrapper receipt. This RE-DERIVES every shard against its own
prefix scope — shard verdicts are not trusted — then: concatenates all shards'
normalized outputs as a MULTISET and counts cross-shard duplicates BEFORE
dedup; checks the combined output against the FULL manifest exactly; and
verifies scope coverage, where the union of shard prefixes plus an EXPLICITLY
DESIGNATED remainder shard must cover the manifest's keyspace, or a root-level
key belongs to no shard and the union is STRUCTURALLY incomplete — a plan
defect (ERROR), not a tool finding.

The union mirrors the single-receipt path everywhere it issues a verdict: it
binds mode across the PREFIX shards (the designated remainder is exempt —
covering the unprefixed remainder legitimately needs a different request shape,
so it is normalized under its OWN mode against the orphan keys), refuses
redaction-altered or truncated payloads, selects each shard's verified stream
from ``run.meta``, copies-then-hashes-then-checks, and re-lists the reference
before ANY FAIL so bucket drift is never attributed to the tool.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import report
from .common import (
    bind_streams,
    generated_utc,
    head,
    manifest_field_count,
    normalize,
    read_manifest,
    relist,
    say,
    snapshot,
)
from .compare import Row, malformed_rows, split_records, staged
from .errors import UnionError, VerifierError
from .meta import read_meta, real
from .registry_source import RegistrySource
from .security import SecurityBoundary, docker_status
from .single import ADAPTER_MTIME, MANIFEST_MTIME, check_mtime_shape

STRUCTURAL_NOTE = (
    "**STRUCTURAL INCOMPLETENESS** — {orphan} manifest key(s) live under no shard prefix, and no "
    "explicit unprefixed-remainder shard (--remainder) was supplied to attribute them to. A union "
    "of prefixes never lists a root-level key (e.g. `index.html`): this is a coverage defect in "
    "the FAN-OUT PLAN, **distinct from the tool dropping keys**, so it is an ERROR, not a tool "
    "FAIL. Add a remainder shard (an empty-prefix run) and designate it with --remainder."
)

DRIFT_NOTE = (
    "reference re-list disagrees with the manifest (full 5-field records: a size/ETag/mtime change "
    "on a shared key counts) — the bucket moved since {snapshot}. **This is not a tool finding.** "
    "Stop; the orchestrator re-baselines."
)

FAIL_NOTE = (
    "reference agrees with the manifest (the bucket did not move): the union has missing/extra "
    "keys, field mismatches, or a shard that fails its own scope, so the discrepancy is **in the "
    "tool or in this mode's `normalize.py` adapter**. Confirm the adapter before any negative "
    "finding."
)


@dataclass
class Shard:
    """One shard, as its own ``run.meta`` describes it."""

    receipt: str
    prefix: str
    mode: str
    payload: Path
    is_remainder: bool


@dataclass
class Options:
    """One ``--scope union`` invocation."""

    receipts: list[str]
    normalize: str
    out: str
    registry: RegistrySource
    security: SecurityBoundary
    remainder: str = ""
    stream: str = ""
    mode: str = ""
    shards: list[Shard] = field(default_factory=list)


def _select_stream(
    options: Options, receipt: str, meta: dict[str, str], run_meta: Path, work: Path, index: int
) -> tuple[Path, str]:
    """Bind both streams, then pick the one carrying the listing.

    A tool that emits its listing on stderr records both streams; the union
    picks the one carrying the listing. Copy FIRST, then hash the copy, then
    check the copy — no window to swap the payload between the hash and the
    read.
    """
    try:
        streams = bind_streams(run_meta, meta)
    except VerifierError:
        raise UnionError(
            f"union shard {receipt} declares an unsupported payload_path_base in run.meta"
        ) from None
    bound: dict[str, tuple[Path, str]] = {}
    for stream, path, sha, truncated in streams:
        if not Path(path).is_file():
            raise UnionError(f"union shard {receipt} {stream} payload not readable: {path}")
        copy = work / f"shard.{index}.{stream}.raw"
        if snapshot(path, copy) != sha:
            raise UnionError(
                f"union shard {receipt} {stream} payload no longer matches the sha256 its "
                "run.meta cites"
            )
        bound[stream] = (copy, truncated)

    # A --stream override pins the stream for ALL shards (fan-out shards share
    # tool+mode, so they share the stream that carries the listing); otherwise
    # the default heuristic prefers the stream that actually carries content,
    # stdout winning ties. The heuristic is a guess — a tool that prints a
    # banner on stdout and its listing on stderr needs the override.
    if options.stream:
        if options.stream not in bound:
            raise UnionError(
                f"union shard {receipt} has no {options.stream} payload but --stream "
                f"{options.stream} was given"
            )
        selected = bound[options.stream]
    else:
        candidates = [
            bound[s] for s in ("stdout", "stderr") if s in bound and bound[s][0].stat().st_size
        ]
        candidates += [bound[s] for s in ("stdout", "stderr") if s in bound]
        if not candidates:
            raise UnionError(
                f"union shard {receipt} has no stdout/stderr payload recorded in run.meta"
            )
        selected = candidates[0]
    if selected[1] == "yes":
        raise UnionError(
            f"union shard {receipt} verified stream was TRUNCATED at the 64 MiB payload cap — "
            "a truncated shard cannot prove completeness; refusing the union verdict"
        )
    return selected


def _read_shards(options: Options, work: Path) -> dict[str, str]:
    """Bind every shard to its run.meta, returning the first shard's shared facts."""
    remainder_real = ""
    if options.remainder:
        remainder = Path(options.remainder)
        if not remainder.is_dir() or not (remainder / "run.meta").is_file():
            raise UnionError(
                f"--remainder is not a receipt directory with a run.meta: {options.remainder}"
            )
        remainder_real = real(options.remainder)
        if all(real(r) != remainder_real for r in options.receipts):
            options.receipts.append(options.remainder)

    # The same receipt named twice double-counts its keys. realpath-dedup so two
    # spellings of one directory are caught. Duplication cannot be bucket drift.
    seen: set[str] = set()
    for receipt in options.receipts:
        resolved = real(receipt)
        if resolved in seen:
            raise UnionError(
                f"union names the same receipt twice ({receipt}) — a duplicated shard "
                "double-counts its keys. That is an invocation-plan defect (ERROR), not a tool "
                "finding."
            )
        seen.add(resolved)

    shared: dict[str, str] = {}
    for index, receipt in enumerate(options.receipts):
        run_meta = Path(receipt) / "run.meta"
        if not run_meta.is_file():
            raise UnionError(
                f"no run.meta in {receipt} — receipts produced outside the wrapper do not count"
            )
        meta = read_meta(run_meta)
        if index == 0:
            shared = {
                key: meta.get(key, "")
                for key in (
                    "tool",
                    "bucket",
                    "auth",
                    "registry_sha256",
                    "manifest_sha256",
                    "manifest",
                    "snapshot_date",
                    "region",
                )
            }
        else:
            for key, complaint in (
                ("tool", "is tool '{got}', not '{want}' — refusing a mixed set"),
                ("bucket", "is bucket '{got}', not '{want}'"),
                ("auth", "is auth '{got}', not '{want}' — refusing to union across auth modes"),
                (
                    "registry_sha256",
                    "cites a different registry digest than the first shard",
                ),
                ("manifest_sha256", "cites a different manifest than the first shard"),
            ):
                if meta.get(key, "") != shared[key]:
                    raise UnionError(
                        f"union shard {receipt} "
                        + complaint.format(got=meta.get(key, ""), want=shared[key])
                    )
        if meta.get("registry_sha256", "") != options.registry.digest:
            raise UnionError(
                f"union shard {receipt} cites registry {meta.get('registry_sha256', '')}, "
                f"current registry is {options.registry.digest} — re-run the shard, or check out "
                "the registry it used"
            )
        # Redaction rewrites bytes; a verdict over scrubbed output is about our
        # scrubber, not the tool.
        redaction = meta.get("redaction_changed_bytes", "")
        if redaction != "no":
            raise UnionError(
                f"union shard {receipt} has redaction_changed_bytes={redaction or '<unset>'} — "
                "refusing to verify redacted bytes (a verdict would judge the scrubber's output, "
                "not the tool's). Orchestrator reviews."
            )

        payload, _ = _select_stream(options, receipt, meta, run_meta, work, index)

        # An empty run.meta prefix is BOTH what a designated remainder records
        # AND what an ordinary full-bucket root run records. Refuse to guess.
        prefix = meta.get("prefix", "")
        is_remainder = bool(remainder_real) and real(receipt) == remainder_real
        if is_remainder:
            if prefix:
                raise UnionError(
                    f"--remainder shard {receipt} has a non-empty run.meta prefix ('{prefix}') — "
                    "the remainder is the UNPREFIXED complement; a prefixed run cannot be it"
                )
        elif not prefix:
            raise UnionError(
                f"union shard {receipt} has an empty run.meta prefix but is not the designated "
                "--remainder — ambiguous: empty prefix is also what a full-bucket run records; "
                "designate the remainder explicitly with --remainder"
            )
        options.shards.append(
            Shard(
                receipt=receipt,
                prefix=prefix,
                mode=meta.get("mode", ""),
                payload=payload,
                is_remainder=is_remainder,
            )
        )
    return shared


def _bind_mode(options: Options) -> str:
    """Bind the mode across the PREFIX shards only; the remainder is exempt."""
    reference: str | None = None
    for shard in options.shards:
        if shard.is_remainder:
            continue
        if reference is None:
            reference = shard.mode
        elif shard.mode != reference:
            raise UnionError(
                f"union prefix shard {shard.receipt} is mode '{shard.mode}', not '{reference}' — "
                "refusing a mixed-mode set (a mode that did not produce a payload cannot certify "
                "it)"
            )
    # `is None` and not `not reference`: a shard whose run.meta records an empty
    # mode would otherwise skip binding, and a mixed-mode set would pass.
    if reference is None:
        reference = options.shards[0].mode
    if options.mode and options.mode != reference:
        raise UnionError(
            f"--mode '{options.mode}' contradicts the prefix shards' run.meta mode "
            f"('{reference}') — refusing to normalize under a mode that did not produce the "
            "payloads"
        )
    return reference


def _check_overlap(prefixes: list[str]) -> None:
    """Overlapping prefixes make a correct tool double-emit the nested keys.

    That then reads as cross-shard duplicates and a FAIL wrongly attributed to the
    tool. Compared over NON-empty prefixes only — the remainder's empty prefix
    is a string-prefix of everything, by design.
    """
    for x, outer in enumerate(prefixes):
        for y, inner in enumerate(prefixes):
            if x != y and inner.startswith(outer):
                raise UnionError(
                    f"union shards overlap: prefix '{outer}' is a string-prefix of '{inner}' — "
                    "a correct tool double-emits nested keys under both. That is an "
                    "invocation-plan defect (ERROR), not a tool finding."
                )


def run(options: Options) -> int:
    """Issue the union verdict. Returns the process exit code."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            return _run(options, Path(tmp))
    except VerifierError as exc:
        # Every union refusal owes the durable artifact, not only the ones
        # raised as UnionError: `common.normalize`, `common.read_manifest` and
        # `common.relist` raise the plain VerifierError, and converting those in
        # a sibling handler left no union-verify.md at all.
        failure = exc if isinstance(exc, UnionError) else UnionError(str(exc))
        out = Path(options.out)
        try:
            out.mkdir(parents=True, exist_ok=True)
            (out / "union-verify.md").write_bytes(report.union_error(generated_utc(), str(failure)))
        except OSError:
            pass
        raise failure from None


def _run(options: Options, work: Path) -> int:
    shared = _read_shards(options, work)
    mode = _bind_mode(options)
    prefixes = [shard.prefix for shard in options.shards if shard.prefix]
    _check_overlap(prefixes)

    manifest_path = shared["manifest"]
    if not Path(manifest_path).is_file():
        raise UnionError(f"manifest not readable: {manifest_path}")
    if snapshot(manifest_path, work / "manifest.gz") != shared["manifest_sha256"]:
        raise UnionError(
            f"manifest digest mismatch — registry {shared['manifest_sha256']} vs file. "
            "Orchestrator re-baselines."
        )
    manifest_bytes = read_manifest(work / "manifest.gz")
    fields = manifest_field_count(manifest_bytes)
    if fields != 5:
        raise UnionError(
            f"manifest has {fields} fields — contract v2 expects 5 "
            "(key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class). This looks like a "
            "pre-2026-07-17 artifact."
        )
    manifest_rows = split_records(manifest_bytes)

    has_remainder = any(not shard.prefix for shard in options.shards)
    encoded = [p.encode("utf-8", errors="surrogateescape") for p in prefixes]
    orphan_rows = [row for row in manifest_rows if not any(row[0].startswith(p) for p in encoded)]
    orphan_keys = {row[0] for row in orphan_rows}
    structural = bool(orphan_rows) and not has_remainder

    union_rows: list[Row] = []
    shard_fail = False
    shard_notes = ""
    for index, shard in enumerate(options.shards):
        try:
            raw = normalize(options.normalize, shard.mode, shard.prefix, shard.payload)
        except VerifierError:
            # The adapter is handed a staged copy, so the underlying refusal
            # names a temp path nobody can act on. Name the shard, as the shell
            # does — the index is what the report and the argv both use.
            raise UnionError(
                f"normalize adapter failed on union shard {index} (mode {shard.mode})"
            ) from None
        rows = split_records(raw)
        if malformed_rows(rows):
            raise UnionError(
                f"union shard {index} adapter output is not 5-field "
                "(key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class) — a 3-field output is a "
                "pre-2026-07-17 artifact"
            )
        union_rows += rows
        if shard.prefix:
            wanted = shard.prefix.encode("utf-8", errors="surrogateescape")
            expected = [row for row in manifest_rows if row[0].startswith(wanted)]
        else:
            expected = [row for row in manifest_rows if row[0] in orphan_keys]
        actual_keys = {row[0] for row in rows}
        expected_keys_set = {row[0] for row in expected}
        shard_missing = len(expected_keys_set - actual_keys)
        shard_extra = len(actual_keys - expected_keys_set)
        if shard_missing or shard_extra:
            shard_fail = True
            shard_notes += (
                f"\n  shard {index} (prefix='{shard.prefix or '<remainder>'}'): "
                f"missing={shard_missing} extra={shard_extra} against its own scope"
            )

    total_records = len(union_rows)
    with staged(union_rows, manifest_rows, work) as sides:
        distinct_keys = sides.distinct("actual")
        expected_keys = sides.distinct("expected")
        dup_keys = sides.duplicated("actual")
        missing = sides.anti_join("expected", "actual")
        extra = sides.anti_join("actual", "expected")
        check_mtime_shape(union_rows, ADAPTER_MTIME.format(adapter=options.normalize, mode=mode))
        check_mtime_shape(manifest_rows, MANIFEST_MTIME)
        mismatches = sides.field_mismatches()
    duplicates = total_records - distinct_keys

    relist_note = ""
    note = ""
    needs_relist = bool(missing or extra or mismatches or shard_fail)
    if structural:
        verdict = "ERROR"
        note = STRUCTURAL_NOTE.format(orphan=len(orphan_rows))
    elif needs_relist:
        say("union discrepancy — re-listing the reference before attributing it to the tool")
        image = options.registry.harness_image()
        if not options.security.image_present(image):
            raise UnionError(
                "digest-pinned harness image is not present locally; campaign execution never pulls"
            )
        if not options.security.preflight(shared["bucket"], shared["region"]):
            raise UnionError("runner security preflight failed; reference re-list was not started")
        rc, reference, stderr = relist(
            options.security, image, shared["bucket"], shared["region"], ""
        )
        if rc != 0:
            verdict = "ERROR"
            relist_note = "reference re-list FAILED"
            note = (
                f"reference re-list {docker_status(rc)}; cannot attribute the union discrepancy. "
                f"{head(stderr, 2)}"
            )
        elif sorted(b"\t".join(row) for row in manifest_rows) != sorted(reference):
            verdict = "DRIFT"
            relist_note = "reference re-list DISAGREES with the manifest (bucket moved)"
            note = DRIFT_NOTE.format(snapshot=shared["snapshot_date"])
        else:
            verdict = "FAIL"
            relist_note = "reference re-list AGREES with the manifest (bucket did not move)"
            note = FAIL_NOTE + (f" Shard-level:{shard_notes}" if shard_notes else "")
    elif duplicates > 0:
        # Duplication alone — no re-list. Duplication cannot be bucket drift.
        verdict = "FAIL"
        note = (
            f"cross-shard duplicates ({duplicates}) with no missing/extra/field discrepancy — "
            "attributable to the tool or this mode's `normalize.py` adapter, not bucket drift "
            "(duplication cannot be drift)."
        )
    else:
        verdict = "PASS"

    out = Path(options.out)
    out.mkdir(parents=True, exist_ok=True)
    union_md = out / "union-verify.md"
    union_md.write_bytes(
        report.union(
            tool=shared["tool"],
            mode=mode,
            verdict=verdict,
            generated=generated_utc(),
            registry_digest=options.registry.digest,
            bucket=shared["bucket"],
            region=shared["region"],
            auth=shared["auth"],
            manifest_sha256=shared["manifest_sha256"],
            snapshot_date=shared["snapshot_date"],
            relist_note=relist_note,
            note=note,
            shards=[
                (index, shard.prefix or "<remainder>", shard.receipt)
                for index, shard in enumerate(options.shards)
            ],
            prefix_shards=len(prefixes),
            has_remainder=has_remainder,
            orphan=len(orphan_rows),
            structural=structural,
            expected_keys=expected_keys,
            total_records=total_records,
            distinct_keys=distinct_keys,
            duplicates=duplicates,
            missing=missing,
            extra=extra,
            dups=dup_keys,
            field_mismatches=mismatches,
        )
    )
    say(
        f"[union/{shared['tool']}] {verdict} — shards={len(options.shards)} "
        f"expected={expected_keys} distinct={distinct_keys} dups={duplicates} "
        f"missing={len(missing)} extra={len(extra)} fields={len(mismatches)} "
        f"root_uncovered={len(orphan_rows)} structural={int(structural)} → {union_md}"
    )
    return {"PASS": 0, "FAIL": 1, "DRIFT": 4}.get(verdict, 3)
