#!/usr/bin/env python3
"""tools/ps3/adapter/normalize.py <mode> [prefix] — pS3 output adapter.

Reads pS3's raw stdout on stdin, writes contract v2 on stdout, one record per
line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

pS3's only object-emitting code path (``cmd/listObjectsV2.go readObjectsV2``,
[SRC readObjectsV2 @ 9428492]) prints, per object::

    Object: <LastModified> \t <size> \t <key>\n

where ``<LastModified>`` is a Go ``time.Time`` rendered with ``%v``, i.e.
``2006-01-02 15:04:05.999999999 -0700 MST`` — for example
``2020-05-01 12:00:00 +0000 UTC``. That is a regular line format, so the mode is
a SELECT: split on TAB, take the date and time head of field 1, and rebuild the
key from field 3 onward. Framing and validation stay in ``contract`` via
``emit_result``.

Only a line carrying the literal ``Object: `` sentinel is a record; pS3's other
stdout chatter is dropped.

Fields exposed: key, size, mtime. NOT exposed: etag and storage_class, so both
are `-`. The container runs ``TZ=UTC`` and S3 LastModified is UTC, so the printed
zone is UTC by construction and the instant is canonicalised to
``YYYY-MM-DDTHH:MM:SSZ``, dropping any fractional second (S3 is whole-second).

The printf format inserts exactly ONE space before the key, and only that single
separator is consumed — a genuine leading space in a key survives, which is why
this is not a trim. The remaining gap is an embedded NEWLINE (legal in S3): pS3
prints the raw key into a ``\\n``-terminated line, so a line-oriented reader
splits such a key. Here the fragment is refused at the emit boundary instead of
being emitted as a key the bucket does not hold.

``prefix`` (argv[2]) is accepted per the adapter contract and unused: pS3 emits
full keys and cannot scope by prefix, so nothing needs reconstructing. The
adapter runs on the HOST, AFTER the wrapper's clock stops.

Provenance and the mode set
---------------------------
pS3 cannot make unsigned requests and the campaign ran ``CREDS=none``, so no live
pS3 listing exists to exercise this adapter end to end; the committed receipt is
a capability probe whose stdout is a session-creation error and therefore
normalises to nothing. ``list-object-versions`` shares the same ``readObjectsV2``
printer in the shipped binary's ``--help`` surface, but its source is absent from
the pinned checkout, so its exact line format is UNVERIFIED and this adapter
assumes the list-objects-v2 shape for it.

The mode set is also closed, even though every mode shares one format and any
mode string could be accepted. An unknown mode is refused here, as it is by every
other adapter in the study: a mode nothing
declares is a verifier invoking something this port never reviewed, and answering
it with a confident 0 records would be a fabricated verdict. No committed payload
reaches that branch — the only receipt names ``list``.
"""

from __future__ import annotations

import sys
from typing import IO

from benchmark.runtime.duckdb_adapter import (
    connect,
    count_lf_lines,
    emit_result,
    staged,
)
from benchmark.runtime.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 2

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = frozenset({"list", "list-versions"})

LINES = "(SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))"

# Field 1 is "Object: <datetime>", field 2 the space-padded size, fields 3.. the
# key — reassembled with the TABs that split them, because an S3 key may contain
# one. `<datetime>` is "<date> <time>[.frac] <offset> <zone>"; the date and the
# whole-second time head are the instant.
QUERY = rf"""
    SELECT regexp_replace(array_to_string(list_slice(f, 3, len(f)), chr(9)), '^ ', ''),
           replace(f[2], ' ', ''),
           NULL,
           "dt"[1] || 'T' || regexp_replace("dt"[2], '\..*$', '') || 'Z',
           NULL
    FROM (SELECT f, str_split_regex(trim(substr(f[1], 9)), '[ \t]+') AS "dt"
          FROM (SELECT str_split(line, chr(9)) AS f
                FROM {LINES} WHERE starts_with(line, 'Object: ')))
"""


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    return count_lf_lines(data, lambda line: line.startswith(b"Object: "))


def normalize(out: IO[bytes], data: bytes, mode: str, prefix: str = "") -> int:
    if mode not in MODES:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    with staged(data) as path:
        emit_result(out, connect().execute(QUERY, {"path": path}))
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize, modes=MODES, prog="ps3 normalize", argv=argv, error_exit=UNKNOWN_MODE_EXIT
    )


if __name__ == "__main__":
    sys.exit(main())
