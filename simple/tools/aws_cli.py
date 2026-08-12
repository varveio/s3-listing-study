"""aws-cli: `aws s3api list-objects-v2 --output json`.

Checked against the real tool's documented output shape (see README.md's
"Which tools are checked vs. guessed") -- one of the four "right-ish" tools.
"""

from __future__ import annotations

TOOL = "aws-cli"
NATIVE_FILE = "native.json"


def argv(bucket: str, prefix: str) -> list[str]:
    # High-level `aws s3api list-objects-v2` auto-paginates by default and
    # accumulates every page into one JSON document under "Contents".
    return [
        "aws", "s3api", "list-objects-v2",
        "--bucket", bucket, "--prefix", prefix,
        "--output", "json",
    ]


def normalize_sql(native_path: str) -> str:
    return f"""
        SELECT
            c.Key AS key,
            CAST(c.Size AS VARCHAR) AS size,
            replace(c.ETag, '"', '') AS etag,
            c.LastModified AS mtime,
            coalesce(c.StorageClass, 'STANDARD') AS storage_class
        FROM (
            SELECT unnest(Contents) AS c
            FROM read_json_auto('{native_path}')
        )
    """
