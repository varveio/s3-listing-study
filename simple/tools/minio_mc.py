"""minio-mc: `mc ls --recursive --json bucket/prefix`.

Checked against the real tool's documented output shape -- one of the four
"right-ish" tools (see README.md's "Which tools are checked vs. guessed").
"""

from __future__ import annotations

TOOL = "minio-mc"
NATIVE_FILE = "native.ndjson"


def argv(bucket: str, prefix: str) -> list[str]:
    return ["mc", "ls", "--recursive", "--json", f"{bucket}/{prefix}"]


def normalize_sql(native_path: str) -> str:
    return f"""
        SELECT
            key AS key,
            CAST(size AS VARCHAR) AS size,
            replace(etag, '"', '') AS etag,
            lastModified AS mtime,
            coalesce(storageClass, 'STANDARD') AS storage_class
        FROM read_ndjson_auto('{native_path}')
        WHERE type = 'file'
    """
