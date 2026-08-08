#!/usr/bin/env python3
"""tools/aws-cli/adapter/normalize.py <mode> [prefix] — aws-cli output adapter.

Reads one aws-cli listing mode's RAW output on stdin, writes contract v2 on
stdout, one record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

Six of the seven modes write something DuckDB reads directly — TSV for the s3api
text family, JSON for the s3api JSON family, plain lines for ``s3 ls`` — so those
are a SELECT producing the five contract columns, not a parser, with framing and
validation left to ``contract`` via ``emit_result``. ``s3api-v2-yamlstream`` is
the one exception: a multi-document YAML stream, which DuckDB does not read.

mtime is canonicalised to ``YYYY-MM-DDTHH:MM:SSZ``. Containers run ``TZ=UTC``, so
a mode printing a timezone-less local time is printing UTC by construction and we
stamp the ``Z``. The ETag is emitted UNQUOTED (S3 wraps it in literal quotes).

``prefix`` (argv[2]) is the scope the run used, passed by the verifier from
``run.meta``. The s3api commands and ``s3 ls --recursive`` print FULL keys and
ignore it; only the non-recursive ``s3 ls`` prints path-relative names. The
adapter runs on the HOST, AFTER the wrapper's clock stops, so a DuckDB query and a
YAML parse are fair game here — never inside a timed window.

Ragged rows: the one deliberate deviation
-----------------------------------------
A text-family row carrying fewer than five TAB fields, which only a TRUNCATED
payload produces, has no faithful reading. An awk-style ``$5`` of a 4-field row
is the empty string, which would emit a record whose storage_class is neither a
value nor the ``-`` that means "unexposed". This adapter refuses the payload
instead (non-zero exit, which the
verifier reports as ERROR). That is the deviation, taken on purpose: an ERROR
says "no verdict was formed", which is the truth about a truncated listing,
whereas the empty field is a fabricated record. The verifier already refuses a
run.meta that records a truncated stream, so nothing reaches this branch by any
route the study currently has. ``tests/test_adapters.py`` pins both halves.
"""

from __future__ import annotations

import datetime
import re
import sys
from typing import IO, Any

from s3_listing_study.contract import UNEXPOSED, emit
from s3_listing_study.duckdb_adapter import connect, emit_result, staged
from s3_listing_study.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 2

# The s3api text family: `--query Contents[]/Versions[].[Key,Size,ETag,
# LastModified,StorageClass] --output text`. Same 5-column TSV for all four; only
# the request that produced it differs.
TEXT_MODES = frozenset(
    {"s3api-v2-text", "s3api-v1-text", "s3api-versions-text", "s3api-v2-remainder"}
)

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = TEXT_MODES | {
    "s3api-v2-json",
    "s3api-v2-delimiter",
    "s3api-v2-yamlstream",
    "s3-ls-recursive",
    "s3-ls-delimiter",
}

# botocore serialises LastModified with an explicit UTC offset (`+00:00`, or
# `+0000` from an older serialiser). The contract accepts all three spellings; the
# committed verdicts were issued against `Z`.
ZULU = r"regexp_replace({}, '\+00:?00$', 'Z')"

# One buffered JSON body merged across all pages (FullyBufferedFormatter). Columns
# are declared, not sniffed, so a field absent from every object — StorageClass on
# a listing that exposes none, CommonPrefixes without --delimiter — is NULL rather
# than a column that does not exist. The size limit is raised off its 16 MB
# default because a full-bucket listing is ONE object.
S3API_JSON = """read_json(
    $path,
    maximum_object_size = 1073741824,
    columns = {
        'Contents': 'STRUCT("Key" VARCHAR, "Size" BIGINT, "ETag" VARCHAR,
                            "LastModified" VARCHAR, "StorageClass" VARCHAR)[]',
        'CommonPrefixes': 'STRUCT("Prefix" VARCHAR)[]'})"""

# `s3 ls` prints `YYYY-MM-DD HH:MM:SS <right-aligned size> <key>`. The key is the
# whole remainder, so one containing runs of spaces survives intact.
LS_OBJECT = r"""regexp_extract(line,
    '^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) +(\d+) (.*)$',
    ['d', 't', 'size', 'key'])"""

LINES = "(SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))"

QUERIES = {
    # Rows are already the five contract fields; the only repairs are the literal
    # quotes around the ETag and the timestamp offset. quote and escape are
    # disabled because a bare `"` in the ETag column is data, not framing.
    #
    # auto_detect = false: with every column and the whole dialect declared there
    # is nothing left to sniff, and the sniffer refuses an EMPTY payload (it reads
    # zero columns where five are declared). An empty payload is not a corner
    # case — a `--scope prefix` run over a prefix holding no objects, or a fan-out
    # whose shards cover every key, produces one, and the shell adapter answers it
    # with a clean 0-row exit. See "Ragged rows" above for what stays refused.
    "text": f"""
        SELECT "key", "size", replace("etag", '"', ''), {ZULU.format('"mtime"')}, "storage_class"
        FROM read_csv($path, delim = '\t', quote = '', escape = '', header = false,
                      auto_detect = false,
                      columns = {{'key': 'VARCHAR', 'size': 'VARCHAR', 'etag': 'VARCHAR',
                                 'mtime': 'VARCHAR', 'storage_class': 'VARCHAR'}})
    """,
    "s3api-contents": f"""
        SELECT c."Key", CAST(c."Size" AS VARCHAR), replace(c."ETag", '"', ''),
               {ZULU.format('c."LastModified"')}, c."StorageClass"
        FROM (SELECT unnest("Contents") AS c FROM {S3API_JSON})
    """,
    # Under --delimiter / the body also carries CommonPrefixes: rollups with no
    # object behind them, so every value field is `-`.
    "s3api-common-prefixes": f"""
        SELECT p."Prefix", NULL, NULL, NULL, NULL
        FROM (SELECT unnest("CommonPrefixes") AS p FROM {S3API_JSON})
    """,
    # `s3 ls --recursive`: full keys (basename display off), exposing key, size
    # and a timezone-less UTC instant; no ETag and no storage class.
    "s3-ls-recursive": f"""
        SELECT o.key, o.size, NULL, o.d || 'T' || o.t || 'Z', NULL
        FROM (SELECT {LS_OBJECT} AS o FROM {LINES}) WHERE o.d <> ''
    """,
    # `s3 ls` non-recursive: `PRE <name>/` rollups interleaved with keys, in file
    # order. Upstream `_display_page` prints only the LAST path component
    # (`Key.split('/')[-1]` for objects, `Prefix.split('/')[-2]` for common
    # prefixes) [SRC subcommands.py:865-889]. The full key is therefore the
    # DIRECTORY portion of the run prefix — up to and including its last '/',
    # empty if the prefix has none — plus the printed name, and NOT the whole
    # prefix: prepending a non-'/'-terminated prefix `foo` to key `foobar.txt`
    # would wrongly yield `foofoobar.txt`. A root run's prefix is empty, so its
    # directory portion is too.
    "s3-ls-delimiter": rf"""
        SELECT $dir || CASE WHEN pre <> '' THEN pre ELSE o.key END,
               CASE WHEN pre = '' THEN o.size END,
               NULL,
               CASE WHEN pre = '' THEN o.d || 'T' || o.t || 'Z' END,
               NULL
        FROM (SELECT regexp_extract(line, '^ *PRE (.*)$', 1) AS pre, {LS_OBJECT} AS o
              FROM {LINES})
        WHERE pre <> '' OR o.d <> ''
    """,
}


def emit_yamlstream(out: IO[bytes], data: bytes) -> None:
    """s3api ``--output yaml-stream`` (``StreamedYAMLFormatter``) — the hand-parsed mode.

    One YAML document per page, streamed; DuckDB has no multi-document YAML reader.
    The paginated result serialises as a LIST of per-page dicts (a bare dict is
    tolerated), and ``LastModified`` comes back as a ``datetime`` or a string
    depending on how the page was serialised. ``yaml`` is imported in this branch
    alone, so the other six modes still normalise without pyyaml.
    """
    import yaml

    def stamp(value: object) -> str:
        if value is None:
            return UNEXPOSED
        if isinstance(value, datetime.datetime):
            return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        stamp = str(value)
        stamp = re.sub(r"(?<=\d{2}:\d{2}:\d{2})\.\d+(?=(?:Z|\+00:?00)$)", "", stamp)
        return stamp.replace("+00:00", "Z").replace("+0000", "Z")

    def pages(doc: object) -> list[dict[str, Any]]:
        if isinstance(doc, list):
            return [page for page in doc if isinstance(page, dict)]
        return [doc] if isinstance(doc, dict) else []

    for doc in yaml.safe_load_all(data):
        for page in pages(doc) if doc else []:
            for obj in page.get("Contents") or []:
                storage_class = obj.get("StorageClass")
                emit(
                    out,
                    str(obj.get("Key", "")).encode("utf-8"),
                    size=str(obj.get("Size", UNEXPOSED)),
                    etag=str(obj.get("ETag") or UNEXPOSED).replace('"', ""),
                    mtime=stamp(obj.get("LastModified")),
                    storage_class=str(storage_class) if storage_class else UNEXPOSED,
                )


def normalize(out: IO[bytes], data: bytes, mode: str, prefix: str) -> int:
    if mode == "s3api-v2-yamlstream":
        emit_yamlstream(out, data)
        return 0
    if mode in TEXT_MODES:
        queries = [QUERIES["text"]]
    elif mode == "s3api-v2-json":
        queries = [QUERIES["s3api-contents"]]
    elif mode == "s3api-v2-delimiter":
        # CommonPrefixes ahead of the root-level Contents: the order the committed
        # verdicts were issued in. Two statements rather than a UNION ALL, because
        # that order IS the output and no set operator needs to be trusted with it.
        queries = [QUERIES["s3api-common-prefixes"], QUERIES["s3api-contents"]]
    elif mode in QUERIES:
        queries = [QUERIES[mode]]
    else:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT

    directory = prefix[: prefix.rindex("/") + 1] if "/" in prefix else ""
    with staged(data) as path:
        connection = connect()
        for sql in queries:
            # DuckDB rejects a named parameter the statement does not reference,
            # and only the delimiter listing reconstructs a key.
            params = {"path": path} | ({"dir": directory} if "$dir" in sql else {})
            emit_result(out, connection.execute(sql, params))
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize, modes=MODES, prog="aws-cli normalize", argv=argv, error_exit=UNKNOWN_MODE_EXIT
    )


if __name__ == "__main__":
    sys.exit(main())
