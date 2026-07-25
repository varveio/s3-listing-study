"""The set math, in SQL: completeness, duplication, and field mismatches.

The verifier's data logic was already relational algebra written in coreutils —
missing/extra keys are anti-joins (``comm -23`` / ``comm -13``), field
mismatches a join plus a conditional compare where the adapter emitted a
non-``-`` value, duplicates-before-dedup a ``GROUP BY key HAVING count(*) > 1``
over the concatenated multiset. Stating it as SQL removes the hazard the shell
had to shout about: ``LC_ALL=C`` had to hold for ``sort``, ``comm`` and
``join`` to agree on byte order, and a locale that broke that agreement made
the diff *invent* missing and extra keys — a finding against a tool that did
nothing wrong.

Keys travel as **hex**
----------------------
A key is bytes copied from the listing response and is never decoded
(:mod:`s3_listing_study.contract`), but a DuckDB ``VARCHAR`` is UTF-8 text and
``BLOB`` carries neither ``substring`` nor a fast bulk load. Every field is
therefore staged as ``'x' + bytes.hex()``: fixed-width lowercase hex orders
exactly as the underlying bytes do (``'0'`` < ``'9'`` < ``'a'`` < ``'f'`` in
ASCII), so ``ORDER BY`` reproduces ``LC_ALL=C sort`` and equality reproduces
byte equality — for any key, valid UTF-8 or not. The ``'x'`` prefix keeps an
empty value from colliding with the CSV reader's NULL, and ``'~'`` is the one
value that *is* NULL: the ``-`` sentinel on the adapter side, which is what "a
field is asserted only where the mode exposed it" means.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contract import FIELD_COUNT, MTIME_RE, UNEXPOSED, canon_mtime
from ..duckdb_adapter import connect
from .errors import VerifierError

Row = list[bytes]
"""One 5-field record, still as raw bytes: key, size, etag, mtime, storage_class."""

_NULL = "~"
_COLUMNS = (
    "{'k':'VARCHAR','s':'VARCHAR','e':'VARCHAR','el':'VARCHAR',"
    "'m':'VARCHAR','mc':'VARCHAR','sc':'VARCHAR'}"
)
_READ = (
    "read_csv(?, delim='\t', header=false, quote='', escape='', "
    f"nullstr='{_NULL}', columns={_COLUMNS})"
)
_UNEXPOSED = UNEXPOSED.encode("ascii")
_SIDE = {
    False: ("manifest", "The snapshot is corrupt, not a tool finding; orchestrator re-baselines."),
    True: ("adapter output", "This is an adapter-contract violation, not a tool FAIL."),
}


def split_records(data: bytes) -> list[Row]:
    """Split contract-v2 bytes into records, TAB-separated, one per line.

    A final record without a trailing newline counts: counting newline
    characters instead of records is how two identical records with no final
    newline read as one, and a duplicate PASSes.
    """
    if not data:
        return []
    body = data[:-1] if data.endswith(b"\n") else data
    return [line.split(b"\t") for line in body.split(b"\n")]


def bad_mtimes(rows: Iterable[Row]) -> list[bytes]:
    """Rows whose mtime is not contract-v2 shape, rendered as ``key<TAB>mtime``.

    Asserted over every row of both sides *before* any dedup or join.
    :func:`~s3_listing_study.contract.canon_mtime` keeps digits only, so it also
    equates garbage sharing those digits, and a malformed mtime on a key the
    inner join drops (an extra, a missing, a later manifest row) would otherwise
    slip past a first-row check and reach a FAIL or a PASS.
    """
    bad = []
    for row in rows:
        mtime = row[3]
        if mtime == _UNEXPOSED or MTIME_RE.fullmatch(mtime.decode("latin-1")):
            continue
        bad.append(row[0] + b"\t" + mtime)
    return bad


def malformed_rows(rows: Sequence[Row]) -> list[str]:
    """Rows that are not 5-field, rendered as ``<line>: <n> fields``."""
    return [
        f"{n}: {len(row)} fields" for n, row in enumerate(rows, start=1) if len(row) != FIELD_COUNT
    ]


def _hex(value: bytes, sentinel: bool) -> str:
    if sentinel and value == _UNEXPOSED:
        return _NULL
    return "x" + value.hex()


def _unhex(value: str) -> bytes:
    return bytes.fromhex(value[1:])


def _stage(rows: Sequence[Row], *, sentinel: bool) -> bytes:
    """Render rows as the hex TSV DuckDB loads.

    ``sentinel`` marks the adapter side, where ``-`` means "this mode does not
    expose the field" and becomes SQL ``NULL``. The manifest side keeps ``-``
    as a literal value: a CommonPrefix legitimately carries one, and a mismatch
    against it is reported as ``manifest=-``.

    A row that is not 5-field is refused here rather than unpacked. The
    field-count gate reads the manifest's FIRST record only, in both
    implementations, so a TAB inside a key on any later row reaches this point;
    unpacking it raised ``ValueError``, which escaped as exit 1 — FAIL, an
    accusation — where the shell reaches ``die`` and exits 3.
    """
    side, blame = _SIDE[sentinel]
    out = bytearray()
    for number, row in enumerate(rows, start=1):
        if len(row) != FIELD_COUNT:
            raise VerifierError(
                f"{side} record {number} has {len(row)} field(s), not {FIELD_COUNT} "
                "(key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class) — most likely a literal TAB "
                f"inside a key. {blame}"
            )
        key, size, etag, mtime, storage_class = row
        canon = canon_mtime(mtime.decode("latin-1")).encode("latin-1")
        out += "\t".join(
            (
                _hex(key, False),
                _hex(size, sentinel),
                _hex(etag, sentinel),
                _hex(etag.lower(), sentinel),
                _hex(mtime, sentinel),
                _hex(canon, sentinel),
                _hex(storage_class, sentinel),
            )
        ).encode("ascii")
        out += b"\n"
    return bytes(out)


@dataclass(frozen=True)
class Sides:
    """The two staged relations a verdict is formed from."""

    connection: Any

    def distinct(self, table: str) -> int:
        query = f"SELECT count(DISTINCT k) FROM {table}"
        return int(self.connection.execute(query).fetchone()[0])

    def anti_join(self, left: str, right: str) -> list[bytes]:
        """Keys in ``left`` and not in ``right`` — ``comm -23``, in byte order."""
        query = f"SELECT DISTINCT k FROM {left} WHERE k NOT IN (SELECT k FROM {right}) ORDER BY k"
        return [_unhex(k) for (k,) in self.connection.execute(query).fetchall()]

    def duplicated(self, table: str) -> list[bytes]:
        """Keys emitted more than once, counted BEFORE dedup."""
        query = f"SELECT k FROM {table} GROUP BY k HAVING count(*) > 1 ORDER BY k"
        return [_unhex(k) for (k,) in self.connection.execute(query).fetchall()]

    def field_mismatches(self) -> list[bytes]:
        """One line per mismatch: ``<field><TAB><key><TAB>tool=..<TAB>manifest=..``.

        A field is compared only where the adapter emitted a non-``-`` value —
        what a mode exposes it is held to, what it does not it is not failed
        for. ETag compares case-insensitively — ASCII-only and deliberately so,
        since an ETag is a hex digest and a Unicode-aware fold would equate
        byte sequences S3 never emits — and mtime by canonical form, so a
        manifest ``…Z`` and an adapter ``…+00:00`` denoting the same second
        compare equal. Ordering is by key then by field, which is what the
        ``join``-into-``awk`` pipeline produced.
        """
        rows = self.connection.execute(
            """
            SELECT k, field, tool, man FROM (
              SELECT a.k AS k, 1 AS ord, 'size' AS field, a.s AS tool, e.s AS man
                FROM actual_u a JOIN expected_u e ON a.k = e.k
               WHERE a.s IS NOT NULL AND a.s <> e.s
              UNION ALL
              SELECT a.k, 2, 'etag', a.e, e.e
                FROM actual_u a JOIN expected_u e ON a.k = e.k
               WHERE a.e IS NOT NULL AND a.el <> e.el
              UNION ALL
              SELECT a.k, 3, 'mtime', a.m, e.m
                FROM actual_u a JOIN expected_u e ON a.k = e.k
               WHERE a.m IS NOT NULL AND a.mc <> e.mc
              UNION ALL
              SELECT a.k, 4, 'storage_class', a.sc, e.sc
                FROM actual_u a JOIN expected_u e ON a.k = e.k
               WHERE a.sc IS NOT NULL AND a.sc <> e.sc
            ) ORDER BY k, ord
            """
        ).fetchall()
        return [
            b"\t".join(
                (
                    field.encode("ascii"),
                    _unhex(key),
                    b"tool=" + _unhex(tool),
                    b"manifest=" + _unhex(man),
                )
            )
            for key, field, tool, man in rows
        ]


def _dedup(connection: Any, name: str) -> None:
    connection.execute(
        f"CREATE TABLE {name}_u AS SELECT * EXCLUDE (rn) FROM ("
        "SELECT *, row_number() OVER (PARTITION BY k ORDER BY rid) AS rn "
        f"FROM {name}) WHERE rn = 1"
    )


@contextmanager
def staged(actual: Sequence[Row], expected: Sequence[Row], work: Path) -> Iterator[Sides]:
    """Load both sides and expose the relations the verdict is read from.

    ``actual_u`` / ``expected_u`` are the by-key deduplication the shell got
    from ``sort -u -k1,1`` before joining: one record per key, the first one
    emitted for a repeated key. "First one in input order" is a GNU coreutils
    property of ``sort -u``, not something POSIX guarantees; the window function
    below states it explicitly rather than inheriting it from an implementation.
    """
    connection = connect()
    try:
        for name, rows, sentinel in (("actual", actual, True), ("expected", expected, False)):
            path = work / f"{name}.hex.tsv"
            path.write_bytes(_stage(rows, sentinel=sentinel))
            if not rows:
                # An empty relation is still a relation: the CSV reader cannot
                # sniff a schema from zero bytes, and refusing here would turn
                # "the tool emitted nothing" into a crash instead of a verdict.
                connection.execute(
                    f"CREATE TABLE {name}(rid BIGINT, k VARCHAR, s VARCHAR, e VARCHAR, "
                    "el VARCHAR, m VARCHAR, mc VARCHAR, sc VARCHAR)"
                )
                _dedup(connection, name)
                continue
            connection.execute(
                f"CREATE TABLE {name} AS SELECT row_number() OVER () AS rid, * FROM {_READ}",
                [str(path)],
            )
            _dedup(connection, name)
        yield Sides(connection)
    finally:
        connection.close()
