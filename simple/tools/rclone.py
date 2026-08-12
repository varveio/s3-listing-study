"""rclone: `rclone lsjson --recursive s3:bucket/prefix`.

Checked against the real tool's documented output shape -- one of the four
"right-ish" tools (see README.md's "Which tools are checked vs. guessed").
"""

from __future__ import annotations

TOOL = "rclone"
NATIVE_FILE = "native.json"


def argv(bucket: str, prefix: str) -> list[str]:
    return ["rclone", "lsjson", "--recursive", f"s3:{bucket}/{prefix}"]


def normalize_sql(native_path: str) -> str:
    # rclone's S3 backend does not reliably surface the raw ETag or storage
    # class through lsjson, so both come through as unexposed.
    return f"""
        SELECT
            Path AS key,
            CAST(Size AS VARCHAR) AS size,
            '-' AS etag,
            ModTime AS mtime,
            '-' AS storage_class
        FROM read_json_auto('{native_path}')
        WHERE NOT IsDir
    """
