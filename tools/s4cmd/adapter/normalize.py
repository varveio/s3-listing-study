#!/usr/bin/env python3
"""tools/s4cmd/adapter/normalize.py <mode> [prefix] — s4cmd output adapter.

Reads s4cmd's raw ``ls`` output on stdin, writes contract v2 on stdout, one
record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

The output is aligned, space-padded columns, which is still a SELECT: the key is
addressed from the ``s3://`` the URL starts at, and the size is the last token of
what precedes it. Framing and validation stay in ``contract`` via
``emit_result``.

Output shape (``s4cmd.py`` ``pretty_print``, ~line 1592)::

    "<mtime> <size> <name>"

* ``<name>`` is the LAST column, left-justified, and always a full
  ``s3://<bucket>/<key>`` URL — so the key is absolute and ``prefix`` (argv[2]) is
  accepted per the adapter contract but not needed to reconstruct it.
* ``<size>`` is the object size in bytes, or the literal ``DIR`` for a directory
  (CommonPrefix) entry, which carries no size at all.
* ``<mtime>`` is ``TIMESTAMP_FORMAT = "%04d-%02d-%02d %02d:%02d"``
  (``s4cmd.py:55``) — MINUTE precision, no seconds and no zone marker. It is UTC
  only because botocore hands ``pretty_print`` a tz-aware UTC datetime whose
  fields are formatted as-is (``s4cmd.py:1602``; ``TZ`` does not affect it). The
  SECOND is not exposed, so the contract's canonical ``…:SSZ`` value is NOT
  derivable and mtime is `-` — a rounded second would be a fabricated instant.
* etag and storage_class are never printed by ``ls``, so both are `-`.

``du`` is a listing-REQUEST mode with no per-object output: it emits an aggregate
size, so there is nothing to normalise and it exits clean with no records.

LOSSY-KEY CAVEAT (tool-side, not adapter-side): s4cmd ``rstrip()``s each output
line (``s4cmd.py:1622``), so a key with TRAILING whitespace loses it before this
adapter ever sees it, and a key containing a NEWLINE is split across lines by the
line-oriented formatter. Such keys cannot be faithfully normalised by anything
downstream — a limit of the tool's output, recorded so it is not mistaken for
adapter fidelity. The adapter-side half of the same hazard is avoided: taking
byte positions with ``substr`` and no ``LC_ALL=C`` would cut a multi-byte key
mid-character, so the key is carried as text to the emit boundary instead, and a
key the framing cannot carry is refused rather than mangled.

The adapter runs on the HOST, AFTER the wrapper's clock stops, so a DuckDB query
is fair game here — never inside a timed window.
"""

from __future__ import annotations

import sys
from typing import IO

from s3_listing_study.manager.duckdb_adapter import (
    connect,
    count_lf_lines,
    emit_result,
    staged,
)
from s3_listing_study.manager.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 2

LISTING_MODES = frozenset({"recursive", "shallow", "show-directory"})

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = LISTING_MODES | {"du"}

LINES = "(SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))"

# The URL is the whole remainder of the line from its first `s3://`, so a key
# containing spaces survives; everything before it is the aligned `<mtime> <size>`
# gutter, whose last whitespace token is the size. A line with no URL is not a
# record, and neither is a bucket-root URL with no key after the bucket segment.
QUERY = rf"""
    SELECT "key",
           CASE WHEN "size" = 'DIR' THEN NULL ELSE nullif("size", '') END,
           NULL, NULL, NULL
    FROM (SELECT substr("rest", position('/' IN "rest") + 1) AS "key",
                 "gutter"[-1] AS "size"
          FROM (SELECT substr(line, position('s3://' IN line) + 5) AS "rest",
                       str_split_regex(trim(substr(line, 1, position('s3://' IN line) - 1)),
                                       '[ \t]+') AS "gutter"
                FROM {LINES} WHERE position('s3://' IN line) > 0)
          WHERE position('/' IN "rest") > 0)
    WHERE "key" <> ''
"""


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    if mode == "du":
        return 0
    if mode not in LISTING_MODES:
        raise ValueError(f"unknown mode: {mode}")

    def selected(line: bytes) -> bool:
        marker = line.find(b"s3://")
        if marker < 0:
            return False
        rest = line[marker + 5 :]
        slash = rest.find(b"/")
        return slash >= 0 and bool(rest[slash + 1 :])

    return count_lf_lines(data, selected)


def normalize(out: IO[bytes], data: bytes, mode: str, prefix: str = "") -> int:
    if mode == "du":
        print(
            "normalize.py: mode 'du' emits an aggregate size, not a per-key listing; "
            "nothing to normalize",
            file=sys.stderr,
        )
        return 0
    if mode not in LISTING_MODES:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    with staged(data) as path:
        emit_result(out, connect().execute(QUERY, {"path": path}))
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize, modes=MODES, prog="s4cmd normalize", argv=argv, error_exit=UNKNOWN_MODE_EXIT
    )


if __name__ == "__main__":
    sys.exit(main())
