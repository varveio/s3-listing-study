"""s3-fast-list: guessed CLI shape, not checked against a real run.

# approximate: the real tool writes its own TAB-delimited file directly, and
its native output is assumed already close to contract shape (key, size,
etag, mtime), just missing storage_class.
"""

from __future__ import annotations

TOOL = "s3-fast-list"
NATIVE_FILE = "native.tsv"


def argv(bucket: str, prefix: str) -> list[str]:
    return ["s3-fast-list", "--bucket", bucket, "--prefix", prefix, "--output", "-"]


def normalize_sql(native_path: str) -> str:
    return f"""
        SELECT
            column0 AS key,
            column1 AS size,
            replace(column2, '"', '') AS etag,
            column3 AS mtime,
            '-' AS storage_class
        FROM read_csv('{native_path}', delim='\\t', header=false)
    """
