"""The single-receipt verdict: PASS, FAIL, DRIFT or ERROR for one run.

``run.meta`` is the sole source of truth. Without it, tool/mode/bucket/inputs
are independent claims: one mode's output can be checked against another mode's
scope and stamped into a third mode's receipt, and every artifact still looks
consistent.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import report
from .common import (
    bind_streams,
    comm_split,
    head,
    manifest_field_count,
    normalize,
    read_manifest,
    relist,
    say,
    sha256_file,
    snapshot,
)
from .compare import Row, bad_mtimes, malformed_rows, split_records, staged
from .errors import VerifierError
from .meta import read_meta, real
from .registry_source import RegistrySource
from .security import SecurityBoundary, docker_status

_UNEXPOSED = b"-"

Bound = dict[str, tuple[str, str]]
"""Resolved payload path to (sha256, truncated) — every stream the receipts cite."""

ADAPTER_MTIME = (
    "{adapter} (mode {mode}) emitted a non-contract-v2 mtime — must be YYYY-MM-DDTHH:MM:SS "
    "with Z/+00:00/+0000, never a bare or timezone-less string. This is an adapter-contract "
    "violation, not a tool FAIL and not a PASS."
)

MANIFEST_MTIME = (
    "manifest mtime is not contract-v2 form (YYYY-MM-DDTHH:MM:SS with Z/+00:00/+0000) — "
    "the snapshot is corrupt, not a tool finding; orchestrator re-baselines."
)

FAIL_NOTE = (
    "reference re-list agrees with the manifest for this scope, so the bucket did not move. "
    "The discrepancy is therefore **in the tool or in this mode's `normalize.sh` adapter** — "
    "this verdict does not distinguish them. Before recording any negative finding about the "
    "tool, confirm the adapter faithfully represents the raw output (methodology: negative "
    "findings ship with exact invocation and raw output, or they don't ship)."
)


@dataclass
class Options:
    """One invocation of the single-receipt path."""

    receipt: str
    receipts: list[str]
    inputs: list[str]
    normalize: str
    registry: RegistrySource
    security: SecurityBoundary
    scope_kind: str
    scope_prefix: str = ""
    scope_delimiter: str = ""
    tool: str = ""
    mode: str = ""
    bucket: str = ""


def check_mtime_shape(rows: list[Row], complaint: str) -> None:
    """Both sides, every row, before dedup or join. Two different refusals.

    ``complaint`` is the whole difference between them: the adapter emitting a
    malformed mtime is an adapter-contract violation, the manifest carrying one
    is a corrupt snapshot, and the two must never be reported as each other.
    """
    bad = bad_mtimes(rows)
    if bad:
        offending = head(b"".join(line + b"\n" for line in bad), 3)
        raise VerifierError(f"{complaint} Offending (key<TAB>mtime), first 3:\n{offending}")


def expected_for_scope(manifest: list[Row], kind: str, prefix: str, delimiter: str) -> list[Row]:
    """The expected set, derived from the manifest — never from a second listing.

    Deriving the expectation from a fresh listing would compare the tool against
    whatever the bucket happens to be doing right now.
    """
    if kind == "full":
        return manifest
    key_prefix = prefix.encode("utf-8", errors="surrogateescape")
    if kind == "prefix":
        return [row for row in manifest if row[0].startswith(key_prefix)]

    # S3 delimiter semantics, derived: a key under PFX whose remainder contains
    # DELIM collapses into a CommonPrefix (through the first DELIM inclusive);
    # every other key is returned whole. CommonPrefixes carry no size/etag/mtime/
    # storage_class, so they normalise to `-`.
    delim = delimiter.encode("utf-8", errors="surrogateescape")
    rows: list[Row] = []
    common: dict[bytes, None] = {}
    for row in manifest:
        if not row[0].startswith(key_prefix):
            continue
        rest = row[0][len(key_prefix) :]
        cut = rest.find(delim)
        if cut >= 0:
            common[key_prefix + rest[: cut + len(delim)]] = None
        else:
            rows.append(row)
    rows += [[key, _UNEXPOSED, _UNEXPOSED, _UNEXPOSED, _UNEXPOSED] for key in common]
    return rows


def _bind_receipts(options: Options, meta: dict[str, str]) -> Bound:
    """Every input must BE a payload one of these receipts produced.

    Matched by resolved path AND content. Hash-checking only an input whose path
    string equalled ``stdout_path`` let any other readable file — including a
    different spelling of the same path — sail through unchecked: a failed run
    could be verified against an unrelated complete listing and stamped PASS.
    """
    bound: Bound = {}
    for receipt in options.receipts:
        run_meta = Path(receipt) / "run.meta"
        if not run_meta.is_file():
            raise VerifierError(
                f"no run.meta in {receipt} — receipts produced outside the wrapper do not count"
            )
        other = read_meta(run_meta)
        if (
            other.get("tool", "") != meta.get("tool", "")
            or other.get("mode", "") != meta.get("mode", "")
            or other.get("bucket", "") != meta.get("bucket", "")
        ):
            raise VerifierError(
                f"receipt {receipt} is for {other.get('tool', '')}/{other.get('mode', '')}/"
                f"{other.get('bucket', '')}, not {meta.get('tool', '')}/{meta.get('mode', '')}/"
                f"{meta.get('bucket', '')} — refusing to verify a mixed set"
            )
        if other.get("registry_sha256", "") != meta.get("registry_sha256", "") or other.get(
            "manifest_sha256", ""
        ) != meta.get("manifest_sha256", ""):
            raise VerifierError(
                f"receipt {receipt} cites a different registry/manifest than {options.receipt} "
                "— refusing to verify against two references"
            )
        if other.get("redaction_changed_bytes", "") != "no":
            raise VerifierError(
                f"receipt {receipt} has redaction_changed_bytes="
                f"{other.get('redaction_changed_bytes', '')} — see {options.receipt} note"
            )
        try:
            streams = bind_streams(run_meta, other)
        except VerifierError:
            raise VerifierError(
                f"receipt {receipt} declares an unsupported payload_path_base in run.meta"
            ) from None
        for _stream, path, sha, truncated in streams:
            bound[real(path)] = (sha, truncated)
    return bound


def _check_inputs(options: Options, bound: Bound) -> None:
    for path in options.inputs:
        candidate = Path(path)
        if not candidate.is_file():
            raise VerifierError(f"input not readable: {path}")
        want, truncated = bound.get(real(path), ("", "no"))
        if not want:
            raise VerifierError(
                f"input '{path}' is not a payload recorded by any --receipt given.\n"
                "        The verifier only judges bytes the wrapper produced. For a fan-out mode,"
                " pass every\n        invocation's --receipt alongside its --input."
            )
        if sha256_file(candidate) != want:
            raise VerifierError(
                f"input {path} no longer matches the sha256 its receipt cites ({want}) "
                "— the evidence changed under us"
            )
        # A truncated stream cannot certify completeness. Scoped to the STREAM
        # being verified: truncation of stderr alone does not block verifying a
        # complete stdout listing.
        if truncated == "yes":
            raise VerifierError(
                f"input {path} is from a payload stream the wrapper TRUNCATED at the 64 MiB cap"
                " (contract v2).\n        A truncated listing cannot prove it listed everything —"
                " refusing to issue a completeness verdict.\n        (Truncation of stderr alone"
                " does not block verifying a complete stdout listing.)"
            )


def run(options: Options) -> int:
    """Issue the verdict for one receipt. Returns the process exit code."""
    receipt = Path(options.receipt)
    meta_path = receipt / "run.meta"
    if not meta_path.is_file():
        raise VerifierError(
            f"no run.meta in {options.receipt} — this receipt was not produced by "
            "harness/smoke-run.sh, and receipts produced outside the wrapper do not count"
        )
    meta = read_meta(meta_path)
    m_tool, m_mode, m_bucket = meta.get("tool", ""), meta.get("mode", ""), meta.get("bucket", "")

    for name, given, actual in (
        ("tool", options.tool, m_tool),
        ("mode", options.mode, m_mode),
        ("bucket", options.bucket, m_bucket),
    ):
        if given and given != actual:
            raise VerifierError(f"--{name} '{given}' contradicts run.meta ('{actual}')")
    options.tool, options.mode, options.bucket = m_tool, m_mode, m_bucket

    bound = _bind_receipts(options, meta)
    _check_inputs(options, bound)

    # Redaction rewrites bytes. If it changed anything, the verifier is judging
    # our scrubber's output, not the tool's — a legitimate key shaped like a
    # credential would be altered and then failed against the manifest.
    if meta.get("redaction_changed_bytes", "") != "no":
        raise VerifierError(
            "run.meta says redaction altered the payload bytes. Refusing to verify: a verdict here"
            "\n        would be about the redacted text, not about what the tool emitted."
            " Orchestrator must review."
        )
    if meta.get("timed_out", "") == "1":
        say(
            f"note: the run timed out (exit {meta.get('exit_code', '')}) — its output is by "
            "definition partial, and a completeness FAIL is expected rather than a tool defect"
        )

    # Manifest identity comes from run.meta — the registry the RUN used — not
    # from a fresh lookup. Running under one registry and verifying under
    # another would compare the run against a snapshot it never saw, while
    # receipt.md and verify.md cite different manifests and both look
    # authoritative.
    registry_sha = meta.get("registry_sha256", "")
    if not meta.get("manifest", "") or not meta.get("manifest_sha256", ""):
        raise VerifierError("run.meta lacks manifest identity — not a wrapper receipt")
    if options.registry.digest != registry_sha:
        raise VerifierError(
            f"the registry changed since this run: receipt cites sha256 {registry_sha}, current "
            f"registry is {options.registry.digest}.\n        Verifying now would judge the run "
            "against a registry it never saw. Re-run the mode, or\n        check out the registry"
            " the run used."
        )
    harness_image = options.registry.harness_image()

    # Refuse a re-verify BEFORE writing anything. Checking this *after*
    # verify.md had been overwritten left receipt.md saying PASS and verify.md
    # saying FAIL: precisely the inconsistency the guard exists to prevent,
    # created by the guard.
    receipt_md = receipt / "receipt.md"
    if not receipt_md.is_file():
        raise VerifierError(f"no receipt.md in {options.receipt} to stamp")
    stamped, hits = report.stamp(receipt_md.read_text(), "PASS")
    del stamped
    if hits == 0:
        raise VerifierError(
            f"receipt {receipt_md} carries no verdict placeholder — it was already verified.\n"
            "        Nothing has been written. Re-run the mode to produce a fresh receipt rather"
            " than\n        restamping this one."
        )
    if hits > 1:
        raise VerifierError(
            f"receipt has {hits} verdict placeholders — refusing to guess which is the verdict"
        )

    with tempfile.TemporaryDirectory() as tmp:
        return _verdict(options, Path(tmp), receipt, meta, bound, harness_image)


def _verdict(
    options: Options,
    work: Path,
    receipt: Path,
    meta: dict[str, str],
    bound: Bound,
    harness_image: str,
) -> int:
    """``run.meta`` is passed whole: every one of these is a field of it."""
    manifest = meta.get("manifest", "")
    manifest_sha = meta.get("manifest_sha256", "")
    snapshot_date = meta.get("snapshot_date", "")
    meta_prefix = meta.get("prefix", "")
    if not Path(manifest).is_file():
        raise VerifierError(f"manifest not readable: {manifest}")
    actual_sha = snapshot(manifest, work / "manifest.gz")
    if actual_sha != manifest_sha:
        raise VerifierError(
            f"manifest digest mismatch — registry {manifest_sha}, file {actual_sha}. "
            "Orchestrator re-baselines."
        )
    manifest_bytes = read_manifest(work / "manifest.gz")
    fields = manifest_field_count(manifest_bytes)
    if fields != 5:
        raise VerifierError(
            f"manifest has {fields} field(s) — contract v2 expects 5 "
            "(key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class).\n        This looks like a "
            "pre-2026-07-17 artifact; re-baseline with the pinned client before verifying."
        )
    manifest_rows = split_records(manifest_bytes)

    # Snapshot the inputs as well, and re-verify the hash against the snapshot:
    # hashing an input and reopening it later lets a concurrent rerun or a
    # symlink retarget swap the bytes being judged for bytes nobody checked.
    snapshots = []
    for index, path in enumerate(options.inputs):
        destination = work / f"input.{index}"
        if snapshot(path, destination) != bound[real(path)][0]:
            raise VerifierError(
                f"input {path} changed while being snapshotted — refusing to judge unstable"
                " evidence"
            )
        snapshots.append(destination)

    scope = options.scope_kind
    if options.scope_kind == "full":
        if options.scope_prefix or options.scope_delimiter:
            raise VerifierError("scope 'full' takes no prefix or delimiter")
    elif options.scope_kind == "prefix":
        if not options.scope_prefix:
            raise VerifierError("scope 'prefix' needs --scope-prefix")
        scope = f"prefix={options.scope_prefix}"
    elif options.scope_kind == "delimiter":
        if not options.scope_delimiter:
            raise VerifierError("scope 'delimiter' needs --scope-delimiter")
        scope = f"delimiter={options.scope_delimiter} prefix={options.scope_prefix or '<none>'}"
    else:
        raise VerifierError(f"unknown scope '{options.scope_kind}' (full | prefix | delimiter)")
    expected_rows = expected_for_scope(
        manifest_rows, options.scope_kind, options.scope_prefix, options.scope_delimiter
    )
    if not expected_rows:
        raise VerifierError(
            f"expected set for scope '{scope}' is empty — the scope is wrong, or the manifest is"
        )

    # The wrapper recorded which prefix the tool was actually pointed at.
    # Guarded in BOTH directions: checking only "meta prefix nonempty and
    # differs" let a full-bucket run be verified as prefix-scoped, and the tool
    # ate a FAIL for listing exactly what it was asked to list.
    if options.scope_kind == "prefix" and meta_prefix != options.scope_prefix:
        raise VerifierError(
            f"--scope-prefix '{options.scope_prefix}' contradicts the prefix the run actually "
            f"used ('{meta_prefix or '<none: full-bucket run>'}')"
        )
    if options.scope_kind == "full" and meta_prefix:
        raise VerifierError(
            f"scope 'full' but the run was scoped to prefix '{meta_prefix}' — verifying a prefix "
            "run against the whole manifest would fabricate missing keys"
        )
    if options.scope_kind == "delimiter" and meta_prefix and meta_prefix != options.scope_prefix:
        raise VerifierError(
            f"--scope-prefix '{options.scope_prefix or '<none>'}' contradicts the run's prefix "
            f"('{meta_prefix}')"
        )

    # Fan-out modes emit several outputs; they concatenate as a MULTISET.
    raw = b"".join(
        normalize(options.normalize, options.mode, meta_prefix, path) for path in snapshots
    )
    if not raw:
        raise VerifierError("normalized output is empty — adapter bug, or the tool emitted nothing")
    actual_rows = split_records(raw)
    bad = malformed_rows(actual_rows)
    if bad:
        hint = ""
        if len(actual_rows[0]) == 3:
            hint = (
                " — 3 fields is a pre-2026-07-17 adapter; contract v2 expects "
                "key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class (`-` for any field the mode "
                "does not expose)"
            )
        raise VerifierError(
            "normalize adapter emitted rows that are not 5-field "
            f"key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class{hint}:\n" + "\n".join(bad[:3])
        )

    total_records = len(actual_rows)
    rows_with = [sum(1 for row in actual_rows if row[i] != _UNEXPOSED) for i in range(1, 5)]
    rows_without = sum(1 for row in actual_rows if all(f == _UNEXPOSED for f in row[1:5]))

    with staged(actual_rows, expected_rows, work) as sides:
        distinct_keys = sides.distinct("actual")
        expected_keys = sides.distinct("expected")
        dup_keys = sides.duplicated("actual")
        missing = sides.anti_join("expected", "actual")
        extra = sides.anti_join("actual", "expected")
        if sum(rows_with) == 0:
            fields_checked = (
                "keys only — this mode exposed none of size/etag/mtime/storage_class on any of "
                f"{total_records} rows"
            )
            mismatches: list[bytes] = []
        else:
            check_mtime_shape(
                actual_rows,
                ADAPTER_MTIME.format(adapter=options.normalize, mode=options.mode or "?"),
            )
            check_mtime_shape(expected_rows, MANIFEST_MTIME)
            mismatches = sides.field_mismatches()
            fields_checked = (
                f"size {rows_with[0]}/{total_records}; etag {rows_with[1]}/{total_records}; "
                f"mtime {rows_with[2]}/{total_records}; storage_class {rows_with[3]}/"
                f"{total_records} rows compared (by policy)"
            )
            if rows_without > 0:
                fields_checked += (
                    f"; {rows_without} row(s) exposed none and were checked on key only"
                )
    dup_count = total_records - distinct_keys

    verdict = ""
    drift_note = ""
    discrepancy = bool(missing or extra or dup_count > 0 or mismatches)
    if discrepancy:
        say("discrepancy found — re-listing the reference before blaming the tool")
        if not options.security.image_present(harness_image):
            raise VerifierError(
                "digest-pinned harness image is not present locally; campaign execution never pulls"
            )
        if not options.security.preflight(options.bucket, meta.get("region", "")):
            raise VerifierError(
                "runner security preflight failed; reference re-list was not started"
            )
        rc, reference, stderr = relist(
            options.security,
            harness_image,
            options.bucket,
            meta.get("region", ""),
            options.scope_prefix,
        )
        if rc != 0:
            verdict = "ERROR"
            drift_note = (
                f"reference re-list {docker_status(rc)}; cannot attribute the discrepancy. "
                f"{head(stderr, 2)}"
            )
        else:
            scoped = sorted(
                b"\t".join(row)
                for row in manifest_rows
                if row[0].startswith(options.scope_prefix.encode("utf-8", errors="surrogateescape"))
            )
            observed = sorted(reference)
            if scoped != observed:
                ref_only, man_only, _ = comm_split(observed, scoped)
                changed = len(
                    {row.split(b"\t", 1)[0] for row in scoped}
                    & {row.split(b"\t", 1)[0] for row in observed}
                )
                verdict = "DRIFT"
                drift_note = (
                    "reference re-list disagrees with the manifest for this scope: "
                    f"{ref_only} record(s) present now but not in the snapshot, {man_only} in the "
                    f"snapshot but not now ({changed} keys common to both — a size/ETag/mtime "
                    "change on a shared key counts here). The bucket moved since "
                    f"{snapshot_date}. **This is not a tool finding.** Stop; the orchestrator "
                    "re-baselines (single manifest owner)."
                )
    if not verdict:
        if discrepancy:
            verdict = "FAIL"
            drift_note = FAIL_NOTE
        else:
            verdict = "PASS"

    (receipt / "verify.md").write_bytes(
        report.single(
            tool=options.tool,
            mode=options.mode,
            verdict=verdict,
            drift_note=drift_note,
            scope=scope,
            fields_checked=fields_checked,
            registry_path=meta.get("registry_path", ""),
            registry_sha256=meta.get("registry_sha256", ""),
            manifest_sha256=manifest_sha,
            snapshot_date=snapshot_date,
            expected_keys=expected_keys,
            total_records=total_records,
            distinct_keys=distinct_keys,
            duplicates=dup_count,
            missing=missing,
            extra=extra,
            dups=dup_keys,
            field_mismatches=mismatches,
            inputs=len(options.inputs),
        )
    )

    receipt_md = receipt / "receipt.md"
    text, hits = report.stamp(receipt_md.read_text(), verdict)
    if hits != 1:
        raise VerifierError(f"placeholder count changed under us ({hits}) — refusing to stamp")
    receipt_md.write_text(text)
    if report.PLACEHOLDER in receipt_md.read_text():
        raise VerifierError("verdict stamp did not take — placeholder survives in receipt.md")

    say(
        f"[{options.tool}/{options.mode}] {verdict} — expected={expected_keys} "
        f"distinct={distinct_keys} dups={dup_count} missing={len(missing)} extra={len(extra)} "
        f"fields={len(mismatches)}"
    )
    return {"PASS": 0, "FAIL": 1, "DRIFT": 4}.get(verdict, 3)
