"""One dict per tool: the listing argv, and a DuckDB SQL SELECT that turns the
tool's native output into the five contract columns (key, size, etag, mtime,
storage_class), TAB-separated, "-" for anything the mode does not expose.

This is a SKETCH. The real adapters (src/s3_listing_study/worker/adapters/)
each get a whole module, handle pagination edge cases, and are covered by a
byte-identical acceptance test against a frozen manifest. Here every SQL
string just assumes a plausible native shape for the tool and reads it with
DuckDB's JSON/CSV auto-readers. Entries marked `# approximate` are a guess at
the tool's real output shape and would need to be checked against an actual
run before trusting them for anything beyond "the shape of a normalizer".

Every normalize_sql() takes the path to the native output file and returns a
query whose result set is (key, size, etag, mtime, storage_class) as VARCHAR.
verify.py runs it and writes the result out as a TSV.
"""

from __future__ import annotations


def _s3_uri(bucket: str, prefix: str) -> str:
    return f"s3://{bucket}/{prefix}"


# --- aws-cli -----------------------------------------------------------

def aws_cli_argv(bucket: str, prefix: str) -> list[str]:
    # High-level `aws s3api list-objects-v2` auto-paginates by default and
    # accumulates every page into one JSON document under "Contents".
    return [
        "aws", "s3api", "list-objects-v2",
        "--bucket", bucket, "--prefix", prefix,
        "--output", "json",
    ]


def aws_cli_normalize_sql(native_path: str) -> str:
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


# --- s5cmd ---------------------------------------------------------------

def s5cmd_argv(bucket: str, prefix: str) -> list[str]:
    # The trailing '*' forces recursive listing; --json emits one JSON object
    # per line rather than s5cmd's human table.
    return ["s5cmd", "--json", "ls", _s3_uri(bucket, prefix) + "*"]


def s5cmd_normalize_sql(native_path: str) -> str:
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


# --- rclone ----------------------------------------------------------------

def rclone_argv(bucket: str, prefix: str) -> list[str]:
    return ["rclone", "lsjson", "--recursive", f"s3:{bucket}/{prefix}"]


def rclone_normalize_sql(native_path: str) -> str:
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


# --- minio-mc ----------------------------------------------------------

def minio_mc_argv(bucket: str, prefix: str) -> list[str]:
    return ["mc", "ls", "--recursive", "--json", f"{bucket}/{prefix}"]


def minio_mc_normalize_sql(native_path: str) -> str:
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


# --- s7cmd -------------------------------------------------------------

def s7cmd_argv(bucket: str, prefix: str) -> list[str]:
    # approximate: assumes a --tsv flag shaped like s5cmd's table output.
    return ["s7cmd", "ls", "--recursive", "--tsv", _s3_uri(bucket, prefix)]


def s7cmd_normalize_sql(native_path: str) -> str:
    # approximate: guessed column order (key, size, etag, mtime); no header.
    return f"""
        SELECT
            column0 AS key,
            column1 AS size,
            replace(column2, '"', '') AS etag,
            column3 AS mtime,
            '-' AS storage_class
        FROM read_csv('{native_path}', delim='\\t', header=false)
    """


# --- s3-fast-list --------------------------------------------------------

def s3_fast_list_argv(bucket: str, prefix: str) -> list[str]:
    # approximate: the real tool writes its own TAB-delimited file directly.
    return ["s3-fast-list", "--bucket", bucket, "--prefix", prefix, "--output", "-"]


def s3_fast_list_normalize_sql(native_path: str) -> str:
    # approximate: assumes the native output is already close to contract
    # shape (key, size, etag, mtime), just missing storage_class.
    return f"""
        SELECT
            column0 AS key,
            column1 AS size,
            replace(column2, '"', '') AS etag,
            column3 AS mtime,
            '-' AS storage_class
        FROM read_csv('{native_path}', delim='\\t', header=false)
    """


# --- s3kor -----------------------------------------------------------------

def s3kor_argv(bucket: str, prefix: str) -> list[str]:
    # approximate: assumes a --json flag exists on this Go tool.
    return ["s3kor", "list", "-b", bucket, "-p", prefix, "--json"]


def s3kor_normalize_sql(native_path: str) -> str:
    # approximate: guessed field names, mirroring aws-cli's capitalization
    # since s3kor is built on the same AWS Go SDK.
    return f"""
        SELECT
            Key AS key,
            CAST(Size AS VARCHAR) AS size,
            replace(ETag, '"', '') AS etag,
            LastModified AS mtime,
            coalesce(StorageClass, 'STANDARD') AS storage_class
        FROM read_ndjson_auto('{native_path}')
    """


# --- s4cmd -------------------------------------------------------------

def s4cmd_argv(bucket: str, prefix: str) -> list[str]:
    return ["s4cmd", "ls", "-r", _s3_uri(bucket, prefix)]


def s4cmd_normalize_sql(native_path: str) -> str:
    # approximate: s4cmd prints a fixed-width text table (size, date, time,
    # uri); the real adapter cuts this by byte position and, per
    # common/contract.py, that historically split multi-byte keys mid-
    # character without LC_ALL=C. Here we just split on whitespace, which is
    # wrong for keys containing spaces -- illustrative only.
    return f"""
        SELECT
            column3 AS key,
            column0 AS size,
            '-' AS etag,
            column1 || 'T' || column2 || 'Z' AS mtime,
            '-' AS storage_class
        FROM read_csv('{native_path}', delim=' ', header=false)
    """


# --- s3p -------------------------------------------------------------------

def s3p_argv(bucket: str, prefix: str) -> list[str]:
    # approximate: assumes a --json flag on this Node-based tool.
    return ["s3p", "ls", _s3_uri(bucket, prefix), "--recursive", "--json"]


def s3p_normalize_sql(native_path: str) -> str:
    # approximate: guessed camelCase field names.
    return f"""
        SELECT
            key AS key,
            CAST(size AS VARCHAR) AS size,
            replace(eTag, '"', '') AS etag,
            lastModified AS mtime,
            '-' AS storage_class
        FROM read_ndjson_auto('{native_path}')
    """


# --- ps3 -----------------------------------------------------------------

def ps3_argv(bucket: str, prefix: str) -> list[str]:
    return ["ps3", "ls", _s3_uri(bucket, prefix)]


def ps3_normalize_sql(native_path: str) -> str:
    # approximate: assumed to print one bare key per line, nothing else.
    return f"""
        SELECT
            column0 AS key,
            '-' AS size,
            '-' AS etag,
            '-' AS mtime,
            '-' AS storage_class
        FROM read_csv('{native_path}', delim='\\t', header=false, columns={{'column0': 'VARCHAR'}})
    """


# --- swath -----------------------------------------------------------------

def swath_argv(bucket: str, prefix: str) -> list[str]:
    # approximate: real swath also has a Parquet-sink mode (recursive-parquet);
    # this sketch only covers the recursive-tsv mode.
    return ["swath", "list", "--recursive", "--tsv", "--bucket", bucket, "--prefix", prefix]


def swath_normalize_sql(native_path: str) -> str:
    # approximate: guessed column order for the tsv mode.
    return f"""
        SELECT
            column0 AS key,
            column1 AS size,
            replace(column2, '"', '') AS etag,
            column3 AS mtime,
            column4 AS storage_class
        FROM read_csv('{native_path}', delim='\\t', header=false)
    """


TOOLS = {
    "aws-cli": {"argv": aws_cli_argv, "normalize_sql": aws_cli_normalize_sql, "native_file": "native.json"},
    "s5cmd": {"argv": s5cmd_argv, "normalize_sql": s5cmd_normalize_sql, "native_file": "native.ndjson"},
    "rclone": {"argv": rclone_argv, "normalize_sql": rclone_normalize_sql, "native_file": "native.json"},
    "minio-mc": {"argv": minio_mc_argv, "normalize_sql": minio_mc_normalize_sql, "native_file": "native.ndjson"},
    "s7cmd": {"argv": s7cmd_argv, "normalize_sql": s7cmd_normalize_sql, "native_file": "native.tsv"},
    "s3-fast-list": {
        "argv": s3_fast_list_argv,
        "normalize_sql": s3_fast_list_normalize_sql,
        "native_file": "native.tsv",
    },
    "s3kor": {"argv": s3kor_argv, "normalize_sql": s3kor_normalize_sql, "native_file": "native.ndjson"},
    "s4cmd": {"argv": s4cmd_argv, "normalize_sql": s4cmd_normalize_sql, "native_file": "native.txt"},
    "s3p": {"argv": s3p_argv, "normalize_sql": s3p_normalize_sql, "native_file": "native.ndjson"},
    "ps3": {"argv": ps3_argv, "normalize_sql": ps3_normalize_sql, "native_file": "native.txt"},
    "swath": {"argv": swath_argv, "normalize_sql": swath_normalize_sql, "native_file": "native.tsv"},
}
"""Every tool argv builder takes (bucket, prefix); every normalize_sql takes
the path DuckDB should read the tool's stdout (or native output file) from.
"native_file" is only a naming convention measure.py uses when a tool writes
to a file instead of stdout -- the sketch does not distinguish stream-vs-sink
modes the way the real engine's NATIVE_DIRECTORY handling does.
"""


HEAP_ENV = {
    # Mirrors bench/tools.yaml's `heap:` block: the two tools with a managed
    # runtime need to be told what share of their container's memory ceiling
    # they may use as heap, because a JVM/V8 default to a fraction of what
    # they can see rather than the whole cgroup limit.
    "swath": {"env": "JAVA_TOOL_OPTIONS", "template": "-XX:MaxRAMPercentage={percent}"},
    "s3p": {"env": "NODE_OPTIONS", "template": "--max-old-space-size={mib}"},
}

HEAP_PERCENT = 75


def heap_env_for(tool: str, container_memory_mib: int | None) -> tuple[str, str] | None:
    """The one (name, value) environment pair `tool` needs sized to its container
    ceiling, or None for the nine tools with no heap to size.

    Real bench/tools.yaml substitutes both {percent} (JVM) and {mib} (V8) per
    case; a tool only ever uses one of the two placeholders in its template.
    """
    spec = HEAP_ENV.get(tool)
    if spec is None or container_memory_mib is None:
        return None
    value = spec["template"].format(percent=HEAP_PERCENT, mib=int(container_memory_mib * HEAP_PERCENT / 100))
    return spec["env"], value


if __name__ == "__main__":
    # Not a test -- just a quick eyeball of what each tool's argv looks like
    # for a sample bucket, useful when sketching out a new plan by hand.
    for name in sorted(TOOLS):
        print(name, TOOLS[name]["argv"]("example-bucket", "some/prefix/"))

