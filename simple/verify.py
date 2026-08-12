"""Compare one attempt's listing against a reference manifest and print a verdict.

This is a SKETCH standing in for manager/verify/ (~2,000 lines, including
compare.py's careful hex-staged byte-order joins). It uses plain UTF-8 text
comparison in DuckDB rather than hex-staging every field for exact byte
ordering, so it will misbehave on keys that are not valid UTF-8 -- the real
verifier exists partly to make that case correct. It does not distinguish
"manifest is corrupt" from "adapter violated the contract"; both just show up
as noisy diffs.

It does keep several cheap properties (see README.md's "The minimum rigor we
kept" / "Round 2: purpose-fitness additions"): it refuses rather than guesses
when a job's destination holds zero or several attempt leaves, it refuses
rather than verifies a leaf with no result.json (an incomplete or torn
attempt), it refuses rather than verifies a leaf whose recorded
tool/bucket/prefix/mode don't match what the caller expected, and it refuses
rather than silently corrupts a row whose key contains TAB/LF/CR -- each a
distinct exit code, never folded into FAIL. Non-UTF-8 keys remain OUT of
scope: everything here is DuckDB VARCHAR (UTF-8 text); see
common/contract.py's bytes-based Record for the real answer.

Contract (informal, matching src/s3_listing_study/common/contract.py in
spirit): one record per line, TAB-separated:

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class

with "-" meaning "this mode does not expose the field". A manifest is
expected to already be in this shape; an attempt's native tool output is
normalized into it by tools.py.

Verdict (once a leaf is selected, complete, and bound to the right case):
    PASS  -- no missing keys, no extra keys, no duplicate keys, no field
             mismatches.
    DRIFT -- the only field mismatches are mtime (a moving target across
             re-runs of most tools; mirrors the real verifier's spirit
             without its full drift taxonomy).
    FAIL  -- anything else: missing/extra keys, duplicates, or a mismatch on
             size/etag/storage_class.

Usage:
    verify.py --tool aws-cli --bucket some-bucket --prefix "" --mode s3api-v2-text \\
        --attempt-dir /local/or/gs://bucket/job-id/ --manifest /path/to/manifest.tsv
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

from tools import TOOLS, FramingViolation, assert_framing_safe

_COLUMNS = "{'key':'VARCHAR','size':'VARCHAR','etag':'VARCHAR','mtime':'VARCHAR','storage_class':'VARCHAR'}"
# Disabling quote interpretation entirely: the contract TSV has no quoting
# dialect of its own -- a key or etag containing a literal '"' is an ordinary
# character, never the start of a quoted field. Without this, DuckDB's
# default CSV quoting could reinterpret such a field and misplace columns,
# an adapter-honest row read back as something it never was. read_csv() takes
# these as keyword args; COPY ... TO takes them as space-separated options.
_READ_CSV_OPTS = "quote='', escape=''"
_COPY_OPTS = "QUOTE '', ESCAPE ''"

EXIT_CODES = {"PASS": 0, "DRIFT": 2, "FAIL": 1}
EXIT_AMBIGUOUS_LEAVES = 3
EXIT_MISSING_MARKER = 4
EXIT_BINDING_MISMATCH = 5
EXIT_FRAMING_VIOLATION = 6
SAMPLE_LIMIT = 5


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_leaves(destination: str) -> list[str]:
    """Immediate child leaves under a job's destination prefix, local or GCS."""
    if destination.startswith("gs://"):
        result = subprocess.run(
            ["gsutil", "ls", "-d", destination.rstrip("/") + "/*/"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
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


def has_result_marker(leaf: str) -> bool:
    """A leaf is only complete once result.json lands -- see measure.py's
    upload(), which writes it last.
    """
    if leaf.startswith("gs://"):
        result = subprocess.run(
            ["gsutil", "-q", "stat", leaf.rstrip("/") + "/result.json"], capture_output=True
        )
        return result.returncode == 0
    return (Path(leaf) / "result.json").exists()


def check_binding(result: dict, args: argparse.Namespace) -> list[str]:
    """Where the selected leaf's recorded case disagrees with what the caller
    expected. A non-empty list means this is the wrong attempt to verify
    against this manifest, not a tool finding.
    """
    expected = {"tool": args.tool, "bucket": args.bucket, "prefix": args.prefix, "mode": args.mode}
    return [
        f"{field}: leaf={result.get(field)!r} expected={value!r}"
        for field, value in expected.items()
        if result.get(field) != value
    ]


def fetch_attempt_dir(leaf: str, work_dir: Path) -> Path:
    if not leaf.startswith("gs://"):
        return Path(leaf)
    local_dir = work_dir / "attempt"
    local_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["gsutil", "-m", "cp", "-r", leaf.rstrip("/") + "/*", str(local_dir)], check=True)
    return local_dir


def locate_native_output(attempt_dir: Path, tool: str, work_dir: Path) -> Path:
    """Where `tool`'s native output lives on local disk, ready for normalize_sql.

    Most tools stream to stdout, which measure.py gzips; decompress
    stdout.log.gz. A tool declaring a file-sink "native" filename (tools.py's
    swath-parquet, for example) wrote it directly into the attempt dir
    uncompressed, so no decompression step applies -- it is simply there.
    """
    native = TOOLS[tool].get("native", "stdout")
    if native != "stdout":
        return attempt_dir / native
    gz_path = attempt_dir / "stdout.log.gz"
    suffix = Path(TOOLS[tool]["native_file"]).suffix or ".out"
    native_path = work_dir / f"native{suffix}"
    with gzip.open(gz_path, "rb") as src, open(native_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return native_path


def normalize_to_tsv(tool: str, native_path: Path, out_path: Path) -> None:
    select_sql = TOOLS[tool]["normalize_sql"](str(native_path))
    con = duckdb.connect()
    try:
        assert_framing_safe(con, select_sql)
        con.execute(f"COPY ({select_sql}) TO '{out_path}' (DELIMITER '\t', HEADER false, {_COPY_OPTS})")
    finally:
        con.close()


def load_tables(con: duckdb.DuckDBPyConnection, manifest_path: Path, actual_path: Path) -> None:
    for name, path in (("manifest", manifest_path), ("actual", actual_path)):
        con.execute(
            f"CREATE TABLE {name} AS SELECT * FROM "
            f"read_csv(?, delim='\t', header=false, columns={_COLUMNS}, {_READ_CSV_OPTS})",
            [str(path)],
        )


def compute_diff(con: duckdb.DuckDBPyConnection) -> dict:
    missing = con.execute(
        "SELECT key FROM manifest WHERE key NOT IN (SELECT key FROM actual) ORDER BY key"
    ).fetchall()
    extra = con.execute(
        "SELECT key FROM actual WHERE key NOT IN (SELECT key FROM manifest) ORDER BY key"
    ).fetchall()
    duplicates = con.execute(
        "SELECT key FROM actual GROUP BY key HAVING count(*) > 1 ORDER BY key"
    ).fetchall()

    # Deduplicate before the join so a duplicate key does not multiply its own mismatches.
    con.execute("CREATE TABLE actual_u AS SELECT DISTINCT ON (key) * FROM actual ORDER BY key")

    mismatches = con.execute(
        """
        SELECT key, field, tool_value, manifest_value FROM (
          SELECT a.key, 'size' AS field, a.size AS tool_value, m.size AS manifest_value
            FROM actual_u a JOIN manifest m USING (key)
           WHERE a.size <> '-' AND a.size <> m.size
          UNION ALL
          SELECT a.key, 'etag', a.etag, m.etag
            FROM actual_u a JOIN manifest m USING (key)
           WHERE a.etag <> '-' AND lower(a.etag) <> lower(m.etag)
          UNION ALL
          SELECT a.key, 'mtime', a.mtime, m.mtime
            FROM actual_u a JOIN manifest m USING (key)
           WHERE a.mtime <> '-' AND a.mtime <> m.mtime
          UNION ALL
          SELECT a.key, 'storage_class', a.storage_class, m.storage_class
            FROM actual_u a JOIN manifest m USING (key)
           WHERE a.storage_class <> '-' AND a.storage_class <> m.storage_class
        ) ORDER BY key, field
        """
    ).fetchall()

    return {
        "missing": [row[0] for row in missing],
        "extra": [row[0] for row in extra],
        "duplicates": [row[0] for row in duplicates],
        "mismatches": [
            {"key": key, "field": field, "tool": tool_value, "manifest": manifest_value}
            for key, field, tool_value, manifest_value in mismatches
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
            print(f"  mismatch[{m['field']}] {m['key']}: tool={m['tool']!r} manifest={m['manifest']!r}")
        remaining = len(diff["mismatches"]) - len(shown)
        if remaining > 0:
            print(f"  ... (+{remaining} more mismatches)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify one attempt's listing against a reference manifest.")
    parser.add_argument("--tool", required=True, choices=sorted(TOOLS))
    parser.add_argument("--bucket", required=True, help="Expected bucket; checked against the leaf's result.json.")
    parser.add_argument("--prefix", default="", help="Expected prefix; checked against the leaf's result.json.")
    parser.add_argument("--mode", required=True, help="Expected mode; checked against the leaf's result.json.")
    parser.add_argument(
        "--attempt-dir", required=True,
        help="Local path or gs:// prefix for the job's destination (parent of its attempt leaves).",
    )
    parser.add_argument("--manifest", required=True, help="Local reference manifest TSV.")
    parser.add_argument("--verify-output", default=None, help="Where to write verify.json (default: <leaf>/verify.json).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    leaf = resolve_leaf(args.attempt_dir)
    if leaf is None:
        return EXIT_AMBIGUOUS_LEAVES

    if not has_result_marker(leaf):
        print(f"verify: {leaf} has no result.json -- incomplete or torn attempt", file=sys.stderr)
        return EXIT_MISSING_MARKER

    is_remote = leaf.startswith("gs://")
    # A remote leaf only exists inside the temp dir below, so pick the output
    # path -- and write it -- before that directory is cleaned up.
    default_output = Path.cwd() / "verify.json" if is_remote else Path(leaf) / "verify.json"
    verify_output = Path(args.verify_output) if args.verify_output else default_output

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        attempt_dir = fetch_attempt_dir(leaf, work_dir)

        result = json.loads((attempt_dir / "result.json").read_text())
        mismatches = check_binding(result, args)
        if mismatches:
            print(f"verify: {leaf} is not the attempt expected for this case:", file=sys.stderr)
            for mismatch in mismatches:
                print(f"  {mismatch}", file=sys.stderr)
            return EXIT_BINDING_MISMATCH

        native_path = locate_native_output(attempt_dir, args.tool, work_dir)
        actual_tsv = work_dir / "actual.tsv"
        try:
            normalize_to_tsv(args.tool, native_path, actual_tsv)
        except FramingViolation as exc:
            print(f"verify: {exc}", file=sys.stderr)
            return EXIT_FRAMING_VIOLATION

        con = duckdb.connect()
        try:
            load_tables(con, Path(args.manifest), actual_tsv)
            diff = compute_diff(con)
        finally:
            con.close()

        verdict = verdict_for(diff)
        output = {
            "tool": args.tool,
            "leaf": leaf,
            "verdict": verdict,
            "manifest_sha256": sha256_of(Path(args.manifest)),
            "diff": diff,
        }
        verify_output.write_text(json.dumps(output, indent=2) + "\n")

    print(f"missing={len(diff['missing'])} extra={len(diff['extra'])} "
          f"duplicates={len(diff['duplicates'])} mismatches={len(diff['mismatches'])}")
    if verdict != "PASS":
        print_samples(diff)
    print(f"verdict={verdict}")

    return EXIT_CODES[verdict]


if __name__ == "__main__":
    sys.exit(main())
