"""s3p: guessed CLI shape, not checked against a real run.

# approximate: assumes a --json flag on this Node-based tool, with guessed
camelCase field names.
"""

from __future__ import annotations

TOOL = "s3p"
NATIVE_FILE = "native.ndjson"

# Mirrors bench/tools.yaml's `heap:` block: a managed runtime (V8, here)
# needs to be told what share of its container's memory ceiling it may use
# as heap, since it otherwise defaults to a fraction of what it can see
# rather than the whole cgroup limit. See tools.heap_env_for.
HEAP_ENV = "NODE_OPTIONS"
HEAP_TEMPLATE = "--max-old-space-size={mib}"


def argv(bucket: str, prefix: str) -> list[str]:
    return ["s3p", "ls", f"s3://{bucket}/{prefix}", "--recursive", "--json"]


def normalize_sql(native_path: str) -> str:
    return f"""
        SELECT
            key AS key,
            CAST(size AS VARCHAR) AS size,
            replace(eTag, '"', '') AS etag,
            lastModified AS mtime,
            '-' AS storage_class
        FROM read_ndjson_auto('{native_path}')
    """
