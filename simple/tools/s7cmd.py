"""s7cmd: guessed CLI shape, not checked against a real run.

# approximate: assumes a --tsv flag shaped like s5cmd's table output, and a
guessed column order (key, size, etag, mtime); no header.
"""

from __future__ import annotations

TOOL = "s7cmd"
NATIVE_FILE = "native.tsv"


def argv(bucket: str, prefix: str) -> list[str]:
    return ["s7cmd", "ls", "--recursive", "--tsv", f"s3://{bucket}/{prefix}"]


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
