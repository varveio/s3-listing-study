"""ps3: guessed CLI shape, not checked against a real run.

# approximate: assumed to print one bare key per line, nothing else.
"""

from __future__ import annotations

TOOL = "ps3"
NATIVE_FILE = "native.txt"


def argv(bucket: str, prefix: str) -> list[str]:
    return ["ps3", "ls", f"s3://{bucket}/{prefix}"]


def normalize_sql(native_path: str) -> str:
    return f"""
        SELECT
            column0 AS key,
            '-' AS size,
            '-' AS etag,
            '-' AS mtime,
            '-' AS storage_class
        FROM read_csv('{native_path}', delim='\\t', header=false, columns={{'column0': 'VARCHAR'}})
    """
