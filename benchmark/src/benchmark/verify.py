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
underneath the study (`docs/identity.md` § *What identity cannot cover*).
For the explicit small Docker canary check, every saved listing is instead
compared with the saved AWS CLI canary. Agreement is still not independent
ground truth, and a disagreement may be bucket movement, a tool, or its
normalizer.
Replay campaigns are row-count-only: this explicit content verifier refuses
them without downloading or normalizing their retained raw products.

Two decisions this module makes where the docs are silent:

- A real-S3 `statistic: rate` case is summarized as successes over attempts and
  takes no part in cross-tool agreement.
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
import math
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
HEX64 = set("0123456789abcdef")

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
    "replay-row-count-only": EXIT_INCOMPLETE_GROUP,
    "uncalibrated-capacity": EXIT_INCOMPLETE_GROUP,
    "mixed-backend": EXIT_BINDING_MISMATCH,
}


def expected_result_binding(row: sqlite3.Row) -> dict[str, object]:
    """The result fields frozen by the ledger row that launched an attempt."""
    return {
        "group_id": row["group_id"],
        "job_name": row["job_name"],
        "case_id": row["case_id"],
        "attempt_id": row["attempt_id"],
        "tool": row["tool"],
        "mode": row["mode"],
        "bucket": row["target_bucket"],
        "region": row["target_region"],
        "prefix": row["target_prefix"],
        "auth_role": row["auth_role"],
        "image": row["image_uri"],
        "image_set_sha256": row["image_set_sha256"],
        "config": json.loads(row["config"]),
        "replay": None if row["replay"] is None else json.loads(row["replay"]),
        "declared_resources": {
            "machine_type": row["machine_type"],
            "vcpus": row["vcpus"],
            "memory_gb": row["memory_gb"],
            "container_memory_gb": row["container_memory_gb"],
        },
    }


def result_binding_errors(
    expected: Mapping[str, object], result: dict[str, object], *, purpose: str | None = None
) -> list[str]:
    """Where result evidence disagrees with frozen intent or its marker contract."""
    errors = [
        name for name, value in expected.items() if name not in result or result.get(name) != value
    ]
    errors.extend(result_semantic_errors(result, purpose=purpose))
    return errors


def result_semantic_errors(result: dict[str, object], *, purpose: str | None = None) -> list[str]:
    errors: list[str] = []
    exit_code = result.get("exit_code")
    worker_exit_code = result.get("worker_exit_code")
    timed_out = result.get("timed_out")
    row_count = result.get("row_count")
    row_count_error = result.get("row_count_error")
    execution = result.get("execution")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        errors.append("exit_code")
    if isinstance(worker_exit_code, bool) or not isinstance(worker_exit_code, int):
        errors.append("worker_exit_code")
    if not isinstance(timed_out, bool):
        errors.append("timed_out")
    if row_count is not None and (
        isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0
    ):
        errors.append("row_count")
    if row_count_error is not None and not isinstance(row_count_error, str):
        errors.append("row_count_error")
    if execution is None:
        replay_evidence = result.get("replay_evidence")
        replay_refusal = (
            isinstance(result.get("replay"), dict)
            and isinstance(replay_evidence, dict)
            and bool(replay_evidence.get("errors"))
        )
        if not isinstance(result.get("setup"), dict) and not replay_refusal:
            errors.append("setup")
        if exit_code == 0:
            errors.append("exit_code")
        errors.extend(
            name
            for name in (
                "wall_seconds",
                "max_rss_kb",
                "row_count",
                "row_count_error",
                "product",
                "product_error",
            )
            if result.get(name) is not None
        )
        return errors
    if not isinstance(execution, dict):
        return [*errors, "execution"]
    max_rss_kb = result.get("max_rss_kb")
    execution_rss = execution.get("max_rss_kb")
    if (
        isinstance(max_rss_kb, bool)
        or not isinstance(max_rss_kb, int)
        or max_rss_kb < 0
        or max_rss_kb != execution_rss
    ):
        errors.append("max_rss_kb")
    elapsed_ns = execution.get("elapsed_ns")
    wall_seconds = result.get("wall_seconds")
    if isinstance(elapsed_ns, bool) or not isinstance(elapsed_ns, int) or elapsed_ns < 0:
        errors.append("execution.elapsed_ns")
    if (
        isinstance(wall_seconds, bool)
        or not isinstance(wall_seconds, (int, float))
        or not math.isfinite(wall_seconds)
    ):
        errors.append("wall_seconds")
    elif (
        isinstance(elapsed_ns, int)
        and not isinstance(elapsed_ns, bool)
        and wall_seconds != round(elapsed_ns / 1_000_000_000, 6)
    ):
        errors.append("wall_seconds/elapsed_ns")
    for name in (
        "timed_out",
        "process_group_empty",
        "descendants_empty",
        "process_tree_clean",
        "subreaper_enabled",
    ):
        if not isinstance(execution.get(name), bool):
            errors.append(f"execution.{name}")
    if execution.get("timed_out") != timed_out:
        errors.append("execution.timed_out")
    cgroup = execution.get("cgroup")
    if not isinstance(cgroup, dict):
        errors.append("execution.cgroup")
    else:
        for name in ("oom_delta", "oom_kill_delta"):
            value = cgroup.get(name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                errors.append(f"execution.cgroup.{name}")
    errors.extend(result_capture_errors(result))
    counted = (
        purpose != "preparation"
        and exit_code == 0
        and timed_out is False
        and result.get("product_error") is None
    )
    if counted and row_count_error is None and row_count is None:
        errors.append("row_count")
    if not counted and row_count is not None:
        errors.append("row_count")
    return errors


def result_capture_errors(result: dict[str, object]) -> list[str]:
    """Where the marker fails to say what this attempt published, and how."""
    errors: list[str] = []
    minimal = result.get("evidence_profile") == "minimal-replay"
    product = result.get("product")
    if product is not None:
        errors.extend(
            f"product.{name}"
            for name in _artifact_errors(product, digest_optional=True, minimal=minimal)
        )
    product_error = result.get("product_error")
    if product_error is not None and not isinstance(product_error, str):
        errors.append("product_error")
    for stem in ("stdout", "stderr"):
        capture = result.get(stem)
        if capture is None:
            if stem == "stderr" or product is None:
                errors.append(stem)
            continue
        errors.extend(f"{stem}.{name}" for name in _artifact_errors(capture, minimal=minimal))
    return errors


def _artifact_errors(
    block: object, *, digest_optional: bool = False, minimal: bool = False
) -> list[str]:
    """The name/size/digest every published artifact is recorded by."""
    if not isinstance(block, dict):
        return ["shape"]
    errors: list[str] = []
    name = block.get("name")
    if not isinstance(name, str) or not name:
        errors.append("name")
    size = block.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        errors.append("size_bytes")
    digest = block.get("sha256")
    if digest is None:
        if not minimal and (not digest_optional or block.get("channel") != "dataset"):
            errors.append("sha256")
    elif not isinstance(digest, str) or len(digest) != 64 or set(digest) - HEX64:
        errors.append("sha256")
    return errors


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
    result_binding: dict[str, object]

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
            result_binding=expected_result_binding(row),
        )


@dataclass(frozen=True)
class Roster:
    """A group's comparative attempts, replay attempts, and owed slots."""

    subjects: tuple[Subject, ...]
    replay_attempts: tuple[Subject, ...]
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


def roster_for(
    con: sqlite3.Connection, group_id: str, *, include_docker_canaries: bool = False
) -> Roster:
    """Read the comparison population and replay refusal from recorded rows."""
    rows = tuple(attempt_rows(con, group_id=group_id))
    attempts = tuple(Subject.from_row(row) for row in rows)
    replay_attempts = tuple(subject for subject in attempts if subject.replay is not None)
    subjects = tuple(
        subject
        for row, subject in zip(rows, attempts, strict=True)
        if row["replay"] is None
        and (
            row["purpose"] == "measurement"
            or (
                include_docker_canaries
                and row["purpose"] == "canary"
                and row["executor"] == "docker"
            )
        )
    )
    slots = pending_rows(con, group_id=group_id)
    return Roster(
        subjects=subjects,
        replay_attempts=replay_attempts,
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


def rate_subject_succeeded(subject: Subject) -> bool:
    """Whether one settled rate attempt has complete, successful evidence."""
    if subject.state != "SUCCEEDED" or not has_result_marker(subject.result_prefix):
        return False
    try:
        result = json.loads(read_bytes_at(subject.result_prefix, "result.json"))
        if (
            not isinstance(result, dict)
            or result_binding_errors(subject.result_binding, result, purpose=subject.purpose)
            or check_failed_subject(result) is not None
        ):
            return False
        row_count = result.get("row_count")
        return bool(
            result.get("worker_exit_code") == 0
            and isinstance(row_count, int)
            and not isinstance(row_count, bool)
            and (
                subject.replay is None
                or not replay_contract.evidence_errors(
                    subject.replay, result.get("replay_evidence"), purpose=subject.purpose
                )
            )
        )
    except (OSError, ValueError):
        return False


def rate_summary(subjects: Sequence[Subject]) -> dict[str, object]:
    """Successes over settled attempts of one rate case.

    A settled failure is a data point here rather than an omission: for these
    cases the hangs and the panics ARE the measurement.
    """
    settled = [s for s in subjects if s.state in TERMINAL_STATES]
    successes = sum(rate_subject_succeeded(subject) for subject in settled)
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


def verify_bucket(
    subjects: Sequence[Subject],
    *,
    adapter_root: str,
    work_dir: Path,
    write_record: bool,
    reference_tool: str | None = None,
) -> dict[str, Any]:
    """One target bucket's strata, rate cases, and the gaps in between."""
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
    if reference_tool is None:
        groups = [
            (
                key[0],
                key[1],
                sorted(
                    (p for p in prepared if (p.product, p.fields) == key),
                    key=lambda p: p.subject.attempt_id,
                ),
            )
            for key in sorted({(p.product, p.fields) for p in prepared})
        ]
    else:
        references = [p for p in prepared if p.subject.tool == reference_tool]
        groups = []
        if len(references) == 1:
            reference = references[0]
            groups.append(
                (
                    reference.product,
                    reference.fields,
                    [
                        reference,
                        *sorted(
                            (p for p in prepared if p is not reference),
                            key=lambda p: p.subject.attempt_id,
                        ),
                    ],
                )
            )
    for product, fields, members in groups:
        reference, others = members[0], members[1:]
        comparisons: list[dict[str, Any]] = []
        for actual in others:
            try:
                diff = compare(reference, actual)
            except MalformedInputError as exc:
                gaps.append(_gap(actual.subject, "malformed", str(exc)))
                continue
            record: dict[str, Any] = {
                "attempt_id": actual.subject.attempt_id,
                "tool": actual.subject.tool,
                "mode": actual.subject.mode,
                "reference_attempt_id": reference.subject.attempt_id,
                "reference_tool": reference.subject.tool,
                "reference_mode": reference.subject.mode,
                "product": product,
                "fields": list(actual.fields if reference_tool else fields),
                "actual_result_sha256": actual.result_sha256,
                "reference_result_sha256": reference.result_sha256,
                "actual_tsv_sha256": sha256_of(actual.tsv),
                "reference_tsv_sha256": sha256_of(reference.tsv),
                "verdict": verdict_for(diff),
                "diff": diff,
            }
            if reference_tool is not None:
                record["reference_fields"] = list(reference.fields)
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
    include_docker_canaries: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Verify one group against its recorded roster. Returns (exit_code, report)."""
    roster = roster_for(
        con, group_id, include_docker_canaries=include_docker_canaries
    )
    if roster.replay_attempts:
        report = {
            "group_id": group_id,
            "complete": False,
            "verdict": "INCOMPLETE",
            "refusal": (
                "replay groups are row-count-only and are reported from bound result.json; "
                "content verification applies only to real-S3 groups"
            ),
            "replay_attempts": [subject.attempt_id for subject in roster.replay_attempts],
        }
        return GROUP_EXIT_CODES["INCOMPLETE"], report
    if include_docker_canaries and (
        not roster.subjects
        or any(subject.purpose != "canary" for subject in roster.subjects)
        or sum(subject.tool == "aws-cli" for subject in roster.subjects) != 1
    ):
        report = {
            "group_id": group_id,
            "complete": False,
            "verdict": "INCOMPLETE",
            "refusal": (
                "the small Docker canary check requires a Docker-only canary group "
                "containing exactly one aws-cli result"
            ),
            "replay_attempts": [],
        }
        return GROUP_EXIT_CODES["INCOMPLETE"], report
    buckets: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        for bucket in sorted({s.target_bucket for s in roster.subjects}):
            buckets.append(
                verify_bucket(
                    [s for s in roster.subjects if s.target_bucket == bucket],
                    adapter_root=adapter_root,
                    work_dir=work_dir,
                    write_record=write_record,
                    reference_tool="aws-cli" if include_docker_canaries else None,
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
        report = {
            "group_id": group_id,
            "complete": comparison_complete,
            "verdict": comparison_verdict,
            "blocked": list(roster.blocked),
            "abandoned": list(roster.abandoned),
            "subjects": len(roster.subjects),
            "buckets": buckets,
            "caveat": (
                "cross-tool agreement only; the real S3 bucket may change during the run"
                if include_docker_canaries
                else None
            ),
        }
    return GROUP_EXIT_CODES[comparison_verdict], report


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
    if "refusal" in report:
        print(f"group {report['group_id']}: INCOMPLETE")
        print(f"  refused: {report['refusal']}")
        for attempt_id in report["replay_attempts"]:
            print(f"  replay attempt: {attempt_id}")
        print("verdict=INCOMPLETE")
        return
    print(f"group {report['group_id']}: {report['subjects']} measurement attempt(s)")
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
    if report.get("caveat"):
        print(f"  note: {report['caveat']}")
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
        "--include-docker-canaries",
        action="store_true",
        help="compare saved Docker canary listings to the AWS CLI canary",
    )
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
            con,
            args.group,
            adapter_root=args.adapter_root,
            write_record=not args.no_write,
            include_docker_canaries=args.include_docker_canaries,
        )
    finally:
        con.close()
    print_report(report)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
