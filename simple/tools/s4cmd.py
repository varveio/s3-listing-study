"""s4cmd: guessed CLI shape, not checked against a real run.

# approximate: s4cmd prints a fixed-width text table (size, date, time, uri);
the real adapter cuts this by byte position and, per common/contract.py,
that historically split multi-byte keys mid-character without LC_ALL=C. Here
we just split on whitespace, which is wrong for keys containing spaces --
illustrative only.
"""

from __future__ import annotations

TOOL = "s4cmd"
NATIVE_FILE = "native.txt"


def argv(bucket: str, prefix: str) -> list[str]:
    return ["s4cmd", "ls", "-r", f"s3://{bucket}/{prefix}"]


def normalize_sql(native_path: str) -> str:
    return f"""
        SELECT
            column3 AS key,
            column0 AS size,
            '-' AS etag,
            column1 || 'T' || column2 || 'Z' AS mtime,
            '-' AS storage_class
        FROM read_csv('{native_path}', delim=' ', header=false)
    """
