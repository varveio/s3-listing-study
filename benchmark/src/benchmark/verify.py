"""Verify one group of a campaign: bind its evidence to the ledger, then compare.

The roster is the group, read from the recorded rows -- never a re-resolved
plan. Every attempt that went out is a row carrying its `case_id`,
`result_prefix` and settled `state`, which is what completeness needs; a
`BLOCKED` slot is a measurement the launch intended and has not got, and an
`ABANDONED` slot is an absent subject someone declared final. Either one makes
the group incomplete, so a comparison is never reported as passing with one
fewer tool in it (`docs/model.md` § *What verify binds against*).

A comparison is scoped to one target bucket -- comparing listings of different
corpora is not a comparison -- and within a bucket to one stratum, `(product,
fields)` resolved from the capsule's own mode manifest. A text listing is not
compared against a Parquet dataset, and a key-only mode is not ranked against
one emitting five fields, or a tool wins by emitting less.

A real-S3 PASS means the subjects of a stratum AGREE: there is no sealed
manifest, and agreement stands in for control over a corpus that grows
underneath the study (`docs/identity.md` § *What identity cannot cover*). A
replay PASS instead means that one subject independently matches the immutable
reference manifest bound into its ledger row; agreement between replay subjects
is never an oracle.

Two decisions this module makes where the docs are silent:

- A real-S3 `statistic: rate` case is summarized as successes over attempts and
  takes no part in cross-tool agreement. Replay counts a successful attempt only
  after it matches the bound oracle; terminal failures remain denominator data.
- A stratum's reference is its lowest `attempt_id`. Agreement is symmetric, so
  the choice only fixes which side a diff is written from.

Comparison uses plain UTF-8 text in DuckDB. Non-UTF-8 keys are outside this
benchmark's declared corpus and verifier scope; capsule framing itself remains
byte-based in :mod:`benchmark.runtime.contract`.

It refuses rather than guesses, and a refusal is a gap in the group rather than
a verdict: evidence with no `result.json`; evidence whose recorded identity
disagrees with the prefix it was found under; a subject that failed or timed out
(a failed attempt is not a listing finding); a capsule `normalize.py` that
exited nonzero; and a row with a NULL field in a normalized TSV.

Verdict, per stratum and worst-wins for the group:
    PASS       -- no missing keys, no extra keys, no duplicates, no mismatches.
    DRIFT      -- the only field mismatches are mtime (a moving target).
    FAIL       -- anything else.
    UNCOMPARED -- one subject in the stratum; nothing to agree with.
    INCOMPLETE -- the roster owes a subject, or evidence was refused.

Usage:
    verify.py --state campaign.db --group g20260817-120000
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from benchmark import adapters, gcs
from benchmark import replay as replay_contract
from benchmark.contract import (
    EXIT_BINDING_MISMATCH,
    EXIT_DRIFT,
    EXIT_FAIL,
    EXIT_FAILED_SUBJECT,
    EXIT_INCOMPLETE_GROUP,
    EXIT_MALFORMED_INPUT,
    EXIT_MISSING_MARKER,
    EXIT_NORMALIZE_FAILED,
    EXIT_PASS,
    sha256_of,
)
from benchmark.ledger import (
    STATE_FILENAME,
    TERMINAL_STATES,
    attempt_rows,
    open_ledger,
    pending_rows,
    producer_summary,
)
from benchmark.replay_verify import (
    ManifestError,
    replay_manifest_identity,
    stage_replay_manifest,
    validate_replay_evidence,
)
from benchmark.runtime.command_adapter import Mode

_COLUMNS = (
    "{'key':'VARCHAR','size':'VARCHAR','etag':'VARCHAR',"
    "'mtime':'VARCHAR','storage_class':'VARCHAR'}"
)
# Disabling quote interpretation entirely: the contract TSV has no quoting
# dialect of its own -- a key or etag containing a literal '"' is an ordinary
# character, never the start of a quoted field. Without this, DuckDB's
# default CSV quoting could reinterpret such a field and misplace columns,
# an adapter-honest row read back as something it never was.
_READ_CSV_OPTS = "quote='', escape=''"
MISMATCH_FIELDS = ("size", "etag", "mtime", "storage_class")
SAMPLE_LIMIT = 5

# Group-level rungs on contract.py's refusal ladder, which is per comparison.
VERDICT_ORDER = ("UNCOMPARED", "PASS", "DRIFT", "FAIL")
GROUP_EXIT_CODES = {
    "UNCOMPARED": EXIT_PASS,
    "PASS": EXIT_PASS,
    "DRIFT": EXIT_DRIFT,
    "FAIL": EXIT_FAIL,
    "INCOMPLETE": EXIT_INCOMPLETE_GROUP,
}
# Which refusal each gap reports as, so a caller can act on the reason without
# parsing the message.
GAP_EXIT_CODES = {
    "unsettled": EXIT_INCOMPLETE_GROUP,
    "absent": EXIT_INCOMPLETE_GROUP,
    "missing-evidence": EXIT_MISSING_MARKER,
    "identity": EXIT_BINDING_MISMATCH,
    "failed-subject": EXIT_FAILED_SUBJECT,
    "normalize": EXIT_NORMALIZE_FAILED,
    "malformed": EXIT_MALFORMED_INPUT,
    "manifest": EXIT_BINDING_MISMATCH,
    "replay-evidence": EXIT_MALFORMED_INPUT,
    "uncalibrated-capacity": EXIT_INCOMPLETE_GROUP,
    "mixed-backend": EXIT_BINDING_MISMATCH,
}


class MalformedInputError(Exception):
    """A normalized TSV has a NULL field -- an anti-join over it would be
    NULL-blind and silently under-report every discrepancy list.
    """


@dataclass(frozen=True)
class Subject:
    """One attempt of the roster, as the ledger recorded it."""

    attempt_id: str
    case_id: str
    tool: str
    mode: str
    purpose: str
    statistic: str
    state: str
    target_bucket: str
    target_prefix: str
    result_prefix: str
    config: dict[str, object]
    replay: replay_contract.ReplayConfig | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Subject:
        try:
            replay = (
                None
                if row["replay"] is None
                else replay_contract.parse_document(str(row["replay"]))
            )
        except replay_contract.ReplayError as exc:
            raise ValueError(f"{row['attempt_id']} replay document is invalid: {exc}") from exc
        return cls(
            attempt_id=row["attempt_id"],
            case_id=row["case_id"],
            tool=row["tool"],
            mode=row["mode"],
            purpose=row["purpose"],
            statistic=row["statistic"],
            state=row["state"],
            target_bucket=row["target_bucket"],
            target_prefix=row["target_prefix"],
            result_prefix=row["result_prefix"],
            config=json.loads(row["config"]),
            replay=replay,
        )


@dataclass(frozen=True)
class Roster:
    """A group's comparative attempts, replay diagnostics, and owed slots."""

    subjects: tuple[Subject, ...]
    diagnostics: tuple[Subject, ...]
    blocked: tuple[str, ...]
    abandoned: tuple[str, ...]


@dataclass(frozen=True)
class Prepared:
    """A subject whose evidence is bound, normalized, and ready to compare."""

    subject: Subject
    result_sha256: str
    tsv: Path
    product: str
    fields: tuple[str, ...]
    replay_diagnostics: dict[str, object] | None = None


def roster_for(con: sqlite3.Connection, group_id: str) -> Roster:
    """Read comparison population and separately-verifiable replay diagnostics.

    A diagnostic or canary gives evidence about the replay path, never a
    comparative timing or rate.  It remains in the roster here only so its
    identity, product, replay observations, and manifest binding can be
    verified and recorded under its own prefix.
    """
    attempts = tuple(Subject.from_row(row) for row in attempt_rows(con, group_id=group_id))
    subjects = tuple(subject for subject in attempts if subject.purpose == "measurement")
    diagnostics = tuple(
        subject
        for subject in attempts
        if subject.purpose in {"canary", "diagnostic"} and subject.replay is not None
    )
    slots = pending_rows(con, group_id=group_id)
    return Roster(
        subjects=subjects,
        diagnostics=diagnostics,
        blocked=tuple(_slot_label(row) for row in slots if row["state"] == "BLOCKED"),
        abandoned=tuple(_slot_label(row) for row in slots if row["state"] == "ABANDONED"),
    )


def _slot_label(row: sqlite3.Row) -> str:
    owed = row["awaiting"] or producer_summary(str(row["producer"]))
    return f"slot {row['slot']} ({row['tool']}) awaiting {owed}"


def read_bytes_at(prefix: str, name: str) -> bytes:
    if prefix.startswith("gs://"):
        return gcs.download_bytes(prefix.rstrip("/") + "/" + name)
    return (Path(prefix) / name).read_bytes()


def has_result_marker(prefix: str) -> bool:
    """Evidence is only complete once result.json lands -- see measure.py's
    upload(), which writes it last.
    """
    if prefix.startswith("gs://"):
        return gcs.blob_exists(prefix.rstrip("/") + "/result.json")
    return (Path(prefix) / "result.json").exists()


def identity_errors(
    result: Mapping[str, object], *, attempt_id: str, case_id: str, result_prefix: str
) -> list[str]:
    """Where evidence's recorded identity disagrees with the row or its prefix.

    The prefix is deterministic and its last segment is the attempt_id, so
    evidence found somewhere else -- or naming another case -- is the wrong
    evidence for this row, not a tool finding.
    """
    leaf = result_prefix.rstrip("/").rsplit("/", 1)[-1]
    errors = [
        f"{field}: evidence={result.get(field)!r} row={value!r}"
        for field, value in (("attempt_id", attempt_id), ("case_id", case_id))
        if result.get(field) != value
    ]
    if leaf != attempt_id:
        errors.append(f"prefix: {result_prefix} is not the prefix of {attempt_id}")
    return errors


def check_failed_subject(result: Mapping[str, object]) -> str | None:
    """A message if the evidence's own subject failed or timed out, else None.

    A failed or truncated run has nothing to say about listing agreement --
    refusing here is what keeps a subject crash from reading as a diff.
    """
    if result.get("timed_out") is not False:
        return "subject timed out"
    if isinstance(result.get("exit_code"), bool) or result.get("exit_code") != 0:
        return f"subject exited {result.get('exit_code')}"
    execution = result.get("execution")
    if not isinstance(execution, dict):
        return "subject execution evidence is missing"
    if execution.get("timed_out") is not False:
        return "subject execution timeout evidence is invalid"
    if execution.get("subreaper_enabled") is not True:
        return "subject descendant supervision was not enabled"
    if execution.get("process_tree_clean") is not True:
        return "subject left a live descendant after its main process exited"
    if execution.get("process_group_empty") is not True:
        return "subject process group was not empty"
    if execution.get("descendants_empty") is not True:
        return "subject descendants were not empty"
    cgroup = execution.get("cgroup")
    if not isinstance(cgroup, dict) or "oom_kill_delta" not in cgroup:
        return "subject cgroup OOM evidence is missing"
    oom_kill_delta = cgroup.get("oom_kill_delta")
    if oom_kill_delta is not None and (
        isinstance(oom_kill_delta, bool)
        or not isinstance(oom_kill_delta, int)
        or oom_kill_delta < 0
    ):
        return "subject cgroup OOM evidence is invalid"
    if isinstance(oom_kill_delta, int) and oom_kill_delta > 0:
        return f"subject cgroup recorded {oom_kill_delta} OOM kill(s)"
    return None


def load_tables(con: duckdb.DuckDBPyConnection, reference_tsv: Path, actual_tsv: Path) -> None:
    for name, path in (("reference", reference_tsv), ("actual", actual_tsv)):
        if path.stat().st_size == 0:
            con.execute(
                f"CREATE TABLE {name} (key VARCHAR, size VARCHAR, etag VARCHAR, "
                "mtime VARCHAR, storage_class VARCHAR)"
            )
            continue
        con.execute(
            f"CREATE TABLE {name} AS SELECT * FROM "
            f"read_csv(?, delim='\t', header=false, columns={_COLUMNS}, {_READ_CSV_OPTS})",
            [str(path)],
        )


def assert_no_null_fields(con: duckdb.DuckDBPyConnection, table: str) -> None:
    """Refuse rather than risk a NULL-blind `NOT IN`/anti-join false PASS.

    A malformed row -- any of the five columns NULL -- must be a refusal,
    never silently swallowed into an empty discrepancy list.
    """
    row = con.execute(
        f"SELECT count(*) FROM {table} WHERE key IS NULL OR size IS NULL OR etag IS NULL "
        "OR mtime IS NULL OR storage_class IS NULL"
    ).fetchone()
    if row is None:
        raise MalformedInputError(f"{table}: NULL-field query returned no row")
    bad = row[0]
    if bad:
        raise MalformedInputError(f"{table}: {bad} row(s) have a NULL field")


def compute_diff(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    # NOT EXISTS, not NOT IN: NOT IN is NULL-blind (a single NULL on the
    # right-hand side empties the whole anti-join), NOT EXISTS is not.
    # assert_no_null_fields() already refuses a NULL field before this runs,
    # so this is belt-and-suspenders against the same failure mode.
    missing = con.execute(
        "SELECT key FROM reference r WHERE NOT EXISTS "
        "(SELECT 1 FROM actual a WHERE a.key = r.key) ORDER BY key"
    ).fetchall()
    extra = con.execute(
        "SELECT key FROM actual a WHERE NOT EXISTS "
        "(SELECT 1 FROM reference r WHERE r.key = a.key) ORDER BY key"
    ).fetchall()
    actual_duplicates = con.execute(
        "SELECT key FROM actual GROUP BY key HAVING count(*) > 1 ORDER BY key"
    ).fetchall()
    reference_duplicates = con.execute(
        "SELECT key FROM reference GROUP BY key HAVING count(*) > 1 ORDER BY key"
    ).fetchall()

    # Deduplicate before the join so a duplicate key does not multiply its own mismatches.
    con.execute("CREATE TABLE actual_u AS SELECT DISTINCT ON (key) * FROM actual ORDER BY key")

    subqueries = []
    for order, field in enumerate(MISMATCH_FIELDS, start=1):
        # ETag compares case-insensitively -- it's a hex digest, and casing
        # differs harmlessly across tools/SDKs; every other field is exact.
        compare = (
            f"lower(a.{field}) <> lower(r.{field})"
            if field == "etag"
            else f"a.{field} <> r.{field}"
        )
        subqueries.append(
            f"SELECT a.key, {order} AS ord, '{field}' AS field, a.{field} AS tool_value, "
            f"r.{field} AS reference_value FROM actual_u a JOIN reference r USING (key) "
            f"WHERE a.{field} <> '-' AND r.{field} <> '-' AND {compare}"
        )
    mismatches = con.execute(
        "SELECT key, field, tool_value, reference_value FROM ("
        + " UNION ALL ".join(subqueries)
        + ") ORDER BY key, ord"
    ).fetchall()

    return {
        "missing": [row[0] for row in missing],
        "extra": [row[0] for row in extra],
        "duplicates": [row[0] for row in actual_duplicates],
        "reference_duplicates": [row[0] for row in reference_duplicates],
        "mismatches": [
            {"key": key, "field": field, "tool": tool_value, "reference": reference_value}
            for key, field, tool_value, reference_value in mismatches
        ],
    }


def verdict_for(diff: Mapping[str, Any]) -> str:
    if diff["missing"] or diff["extra"] or diff["duplicates"] or diff["reference_duplicates"]:
        return "FAIL"
    other_fields = {m["field"] for m in diff["mismatches"] if m["field"] != "mtime"}
    if other_fields:
        return "FAIL"
    if any(m["field"] == "mtime" for m in diff["mismatches"]):
        return "DRIFT"
    return "PASS"


def replay_verdict_for(diff: Mapping[str, Any]) -> str:
    """Replay is immutable ground truth, so mtime drift is an ordinary failure."""
    return "PASS" if not any(diff[name] for name in diff) else "FAIL"


def worst_verdict(verdicts: Iterable[str]) -> str:
    return max(verdicts, key=VERDICT_ORDER.index, default="UNCOMPARED")


def stage_evidence(result_prefix: str, staging: Path) -> Path:
    """A local directory holding this attempt's evidence, downloading if remote."""
    staging.mkdir(parents=True)
    if result_prefix.startswith("gs://"):
        gcs.download_tree(result_prefix, staging)
        return staging
    return Path(result_prefix)


def _decompressed(source: Path, into: Path) -> Path:
    """Unpack a published product into the working directory the comparison reads.

    `source.stem` drops the `.gz` the upload added, so what the normalizer is
    handed is named for what is in it.
    """
    target = into / source.stem
    with gzip.open(source, "rb") as packed, open(target, "wb") as plain:
        shutil.copyfileobj(packed, plain)
    return target


def normalize_evidence(
    local_prefix: Path,
    result: Mapping[str, object],
    adapter_dir: Path,
    subject: Subject,
    manifest: Mode,
    output_path: Path,
) -> None:
    """Normalize this attempt's declared product through the capsule.

    Which file that is, and whether it is one file or a directory of parts, is
    read off the mode's own declaration. Asking the evidence instead — *is
    `native/` non-empty?* — answered "directory dataset" for any subject with a
    side output, which sent every `s3-fast-list` listing to a normalizer that
    rightly refused a `--dataset` it does not accept.

    The row's recorded `config` blob is forwarded, not reconstructed: a capsule
    whose output shape depends on a config key has to parse its own output with
    the same blob `command.py` compiled argv from.
    """
    validate_captured_artifacts(local_prefix, result)
    if not manifest.product_artifact:
        raise adapters.AdapterError(
            f"{subject.tool} mode {subject.mode!r} publishes no measured product to compare"
        )
    product = local_prefix / "native" / manifest.product_file
    if manifest.compresses_product and not product.is_file():
        # A text product is published gzipped, so the comparison unpacks it back
        # into the file the normalizer reads. The evidence keeps the name it was
        # uploaded under; only this working copy is the plain one. An attempt
        # that published the plain file is read as it lies: what is in the sink
        # is a fact about that attempt, and the mode's rule is not retroactive.
        product = _decompressed(product.with_name(f"{product.name}.gz"), output_path.parent)
    channel: dict[str, Path | None] = {"dataset": None, "input": None}
    channel["dataset" if manifest.product_channel == "dataset" else "input"] = product
    adapters.normalize_to_path(
        adapter_dir,
        subject.tool,
        subject.mode,
        subject.target_prefix,
        output_path,
        input_path=channel["input"],
        dataset=channel["dataset"],
        config=subject.config,
    )


def _manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_of(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def validate_captured_artifacts(local_prefix: Path, result: Mapping[str, object]) -> None:
    """Bind normalized bytes to hashes in the result marker uploaded after the artifacts."""
    for stem in ("stdout", "stderr"):
        capture = result.get(stem)
        if capture is None:
            # Only stdout may be absent, and only because the product took fd 1.
            if stem == "stdout" and not (local_prefix / "stdout.log.gz").exists():
                continue
            raise adapters.AdapterError(f"{stem}: result.json has invalid artifact identity")
        if not isinstance(capture, Mapping):
            raise adapters.AdapterError(f"{stem}: result.json has invalid artifact identity")
        name = capture.get("name")
        expected = capture.get("sha256")
        if not isinstance(name, str) or Path(name).name != name or not isinstance(expected, str):
            raise adapters.AdapterError(f"{stem}: result.json has invalid artifact identity")
        path = local_prefix / name
        if not path.is_file() or sha256_of(path) != expected:
            raise adapters.AdapterError(f"{stem}: captured artifact does not match result.json")

    expected_native = result.get("native_manifest")
    if not isinstance(expected_native, dict) or not all(
        isinstance(name, str) and isinstance(digest, str)
        for name, digest in expected_native.items()
    ):
        raise adapters.AdapterError("native: result.json has invalid artifact manifest")
    native_root = local_prefix / "native"
    actual_native = _manifest(native_root) if native_root.is_dir() else {}
    if actual_native != expected_native:
        raise adapters.AdapterError("native: captured artifacts do not match result.json")


def _gap(subject: Subject, reason: str, detail: str) -> dict[str, object]:
    return {
        "attempt_id": subject.attempt_id,
        "tool": subject.tool,
        "reason": reason,
        "detail": detail,
        "exit_code": GAP_EXIT_CODES[reason],
    }


def rate_summary(subjects: Sequence[Subject]) -> dict[str, object]:
    """Successes over settled attempts of one rate case.

    A settled failure is a data point here rather than an omission: for these
    cases the hangs and the panics ARE the measurement.
    """
    settled = [s for s in subjects if s.state in TERMINAL_STATES]
    successes = sum(1 for s in settled if s.state == "SUCCEEDED")
    first = subjects[0]
    return {
        "case_id": first.case_id,
        "tool": first.tool,
        "mode": first.mode,
        "attempts": len(settled),
        "successes": successes,
        "rate": round(successes / len(settled), 4) if settled else None,
    }


def prepare_subject(
    subject: Subject, *, adapter_root: str, work_dir: Path
) -> tuple[Prepared | None, dict[str, object] | None]:
    """Bind one subject's evidence and normalize it, or report why it cannot be."""
    if not has_result_marker(subject.result_prefix):
        detail = f"no result.json under {subject.result_prefix}"
        return None, _gap(subject, "missing-evidence", detail)
    raw = read_bytes_at(subject.result_prefix, "result.json")
    result = json.loads(raw)
    if not isinstance(result, dict):
        return None, _gap(subject, "missing-evidence", "result.json is not an object")
    errors = identity_errors(
        result,
        attempt_id=subject.attempt_id,
        case_id=subject.case_id,
        result_prefix=subject.result_prefix,
    )
    if errors:
        return None, _gap(subject, "identity", "; ".join(errors))
    failure = check_failed_subject(result)
    if failure:
        return None, _gap(subject, "failed-subject", failure)
    replay_diagnostics = None
    if subject.replay is not None:
        try:
            replay_diagnostics = validate_replay_evidence(result, subject.replay)
        except ValueError as exc:
            return None, _gap(subject, "replay-evidence", str(exc))
    staging = work_dir / subject.attempt_id
    tsv = work_dir / f"{subject.attempt_id}.tsv"
    try:
        manifest = adapters.mode_manifest(
            adapters.adapter_dir_for(subject.tool, adapter_root), subject.tool, subject.mode
        )
        local_prefix = stage_evidence(subject.result_prefix, staging)
        normalize_evidence(
            local_prefix,
            result,
            adapters.adapter_dir_for(subject.tool, adapter_root),
            subject,
            manifest,
            tsv,
        )
    except adapters.AdapterError as exc:
        return None, _gap(subject, "normalize", str(exc))
    return (
        Prepared(
            subject=subject,
            result_sha256=hashlib.sha256(raw).hexdigest(),
            tsv=tsv,
            product=manifest.product,
            fields=manifest.fields,
            replay_diagnostics=replay_diagnostics,
        ),
        None,
    )


def compare(reference: Prepared, actual: Prepared) -> dict[str, Any]:
    con = duckdb.connect()
    try:
        load_tables(con, reference.tsv, actual.tsv)
        assert_no_null_fields(con, "reference")
        assert_no_null_fields(con, "actual")
        return compute_diff(con)
    finally:
        con.close()


def verify_replay_bucket(
    subjects: Sequence[Subject],
    *,
    adapter_root: str,
    work_dir: Path,
    write_record: bool,
    require_calibration: bool = True,
) -> dict[str, Any]:
    """Verify replay attempts against an immutable oracle.

    Comparative replay measurements need a declared calibrated capacity.  A
    canary or diagnostic is instead evidence for reaching that state, so its
    caller deliberately sets ``require_calibration`` false.
    """
    gaps: list[dict[str, object]] = []
    if require_calibration:
        for subject in subjects:
            assert subject.replay is not None
            if subject.replay.capacity_status != "calibrated":
                gaps.append(
                    _gap(
                        subject,
                        "uncalibrated-capacity",
                        "replay capacity_status is UNCALIBRATED; measurement is refused",
                    )
                )
    if gaps:
        return {
            "target_bucket": subjects[0].target_bucket,
            "backend": "replay",
            "complete": False,
            "verdict": "INCOMPLETE",
            "strata": [],
            "rates": [],
            "gaps": gaps,
        }
    identities: list[tuple[str, str, str]] = []
    for subject in subjects:
        try:
            assert subject.replay is not None
            identities.append(replay_manifest_identity(subject.replay))
        except ManifestError as exc:
            gaps.append(_gap(subject, "manifest", str(exc)))
    distinct = set(identities)
    if len(distinct) > 1:
        gaps.append(
            _gap(
                subjects[0],
                "manifest",
                "replay subjects in one bucket bind different fixture/reference manifests",
            )
        )

    manifest_tsv: Path | None = None
    fixture_sha256 = manifest_uri = manifest_sha256 = ""
    if not gaps and identities:
        fixture_sha256, manifest_uri, manifest_sha256 = identities[0]
        try:
            bucket_key = hashlib.sha256(subjects[0].target_bucket.encode()).hexdigest()[:12]
            # A group may verify the same bucket more than once for separate
            # diagnostics. Hash this invocation's unique ledger IDs into one
            # bounded path instead of sharing mutable staged files.
            invocation_key = hashlib.sha256(
                "\0".join(sorted(subject.attempt_id for subject in subjects)).encode()
            ).hexdigest()
            manifest_dir = work_dir / f"replay-manifest-{bucket_key}-{invocation_key}"
            manifest_tsv = stage_replay_manifest(
                manifest_uri,
                manifest_sha256,
                manifest_dir,
            )
        except Exception as exc:
            # Provider/download errors are binding gaps too. A replay result may
            # not fall back to cross-tool agreement because its oracle is down.
            gaps.append(_gap(subjects[0], "manifest", str(exc)))

    prepared: list[Prepared] = []
    rate_cases: dict[str, list[Subject]] = {}
    for subject in subjects:
        if subject.statistic == "rate":
            rate_cases.setdefault(subject.case_id, []).append(subject)
        if subject.state not in TERMINAL_STATES:
            gaps.append(_gap(subject, "unsettled", f"state {subject.state}"))
            continue
        if subject.state != "SUCCEEDED":
            if subject.statistic != "rate":
                gaps.append(_gap(subject, "absent", f"state {subject.state}"))
            continue
        ready, gap = prepare_subject(subject, adapter_root=adapter_root, work_dir=work_dir)
        if gap is not None:
            gaps.append(gap)
        if ready is not None:
            prepared.append(ready)

    comparisons_by_stratum: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    passed_attempts: set[str] = set()
    if manifest_tsv is not None:
        reference_sha256 = sha256_of(manifest_tsv)
        for actual in sorted(prepared, key=lambda item: item.subject.attempt_id):
            try:
                reference = Prepared(
                    subject=actual.subject,
                    result_sha256=manifest_sha256,
                    tsv=manifest_tsv,
                    product="manifest",
                    fields=("key", "size", "etag", "mtime", "storage_class"),
                )
                diff = compare(reference, actual)
            except MalformedInputError as exc:
                gaps.append(_gap(actual.subject, "malformed", str(exc)))
                continue
            verdict = replay_verdict_for(diff)
            if verdict == "PASS":
                passed_attempts.add(actual.subject.attempt_id)
            record = {
                "attempt_id": actual.subject.attempt_id,
                "tool": actual.subject.tool,
                "mode": actual.subject.mode,
                "fixture_sha256": fixture_sha256,
                "reference_manifest_uri": manifest_uri,
                "reference_manifest_sha256": manifest_sha256,
                "product": actual.product,
                "fields": list(actual.fields),
                "actual_result_sha256": actual.result_sha256,
                "actual_tsv_sha256": sha256_of(actual.tsv),
                "reference_tsv_sha256": reference_sha256,
                "replay_diagnostics": actual.replay_diagnostics,
                "verdict": verdict,
                "diff": diff,
            }
            if write_record:
                write_verify_json(actual.subject.result_prefix, record)
            comparisons_by_stratum.setdefault((actual.product, actual.fields), []).append(record)

    strata: list[dict[str, Any]] = []
    for (product, fields), comparisons in sorted(comparisons_by_stratum.items()):
        strata.append(
            {
                "product": product,
                "fields": list(fields),
                "reference": f"manifest:{manifest_sha256}",
                "subjects": [record["attempt_id"] for record in comparisons],
                "comparisons": comparisons,
                "verdict": worst_verdict(record["verdict"] for record in comparisons),
            }
        )

    rates: list[dict[str, object]] = []
    for case_subjects in rate_cases.values():
        settled = [subject for subject in case_subjects if subject.state in TERMINAL_STATES]
        first = case_subjects[0]
        successes = sum(subject.attempt_id in passed_attempts for subject in settled)
        rates.append(
            {
                "case_id": first.case_id,
                "tool": first.tool,
                "mode": first.mode,
                "attempts": len(settled),
                "successes": successes,
                "rate": round(successes / len(settled), 4) if settled else None,
            }
        )

    complete = not gaps
    verdict = worst_verdict(stratum["verdict"] for stratum in strata)
    return {
        "target_bucket": subjects[0].target_bucket,
        "backend": "replay",
        "complete": complete,
        "verdict": verdict if complete else "INCOMPLETE",
        "strata": strata,
        "rates": rates,
        "gaps": gaps,
    }


def verify_replay_diagnostic(
    subject: Subject,
    *,
    adapter_root: str,
    work_dir: Path,
    write_record: bool,
) -> dict[str, Any]:
    """Verify one non-comparative replay attempt without creating a timing row."""
    result = verify_replay_bucket(
        [subject],
        adapter_root=adapter_root,
        work_dir=work_dir,
        write_record=write_record,
        require_calibration=False,
    )
    comparison = next(
        (
            record
            for stratum in result["strata"]
            for record in stratum["comparisons"]
            if record["attempt_id"] == subject.attempt_id
        ),
        None,
    )
    return {
        "attempt_id": subject.attempt_id,
        "tool": subject.tool,
        "mode": subject.mode,
        "purpose": subject.purpose,
        "capacity_status": subject.replay.capacity_status if subject.replay is not None else "-",
        "complete": result["complete"],
        "verdict": result["verdict"],
        "verification": comparison,
        "gaps": result["gaps"],
    }


def verify_bucket(
    subjects: Sequence[Subject],
    *,
    adapter_root: str,
    work_dir: Path,
    write_record: bool,
) -> dict[str, Any]:
    """One target bucket's strata, rate cases, and the gaps in between."""
    replay_flags = {subject.replay is not None for subject in subjects}
    if len(replay_flags) > 1:
        mixed_gap = _gap(
            subjects[0],
            "mixed-backend",
            "one target-bucket comparison mixes replay and real-S3 attempts",
        )
        return {
            "target_bucket": subjects[0].target_bucket,
            "complete": False,
            "verdict": "INCOMPLETE",
            "strata": [],
            "rates": [],
            "gaps": [mixed_gap],
        }
    if replay_flags == {True}:
        return verify_replay_bucket(
            subjects,
            adapter_root=adapter_root,
            work_dir=work_dir,
            write_record=write_record,
        )
    gaps: list[dict[str, object]] = []
    rates: list[dict[str, object]] = []
    prepared: list[Prepared] = []

    rate_cases: dict[str, list[Subject]] = {}
    for subject in subjects:
        if subject.statistic == "rate":
            rate_cases.setdefault(subject.case_id, []).append(subject)
            continue
        if subject.state not in TERMINAL_STATES:
            gaps.append(_gap(subject, "unsettled", f"state {subject.state}"))
            continue
        if subject.state != "SUCCEEDED":
            gaps.append(_gap(subject, "absent", f"state {subject.state}"))
            continue
        ready, gap = prepare_subject(subject, adapter_root=adapter_root, work_dir=work_dir)
        if gap is not None:
            gaps.append(gap)
        if ready is not None:
            prepared.append(ready)

    for case_subjects in rate_cases.values():
        rates.append(rate_summary(case_subjects))
        gaps.extend(
            _gap(subject, "unsettled", f"state {subject.state}")
            for subject in case_subjects
            if subject.state not in TERMINAL_STATES
        )

    strata: list[dict[str, Any]] = []
    for key in sorted({(p.product, p.fields) for p in prepared}):
        product, fields = key
        members = sorted(
            (p for p in prepared if (p.product, p.fields) == key),
            key=lambda p: p.subject.attempt_id,
        )
        reference, others = members[0], members[1:]
        comparisons: list[dict[str, Any]] = []
        for actual in others:
            try:
                diff = compare(reference, actual)
            except MalformedInputError as exc:
                gaps.append(_gap(actual.subject, "malformed", str(exc)))
                continue
            record = {
                "attempt_id": actual.subject.attempt_id,
                "tool": actual.subject.tool,
                "mode": actual.subject.mode,
                "reference_attempt_id": reference.subject.attempt_id,
                "reference_tool": reference.subject.tool,
                "reference_mode": reference.subject.mode,
                "product": product,
                "fields": list(fields),
                "actual_result_sha256": actual.result_sha256,
                "reference_result_sha256": reference.result_sha256,
                "actual_tsv_sha256": sha256_of(actual.tsv),
                "reference_tsv_sha256": sha256_of(reference.tsv),
                "verdict": verdict_for(diff),
                "diff": diff,
            }
            if write_record:
                write_verify_json(actual.subject.result_prefix, record)
            comparisons.append(record)
        strata.append(
            {
                "product": product,
                "fields": list(fields),
                "reference": reference.subject.attempt_id,
                "subjects": [p.subject.attempt_id for p in members],
                "comparisons": comparisons,
                "verdict": worst_verdict(c["verdict"] for c in comparisons),
            }
        )

    complete = not gaps
    verdict = worst_verdict(s["verdict"] for s in strata)
    return {
        "target_bucket": subjects[0].target_bucket,
        "complete": complete,
        "verdict": verdict if complete else "INCOMPLETE",
        "strata": strata,
        "rates": rates,
        "gaps": gaps,
    }


def write_verify_json(result_prefix: str, record: Mapping[str, Any]) -> None:
    """verify.json is written back under the compared attempt's own prefix, so a
    repeat verification overwrites its own record and never a different case's.
    """
    data = json.dumps(record, indent=2).encode() + b"\n"
    if result_prefix.startswith("gs://"):
        gcs.upload_bytes(
            data, result_prefix.rstrip("/") + "/verify.json", content_type="application/json"
        )
    else:
        (Path(result_prefix) / "verify.json").write_bytes(data)


def verify_group(
    con: sqlite3.Connection,
    group_id: str,
    *,
    adapter_root: str,
    write_record: bool = True,
) -> tuple[int, dict[str, Any]]:
    """Verify one group against its recorded roster. Returns (exit_code, report)."""
    roster = roster_for(con, group_id)
    buckets: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        for bucket in sorted({s.target_bucket for s in roster.subjects}):
            buckets.append(
                verify_bucket(
                    [s for s in roster.subjects if s.target_bucket == bucket],
                    adapter_root=adapter_root,
                    work_dir=work_dir,
                    write_record=write_record,
                )
            )
        for subject in roster.diagnostics:
            diagnostics.append(
                verify_replay_diagnostic(
                    subject,
                    adapter_root=adapter_root,
                    work_dir=work_dir,
                    write_record=write_record,
                )
            )
        comparison_complete = (
            bool(roster.subjects)
            and not roster.blocked
            and not roster.abandoned
            and all(b["complete"] for b in buckets)
        )
        comparison_verdict = (
            "INCOMPLETE"
            if not comparison_complete
            else worst_verdict(b["verdict"] for b in buckets)
        )
        diagnostics_complete = (
            bool(diagnostics)
            and not roster.blocked
            and not roster.abandoned
            and all(item["complete"] for item in diagnostics)
        )
        diagnostics_verdict = (
            "INCOMPLETE"
            if not diagnostics_complete
            else worst_verdict(item["verdict"] for item in diagnostics)
        )
        # A diagnostic-only group has an evidence verdict, not a fictional
        # comparison verdict.  In a mixed group diagnostics remain visible but
        # do not promote, demote, or publish the comparison population.
        complete = comparison_complete if roster.subjects else diagnostics_complete
        verdict = comparison_verdict if roster.subjects else diagnostics_verdict
        report = {
            "group_id": group_id,
            "complete": complete,
            "verdict": verdict,
            "blocked": list(roster.blocked),
            "abandoned": list(roster.abandoned),
            "subjects": len(roster.subjects),
            "diagnostic_subjects": len(roster.diagnostics),
            "diagnostic_complete": diagnostics_complete,
            "diagnostic_verdict": diagnostics_verdict,
            "buckets": buckets,
            "diagnostics": diagnostics,
        }
    return GROUP_EXIT_CODES[verdict], report


def print_samples(diff: Mapping[str, Any]) -> None:
    """Print up to SAMPLE_LIMIT examples of each discrepancy kind, so a
    non-PASS verdict is legible from the console without opening verify.json.
    """
    for label in ("missing", "extra", "duplicates", "reference_duplicates"):
        keys = diff[label]
        if not keys:
            continue
        shown = ", ".join(keys[:SAMPLE_LIMIT])
        more = f" (+{len(keys) - SAMPLE_LIMIT} more)" if len(keys) > SAMPLE_LIMIT else ""
        print(f"      {label}: {shown}{more}")
    for m in diff["mismatches"][:SAMPLE_LIMIT]:
        print(
            f"      mismatch[{m['field']}] {m['key']}: "
            f"tool={m['tool']!r} reference={m['reference']!r}"
        )
    remaining = len(diff["mismatches"]) - SAMPLE_LIMIT
    if remaining > 0:
        print(f"      ... (+{remaining} more mismatches)")


def print_report(report: Mapping[str, Any]) -> None:
    print(f"group {report['group_id']}: {report['subjects']} measurement attempt(s)")
    if report["diagnostic_subjects"]:
        print(
            f"  {report['diagnostic_subjects']} replay diagnostic/canary attempt(s): "
            f"{report['diagnostic_verdict']}"
        )
    for label in ("blocked", "abandoned"):
        for slot in report[label]:
            print(f"  {label}: {slot}")
    for bucket in report["buckets"]:
        print(f"  {bucket['target_bucket']}: {bucket['verdict']}")
        for stratum in bucket["strata"]:
            fields = ",".join(stratum["fields"])
            print(
                f"    {stratum['product']} [{fields}] {stratum['verdict']} "
                f"({len(stratum['subjects'])} subject(s), reference {stratum['reference']})"
            )
            for comparison in stratum["comparisons"]:
                if comparison["verdict"] != "PASS":
                    print(f"    {comparison['attempt_id']}: {comparison['verdict']}")
                    print_samples(comparison["diff"])
        for rate in bucket["rates"]:
            print(
                f"    rate {rate['case_id']}: {rate['successes']}/{rate['attempts']} "
                f"= {rate['rate']}"
            )
        for gap in bucket["gaps"]:
            print(f"    gap {gap['attempt_id']} [{gap['reason']}]: {gap['detail']}")
    for diagnostic in report["diagnostics"]:
        print(
            f"  diagnostic {diagnostic['attempt_id']} [{diagnostic['purpose']}]: "
            f"{diagnostic['verdict']} (capacity {diagnostic['capacity_status']})"
        )
        for gap in diagnostic["gaps"]:
            print(f"    gap {gap['attempt_id']} [{gap['reason']}]: {gap['detail']}")
    print(f"verdict={report['verdict']}")


# Anchored to the repository, not the working directory: a relative default
# resolves against wherever the operator happened to stand, and a missing
# adapter directory then refuses every subject as unnormalizable.
DEFAULT_ADAPTER_ROOT = str(Path(__file__).resolve().parents[3] / "tools")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify one group's evidence against its recorded roster."
    )
    parser.add_argument("--state", default=STATE_FILENAME, help="campaign.db path (sqlite3).")
    parser.add_argument("--group", required=True, help="The group to verify; verify is one group.")
    parser.add_argument("--adapter-root", default=DEFAULT_ADAPTER_ROOT)
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Compare without writing verify.json back under each attempt's prefix.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not Path(args.adapter_root).is_dir():
        print(
            f"verify: adapter root {args.adapter_root} is not a directory; refusing to "
            "report every subject as unnormalizable",
            file=sys.stderr,
        )
        return EXIT_NORMALIZE_FAILED
    con = open_ledger(args.state, readonly=True)
    try:
        exit_code, report = verify_group(
            con, args.group, adapter_root=args.adapter_root, write_record=not args.no_write
        )
    finally:
        con.close()
    print_report(report)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
