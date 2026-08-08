#!/usr/bin/env python3
"""tools/s3kor/adapter/normalize.py <mode> [prefix] — s3kor output adapter.

Reads s3kor's raw stdout for one mode on stdin, writes contract v2 on stdout, one
record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

Both modes are a line per entry, so both are a SELECT. Framing and validation
stay in ``contract`` via ``emit_result``.

s3kor's ``ls`` output contract (v0.0.37, ``list.go printAllObjects``)::

    list           one line per object: the FULL object key, nothing else.
    list-versions  one line per version: "<versionId> <key>", one space between.

Neither mode exposes size, etag, mtime or storage_class — every one of them is
`-`. In ``list-versions`` the version id is dropped (contract v2 has no version
axis) by taking everything after the FIRST space, so a key containing spaces
survives intact; a line with no space at all is not a record.

CAVEAT (``list-versions``): ListObjectVersions returns every version AND every
delete marker, so dropping the version id makes this mode comparable to a
current-object manifest ONLY on an UNVERSIONED bucket (one version per key, no
markers). On a versioned bucket it legitimately emits duplicate and marker keys
the current-object manifest lacks — a property of the mode, not a tool fault.

``prefix`` (argv[2]) is accepted per the adapter contract and unused: s3kor
prints absolute keys. The adapter runs on the HOST, AFTER the wrapper's clock
stops, so a DuckDB query is fair game here — never inside a timed window.

Keys are compared as TEXT rather than as bytes under ``LC_ALL=C``; every key in
every bucket the study lists is ASCII, so the two orderings agree.

A line the framing cannot carry: refused, not emitted
-----------------------------------------------------
``list`` treats a whole line as a key, which is exact for a listing and wrong for
anything else. s3kor's only committed receipts are capability probes that failed
to authenticate, so the stream the corpus selects for them is a Go stack trace —
whose lines are TAB-indented. Emitting those would produce records whose key
contains a TAB, i.e. rows a field split reads as six columns and
:func:`~s3_listing_study.contract.parse_line` rejects. This adapter refuses the
payload instead (non-zero exit, which the verifier reports as ERROR), the same
refusal ``contract`` documents as the reason keys are bytes and TAB is reserved.
It is the deviation, taken on purpose: ERROR says "no verdict was formed", which
is the truth about a stack trace, whereas a TAB-bearing key is a record no bucket
holds. It is also the ONLY pair in the port that is not byte-identical, so it is
named in ``SANCTIONED_DEVIATIONS`` (``tests/adapters/equivalence.py``) and pinned
by what the difference is — four 6-column rows from the shell, exit 1 and no
output here — rather than merely asserted in this docstring.

``list-versions`` reads the same probe and does NOT deviate: it takes everything
after the first space, which leaves no TAB in any key, and both adapters emit the
same six records.
"""

from __future__ import annotations

import sys
from typing import IO

from s3_listing_study.duckdb_adapter import connect, emit_result, staged

UNKNOWN_MODE_EXIT = 2

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = frozenset({"list", "list-versions"})

LINES = "(SELECT unnest(str_split(content, chr(10))) AS line FROM read_text($path))"

QUERIES = {
    "list": f"""
        SELECT line, NULL, NULL, NULL, NULL FROM {LINES} WHERE line <> ''
    """,
    "list-versions": f"""
        SELECT substr(line, position(' ' IN line) + 1), NULL, NULL, NULL, NULL
        FROM {LINES} WHERE position(' ' IN line) > 0
    """,
}


def normalize(out: IO[bytes], data: bytes, mode: str) -> int:
    if mode not in QUERIES:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    with staged(data) as path:
        emit_result(out, connect().execute(QUERIES[mode], {"path": path}))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("normalize.py: mode required", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    return normalize(sys.stdout.buffer, sys.stdin.buffer.read(), argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
