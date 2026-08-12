"""swath: guessed CLI shape, not checked against a real run.

# approximate: guessed column order for the recursive-tsv mode.

Real swath also has a Parquet-sink mode (recursive-parquet) that refuses to
stream -- it writes a Parquet dataset file the caller points it at, rather
than stdout. That mode is registered as a second TOOLS entry, "swath-parquet"
(see tools/__init__.py), exposed here as PARQUET_TOOL/parquet_argv/
parquet_normalize_sql/PARQUET_NATIVE rather than a separate module, since
it's the same binary and worth keeping next to its stdout-mode sibling.
"""

from __future__ import annotations

TOOL = "swath"
NATIVE_FILE = "native.tsv"

# Mirrors bench/tools.yaml's `heap:` block: a managed runtime (a JVM, here)
# needs to be told what share of its container's memory ceiling it may use
# as heap. See tools.heap_env_for.
HEAP_ENV = "JAVA_TOOL_OPTIONS"
HEAP_TEMPLATE = "-XX:MaxRAMPercentage={percent}"

PARQUET_TOOL = "swath-parquet"
PARQUET_NATIVE = "output.parquet"


def argv(bucket: str, prefix: str) -> list[str]:
    return ["swath", "list", "--recursive", "--tsv", "--bucket", bucket, "--prefix", prefix]


def normalize_sql(native_path: str) -> str:
    return f"""
        SELECT
            column0 AS key,
            column1 AS size,
            replace(column2, '"', '') AS etag,
            column3 AS mtime,
            column4 AS storage_class
        FROM read_csv('{native_path}', delim='\\t', header=false)
    """


def parquet_argv(bucket: str, prefix: str, attempt_dir: str) -> list[str]:
    return [
        "swath", "list", "--recursive", "--parquet",
        "--bucket", bucket, "--prefix", prefix,
        "--output", f"{attempt_dir}/output.parquet",
    ]


def parquet_normalize_sql(native_path: str) -> str:
    # DuckDB reads Parquet natively, so unlike the CSV-based normalizers
    # above there is no delimiter/quote dialect to get wrong here -- just
    # guessed Parquet column names.
    return f"""
        SELECT
            key AS key,
            CAST(size AS VARCHAR) AS size,
            replace(etag, '"', '') AS etag,
            mtime AS mtime,
            coalesce(storage_class, 'STANDARD') AS storage_class
        FROM read_parquet('{native_path}')
    """
