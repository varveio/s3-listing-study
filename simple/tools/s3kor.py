"""s3kor: guessed CLI shape, not checked against a real run.

# approximate: assumes a --json flag exists on this Go tool, with field names
mirroring aws-cli's capitalization since s3kor is built on the same AWS Go SDK.
"""

from __future__ import annotations

TOOL = "s3kor"
NATIVE_FILE = "native.ndjson"


def argv(bucket: str, prefix: str) -> list[str]:
    return ["s3kor", "list", "-b", bucket, "-p", prefix, "--json"]


def normalize_sql(native_path: str) -> str:
    return f"""
        SELECT
            Key AS key,
            CAST(Size AS VARCHAR) AS size,
            replace(ETag, '"', '') AS etag,
            LastModified AS mtime,
            coalesce(StorageClass, 'STANDARD') AS storage_class
        FROM read_ndjson_auto('{native_path}')
    """
