"""Compare two attempts' listings against each other and print an agreement verdict.

This is a SKETCH standing in for manager/verify/ (~2,000 lines, including
compare.py's careful hex-staged byte-order joins). It uses plain UTF-8 text
comparison in DuckDB rather than hex-staging every field for exact byte
ordering, so it will misbehave on keys that are not valid UTF-8 -- the real
verifier exists partly to make that case correct. Non-UTF-8 keys are OUT of
scope here; see common/contract.py's bytes-based Record for the real answer.

Round 3 repoints this from a blessed reference manifest to CROSS-ATTEMPT
comparison: --reference-attempt-dir is another job's destination (e.g. the
aws-cli case's), not a manifest file. A PASS here means two tools' listings
AGREE, not that either is correct against ground truth -- there is no
manifest.py anymore. The campaign's primary validity signal is row_count
(see measure.py, report.py); this comparison is the on-demand deep diff a
disagreement (or a curiosity) calls for. See README.md for the named
limitation this implies on a mutable bucket, and for the NULL-blind
anti-join bug this round's assert_no_null_fields closes.

It refuses rather than guesses in several places (see README.md's "The
minimum rigor we kept" sections), each a distinct exit code, never folded
into FAIL: zero or several attempt leaves under a destination; a leaf with
no result.json (incomplete/torn); a leaf whose recorded case doesn't match
what the caller expected; a leaf whose subject failed or timed out (a
failed attempt is not a listing finding); a capsule normalize.py that
exited nonzero; and a row with a NULL field in either normalized TSV.

Verdict (once both leaves are selected, complete, bound, and neither
subject failed):
    PASS  -- no missing keys, no extra keys, no duplicate keys, no field
             mismatches.
    DRIFT -- the only field mismatches are mtime (a moving target across
             re-runs of most tools; mirrors the real verifier's spirit
             without its full drift taxonomy).
    FAIL  -- anything else: missing/extra keys, duplicates, or a mismatch on
             size/etag/storage_class.

Usage:
    verify.py --tool s5cmd --mode recursive --bucket some-bucket \\
        --attempt-dir gs://results/s5cmd-job/ \\
        --reference-attempt-dir gs://results/aws-cli-job/
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import tempfile
from pathlib import Path

import duckdb

import adapters
import gcs
from contract import EXIT_AMBIGUOUS_LEAVES, EXIT_BINDING_MISMATCH, EXIT_FAILED_SUBJECT
from contract import EXIT_MALFORMED_INPUT, EXIT_MISSING_MARKER, EXIT_NORMALIZE_FAILED
from contract import VERDICT_EXIT_CODES, sha256_of

_COLUMNS = "{'key':'VARCHAR','size':'VARCHAR','etag':'VARCHAR','mtime':'VARCHAR','storage_class':'VARCHAR'}"
# Disabling quote interpretation entirely: the contract TSV has no quoting
# dialect of its own -- a key or etag containing a literal '"' is an ordinary
# character, never the start of a quoted field. Without this, DuckDB's
# default CSV quoting could reinterpret such a field and misplace columns,
# an adapter-honest row read back as something it never was.
_READ_CSV_OPTS = "quote='', escape=''"
MISMATCH_FIELDS = ("size", "etag", "mtime", "storage_class")
SAMPLE_LIMIT = 5


class MalformedInputError(Exception):
    """A normalized TSV has a NULL field -- an anti-join over it would be
    NULL-blind and silently under-report every discrepancy list.
    """


def list_leaves(destination: str) -> list[str]:
    """Immediate child leaves under a job's destination prefix, local or GCS."""
    if destination.startswith("gs://"):
        return gcs.list_child_prefixes(destination)
    base = Path(destination)
    if not base.is_dir():
        return []
    return [str(p) + "/" for p in sorted(base.iterdir()) if p.is_dir()]


def resolve_leaf(destination: str) -> str | None:
    """Exactly one child leaf under destination: return it.

    Zero or 2+ leaves means two launches raced (or none ran) -- ambiguity is
    refused here, never resolved by picking the newest or the first.
    """
    leaves = list_leaves(destination)
    if len(leaves) == 1:
        return leaves[0]
    print(
        f"verify: expected exactly one attempt leaf under {destination}, found "
        f"{len(leaves)}: {leaves}",
        file=sys.stderr,
    )
    return None


def read_bytes_at(leaf: str, name: str) -> bytes:
    if leaf.startswith("gs://"):
        return gcs.download_bytes(leaf.rstrip("/") + "/" + name)
    return (Path(leaf) / name).read_bytes()


def has_result_marker(leaf: str) -> bool:
    """A leaf is only complete once result.json lands -- see measure.py's
    upload(), which writes it last.
    """
    if leaf.startswith("gs://"):
        return gcs.blob_exists(leaf.rstrip("/") + "/result.json")
    return (Path(leaf) / "result.json").exists()


def check_binding(result: dict, expected: dict) -> list[str]:
    """Where `result` disagrees with `expected` on any key `expected` states.
    A non-empty list means this is the wrong attempt for this comparison,
    not a tool finding.
    """
    return [
        f"{field}: leaf={result.get(field)!r} expected={value!r}"
        for field, value in expected.items()
        if result.get(field) != value
    ]


def check_failed_subject(result: dict) -> str | None:
    """A message if the leaf's own subject failed or timed out, else None.

    A failed or truncated run has nothing to say about listing agreement --
    refusing here is what keeps a subject crash from reading as a diff.
    """
    if result.get("timed_out"):
        return "subject timed out"
    if result.get("exit_code") != 0:
        return f"subject exited {result.get('exit_code')}"
    return None


def load_tables(con: duckdb.DuckDBPyConnection, reference_tsv: Path, actual_tsv: Path) -> None:
    for name, path in (("reference", reference_tsv), ("actual", actual_tsv)):
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
    bad = con.execute(
        f"SELECT count(*) FROM {table} WHERE key IS NULL OR size IS NULL OR etag IS NULL "
        "OR mtime IS NULL OR storage_class IS NULL"
    ).fetchone()[0]
    if bad:
        raise MalformedInputError(f"{table}: {bad} row(s) have a NULL field")


def compute_diff(con: duckdb.DuckDBPyConnection) -> dict:
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
    duplicates = con.execute(
        "SELECT key FROM actual GROUP BY key HAVING count(*) > 1 ORDER BY key"
    ).fetchall()

    # Deduplicate before the join so a duplicate key does not multiply its own mismatches.
    con.execute("CREATE TABLE actual_u AS SELECT DISTINCT ON (key) * FROM actual ORDER BY key")

    subqueries = []
    for order, field in enumerate(MISMATCH_FIELDS, start=1):
        # ETag compares case-insensitively -- it's a hex digest, and casing
        # differs harmlessly across tools/SDKs; every other field is exact.
        compare = f"lower(a.{field}) <> lower(r.{field})" if field == "etag" else f"a.{field} <> r.{field}"
        subqueries.append(
            f"SELECT a.key, {order} AS ord, '{field}' AS field, a.{field} AS tool_value, "
            f"r.{field} AS reference_value FROM actual_u a JOIN reference r USING (key) "
            f"WHERE a.{field} <> '-' AND {compare}"
        )
    mismatches = con.execute(
        "SELECT key, field, tool_value, reference_value FROM ("
        + " UNION ALL ".join(subqueries) + ") ORDER BY key, ord"
    ).fetchall()

    return {
        "missing": [row[0] for row in missing],
        "extra": [row[0] for row in extra],
        "duplicates": [row[0] for row in duplicates],
        "mismatches": [
            {"key": key, "field": field, "tool": tool_value, "reference": reference_value}
            for key, field, tool_value, reference_value in mismatches
        ],
    }


def verdict_for(diff: dict) -> str:
    if diff["missing"] or diff["extra"] or diff["duplicates"]:
        return "FAIL"
    other_fields = {m["field"] for m in diff["mismatches"] if m["field"] != "mtime"}
    if other_fields:
        return "FAIL"
    if any(m["field"] == "mtime" for m in diff["mismatches"]):
        return "DRIFT"
    return "PASS"


def print_samples(diff: dict) -> None:
    """Print up to SAMPLE_LIMIT examples of each discrepancy kind, so a
    non-PASS verdict is legible from the console without opening verify.json.
    """
    for label in ("missing", "extra", "duplicates"):
        keys = diff[label]
        if not keys:
            continue
        shown = ", ".join(keys[:SAMPLE_LIMIT])
        more = f" (+{len(keys) - SAMPLE_LIMIT} more)" if len(keys) > SAMPLE_LIMIT else ""
        print(f"  {label}: {shown}{more}")
    if diff["mismatches"]:
        shown = diff["mismatches"][:SAMPLE_LIMIT]
        for m in shown:
            print(f"  mismatch[{m['field']}] {m['key']}: tool={m['tool']!r} reference={m['reference']!r}")
        remaining = len(diff["mismatches"]) - len(shown)
        if remaining > 0:
            print(f"  ... (+{remaining} more mismatches)")


def write_verify_json(leaf: str, output: dict) -> None:
    """verify.json is written back INTO the actual leaf, not dumped to CWD --
    a repeat verification overwrites its own leaf's own record, never a
    different case's.
    """
    data = json.dumps(output, indent=2).encode() + b"\n"
    if leaf.startswith("gs://"):
        gcs.upload_bytes(data, leaf.rstrip("/") + "/verify.json", content_type="application/json")
    else:
        (Path(leaf) / "verify.json").write_bytes(data)


def verify_leaves(
    *, tool: str, bucket: str, prefix: str, mode: str,
    actual_destination: str, reference_destination: str, adapter_root: str,
) -> tuple[int, dict]:
    """Resolve, bind-check, and normalize both sides through the same path;
    diff them; write verify.json into the actual leaf. Returns
    (exit_code, output) -- output has "verdict"/"diff" on a completed
    comparison, or just "error" on an earlier refusal.
    """
    actual_leaf = resolve_leaf(actual_destination)
    if actual_leaf is None:
        return EXIT_AMBIGUOUS_LEAVES, {"error": f"ambiguous actual leaves under {actual_destination}"}
    reference_leaf = resolve_leaf(reference_destination)
    if reference_leaf is None:
        return EXIT_AMBIGUOUS_LEAVES, {"error": f"ambiguous reference leaves under {reference_destination}"}

    for label, leaf in (("actual", actual_leaf), ("reference", reference_leaf)):
        if not has_result_marker(leaf):
            return EXIT_MISSING_MARKER, {"error": f"{label} leaf {leaf} has no result.json"}

    actual_result = json.loads(read_bytes_at(actual_leaf, "result.json"))
    reference_result = json.loads(read_bytes_at(reference_leaf, "result.json"))

    mismatches = check_binding(actual_result, {"tool": tool, "bucket": bucket, "prefix": prefix, "mode": mode})
    if mismatches:
        return EXIT_BINDING_MISMATCH, {"error": "actual leaf does not match the expected case", "mismatches": mismatches}
    # The reference is necessarily a different tool/mode; only the target
    # (bucket/prefix) needs to match, or the two sides are not even
    # attempting to list the same thing.
    ref_mismatches = check_binding(reference_result, {"bucket": bucket, "prefix": prefix})
    if ref_mismatches:
        return EXIT_BINDING_MISMATCH, {"error": "reference leaf targets a different bucket/prefix", "mismatches": ref_mismatches}

    for label, result in (("actual", actual_result), ("reference", reference_result)):
        failure = check_failed_subject(result)
        if failure:
            return EXIT_FAILED_SUBJECT, {"error": f"{label}: {failure}"}

    reference_tool, reference_mode = reference_result["tool"], reference_result["mode"]
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        actual_tsv, reference_tsv = work_dir / "actual.tsv", work_dir / "reference.tsv"
        try:
            actual_native = gzip.decompress(read_bytes_at(actual_leaf, "stdout.log.gz"))
            actual_tsv.write_bytes(adapters.normalize_attempt(
                adapters.adapter_dir_for(tool, adapter_root), tool, mode, prefix, actual_native
            ))
            reference_native = gzip.decompress(read_bytes_at(reference_leaf, "stdout.log.gz"))
            reference_tsv.write_bytes(adapters.normalize_attempt(
                adapters.adapter_dir_for(reference_tool, adapter_root), reference_tool, reference_mode,
                reference_result.get("prefix", ""), reference_native,
            ))
        except adapters.AdapterError as exc:
            return EXIT_NORMALIZE_FAILED, {"error": str(exc)}

        con = duckdb.connect()
        try:
            load_tables(con, reference_tsv, actual_tsv)
            try:
                assert_no_null_fields(con, "reference")
                assert_no_null_fields(con, "actual")
            except MalformedInputError as exc:
                return EXIT_MALFORMED_INPUT, {"error": str(exc)}
            diff = compute_diff(con)
        finally:
            con.close()

        verdict = verdict_for(diff)
        output = {
            "tool": tool, "mode": mode,
            "reference_tool": reference_tool, "reference_mode": reference_mode,
            "actual_leaf": actual_leaf, "reference_leaf": reference_leaf,
            "verdict": verdict,
            "actual_tsv_sha256": sha256_of(actual_tsv),
            "reference_tsv_sha256": sha256_of(reference_tsv),
            "diff": diff,
        }
        write_verify_json(actual_leaf, output)

    return VERDICT_EXIT_CODES[verdict], output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two attempts' listings and print an agreement verdict.")
    parser.add_argument("--tool", required=True, help="Expected tool for --attempt-dir; checked against its result.json.")
    parser.add_argument("--bucket", required=True, help="Expected bucket; checked against both leaves' result.json.")
    parser.add_argument("--prefix", default="", help="Expected prefix; checked against both leaves' result.json.")
    parser.add_argument("--mode", required=True, help="Expected mode for --attempt-dir; checked against its result.json.")
    parser.add_argument(
        "--attempt-dir", required=True,
        help="Local path or gs:// prefix for the job's destination (parent of its attempt leaves).",
    )
    parser.add_argument(
        "--reference-attempt-dir", required=True,
        help="Another job's destination to compare against -- not a blessed manifest.",
    )
    parser.add_argument(
        "--adapter-root", default="tools",
        help="Checkout-relative directory holding tools/<tool>/adapter capsules.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exit_code, output = verify_leaves(
        tool=args.tool, bucket=args.bucket, prefix=args.prefix, mode=args.mode,
        actual_destination=args.attempt_dir, reference_destination=args.reference_attempt_dir,
        adapter_root=args.adapter_root,
    )
    if "diff" in output:
        diff = output["diff"]
        print(f"missing={len(diff['missing'])} extra={len(diff['extra'])} "
              f"duplicates={len(diff['duplicates'])} mismatches={len(diff['mismatches'])}")
        if output["verdict"] != "PASS":
            print_samples(diff)
        print(f"verdict={output['verdict']}")
    else:
        print(f"verify: {output.get('error', 'refused')}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
