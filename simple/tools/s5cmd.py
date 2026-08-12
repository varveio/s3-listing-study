"""s5cmd: `s5cmd --json ls s3://bucket/prefix*`.

Checked against the real tool's documented output shape -- one of the four
"right-ish" tools (see README.md's "Which tools are checked vs. guessed").
"""

from __future__ import annotations

TOOL = "s5cmd"
NATIVE_FILE = "native.ndjson"


def argv(bucket: str, prefix: str) -> list[str]:
    # The trailing '*' forces recursive listing; --json emits one JSON object
    # per line rather than s5cmd's human table.
    return ["s5cmd", "--json", "ls", f"s3://{bucket}/{prefix}*"]


def normalize_sql(native_path: str) -> str:
    return f"""
        SELECT
            key AS key,
            CAST(size AS VARCHAR) AS size,
            replace(etag, '"', '') AS etag,
            last_modified AS mtime,
            '-' AS storage_class
        FROM read_ndjson_auto('{native_path}')
        WHERE type = 'file'
    """
