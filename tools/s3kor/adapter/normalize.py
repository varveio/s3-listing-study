#!/usr/bin/env python3
"""tools/s3kor/adapter/normalize.py <mode> [prefix] — s3kor output adapter.

Reads s3kor's raw stdout for one mode on stdin, writes contract v2 on stdout, one
record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class   (`-` where unexposed)

Both modes are one physical line per entry. Framing and validation stay in the
shared contract through ``emit``.

s3kor's ``ls`` output contract (v0.0.37, ``list.go printAllObjects``)::

    list           one line per object: the FULL object key, nothing else.
    list-versions  one line per version: "<versionId> <key>", one space between.

When ``--custom-endpoint-url`` is set, s3kor writes one informational line to
the same stdout stream before the keys::

    Using custom endpoint [<url>] on region [<region>]

Only a matching first physical line is discarded. Later lines with those bytes
remain keys: the stream is otherwise one key per line, so broad filtering would
silently make such an object unverifiable.

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
stops. It carries each key as the bytes the tool printed and keeps memory bounded
by one input chunk plus the longest physical line.

A line the framing cannot carry: refused, not emitted
-----------------------------------------------------
``list`` treats a whole line as a key, which is exact for a listing and wrong for
anything else. s3kor's only committed receipts are capability probes that failed
to authenticate, so the stream the corpus selects for them is a Go stack trace —
whose lines are TAB-indented. Emitting those would produce records whose key
contains a TAB, i.e. rows a field split reads as six columns and
:func:`~benchmark.runtime.contract.parse_line` rejects. This adapter refuses the
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

import shutil
import sys
import tempfile
from collections.abc import Mapping
from typing import IO

from benchmark.runtime.contract import emit
from benchmark.runtime.duckdb_adapter import count_lf_lines, iter_lf_lines
from benchmark.runtime.normalizer_cli import normalizer_main

UNKNOWN_MODE_EXIT = 2

# Declared rather than inferred, so the equivalence harness can name a mode no
# committed payload exercises — untested by construction, and invisible otherwise.
MODES = frozenset({"list", "list-versions"})

def _custom_endpoint_notice(line: bytes) -> bool:
    return (
        line.startswith(b"Using custom endpoint [")
        and b"] on region [" in line
        and line.endswith(b"]")
    )


def count_rows(data: bytes, mode: str, prefix: str = "", native_root: str = "") -> int:
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    first = True

    def selected(line: bytes) -> bool:
        nonlocal first
        notice = first and _custom_endpoint_notice(line)
        first = False
        return bool(line) and not notice and (mode == "list" or b" " in line)

    return count_lf_lines(data, selected)


def normalize(
    out: IO[bytes],
    data: bytes,
    mode: str,
    prefix: str = "",
    config: Mapping[str, object] | None = None,
) -> int:
    if mode not in MODES:
        print(f"normalize.py: unknown mode: {mode}", file=sys.stderr)
        return UNKNOWN_MODE_EXIT
    with tempfile.TemporaryFile() as staged:
        first = True
        for line in iter_lf_lines(data):
            notice = first and _custom_endpoint_notice(line)
            first = False
            if not line or notice:
                continue
            if mode == "list-versions":
                _version, separator, line = line.partition(b" ")
                if not separator:
                    continue
            emit(staged, line)
        staged.seek(0)
        shutil.copyfileobj(staged, out)
    return 0


def main(argv: list[str] | None = None) -> int:
    return normalizer_main(
        normalize, modes=MODES, prog="s3kor normalize", argv=argv, error_exit=UNKNOWN_MODE_EXIT
    )


if __name__ == "__main__":
    sys.exit(main())
