"""Build a reference manifest from a live bucket listing, via aws-cli.

This is a SKETCH standing in for however the real committed manifests under
this study's reference lineage were built and bound to a bucket
(data/registry.toml pairs a bucket with its manifest; nothing here reproduces
that provenance or the validation that earned a manifest "reference" status).
It runs aws-cli once, normalizes through the same normalize_sql
tools/aws_cli.py uses to verify aws-cli's own attempts, sorts by key, and
writes a TSV plus a small "<output>.meta.json" describing what was built.
Reusing aws-cli's own normalizer to build the thing aws-cli is later checked
against is a real bias this sketch does not correct for -- a manifest built
this way cannot catch a bug aws-cli and its normalizer share.

Usage:
    manifest.py --bucket some-bucket --prefix "" --output manifest.tsv
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from tools import FramingViolation, assert_framing_safe
from tools.aws_cli import argv as aws_cli_argv
from tools.aws_cli import normalize_sql as aws_cli_normalize_sql

EXIT_FRAMING_VIOLATION = 6


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a reference manifest TSV from a live bucket listing.")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--output", required=True, help="Path to write the manifest TSV to.")
    return parser.parse_args(argv)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_path = Path(args.output)

    with tempfile.TemporaryDirectory() as tmp:
        native_path = Path(tmp) / "native.json"
        with open(native_path, "wb") as f:
            result = subprocess.run(aws_cli_argv(args.bucket, args.prefix), stdout=f, stderr=subprocess.PIPE)
        if result.returncode != 0:
            print(f"manifest: aws-cli failed: {result.stderr.decode(errors='replace')}", file=sys.stderr)
            return 1

        select_sql = aws_cli_normalize_sql(str(native_path))
        con = duckdb.connect()
        try:
            try:
                assert_framing_safe(con, select_sql)
            except FramingViolation as exc:
                print(f"manifest: {exc}", file=sys.stderr)
                return EXIT_FRAMING_VIOLATION
            # QUOTE ''/ESCAPE '': see verify.py's _COPY_OPTS -- the contract
            # TSV has no quoting dialect, so a literal '"' in a key or etag
            # must not be reinterpreted as a quote character on write.
            con.execute(
                f"COPY (SELECT * FROM ({select_sql}) ORDER BY key) TO '{output_path}' "
                "(DELIMITER '\t', HEADER false, QUOTE '', ESCAPE '')"
            )
            key_count = con.execute(f"SELECT count(*) FROM ({select_sql})").fetchone()[0]
        finally:
            con.close()

    meta = {
        "bucket": args.bucket,
        "prefix": args.prefix,
        "built_at": datetime.now(UTC).isoformat(),
        "key_count": key_count,
        "manifest_sha256": sha256_of(output_path),
    }
    meta_path = Path(str(output_path) + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"manifest: wrote {key_count} key(s) to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
