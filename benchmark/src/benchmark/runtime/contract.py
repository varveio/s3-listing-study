"""The one listing contract shared by measurement adapters and verification.

Contract v2 is one record per line::

    key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class

with ``-`` for any field a listing mode does not expose (the verifier asserts a
field only where the adapter emitted a non-``-`` value), raw key bytes with no
re-encoding, the ETag unquoted, and mtime as a full UTC instant
``YYYY-MM-DDTHH:MM:SSZ`` (``+00:00`` / ``+0000`` are accepted spellings of the
same instant).

Every adapter emits through :func:`emit` and every consumer reads through
:func:`parse_line`, so the framing rules live here once instead of being
re-derived in eleven adapters.

Bytes, not text
---------------
A key is ``bytes`` throughout — it is copied from the listing response and never
decoded, so a key that is not valid UTF-8 survives byte-for-byte. The other four
fields are ASCII by construction (digits, hex, a timestamp, a storage-class
token) and are carried as ``str``. This split is what makes the three documented
key-fidelity bugs structurally impossible rather than merely fixed. Each of the
three came from an adapter passing keys through a text layer that reserves the
bytes a key may contain: rclone's ``jq @tsv`` C-escapes TAB/NL/CR/backslash,
unquoted DuckDB ``-list`` output for s3-fast-list uses TAB and NEWLINE as its own
delimiters, and s4cmd's fixed-width columns cut by byte position without
``LC_ALL=C`` split multi-byte keys mid-character.

Keys the framing cannot carry: rejected, not escaped
----------------------------------------------------
A key containing TAB, NEWLINE or CR cannot be represented by a 5-column,
one-record-per-line format. The choice here is to **reject it loudly** with
:class:`ContractViolation` — an adapter-contract violation in the same category
as a malformed mtime: never a tool FAIL,
never a PASS. Defining an escape was rejected for three reasons:

* The manifest is written in the same format and is the thing an adapter's output
  is joined against. An escape only helps if both sides speak it, so it would
  invalidate the committed manifest binding — and with it the retired
  verifier's byte-identical acceptance bar.
* An escape is lossy in the only way that matters here: every downstream consumer
  that does not decode it (a ``sort``/``comm``/``join`` over the stream, a
  DuckDB read, a human reading ``verify.md``) then sees a key that is not the key
  the bucket holds — the exact failure mode this module exists to eliminate,
  moved rather than removed.
* Such a key cannot currently be *checked* either: no reference manifest contains
  one (``EDGE_BUCKET=none``), so emitting one would manufacture a record the
  manifest can never match, i.e. a guaranteed FAIL attributed to a tool that did
  nothing wrong. Refusing is the honest verdict: ERROR, not FAIL.

The escape/framing question reopens when the study owns its reference bucket and
can construct such keys. Until then this refusal is the whole answer, and it is
deliberate.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO

SEPARATOR = b"\t"
"""Field separator. Reserved: a key containing it is rejected."""

RECORD_SEPARATOR = b"\n"
"""Record separator. Reserved: a key containing it is rejected."""

UNEXPOSED = "-"
"""Sentinel for a field the listing mode does not expose."""

FIELD_COUNT = 5

FIELD_NAMES = ("key", "size", "etag", "mtime", "storage_class")

UNFRAMEABLE_BYTES = b"\t\n\r"
"""Bytes a key may not contain: the line format has no way to carry them."""

# Every anchored pattern here ends in ``\Z``, never ``$``, and is applied with
# ``fullmatch``. Python's ``$`` also matches immediately before a trailing
# newline, so ``^…$`` would accept ``"12\n"`` as a size and ``"…56Z\n"`` as an
# mtime — values that then break the one-record-per-line framing this module
# exists to hold, and that the verifier's awk (where ``$`` is end-of-string)
# rejects. Anchoring twice is deliberate: the pattern stays correct even if a
# caller reaches for ``search`` or ``match``.
MTIME_RE = re.compile(
    r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|\+00:00|\+0000)\Z"
)
"""The mtime shape gate, mirroring the frozen ``MTIME_RE_AWK`` in
``benchmark/tests/test_contract.py``.

Asserted BEFORE :func:`canon_mtime`, because canonicalisation keeps digits only
and therefore also equates garbage that happens to share those digits.
"""

_ZONE_RE = re.compile(r"(Z|\+00:00|\+0000)\Z")
_NON_DIGIT_RE = re.compile(r"[^0-9]")
_SIZE_RE = re.compile(r"\A[0-9]+\Z")
_TOKEN_RE = re.compile(r"\A[!-~]+\Z")


class ContractViolation(Exception):
    """A record does not satisfy contract v2.

    Raised by the emit and parse boundaries. On adapter output this is an
    adapter-contract violation the caller must fix — never a tool FAIL and never
    a PASS; on manifest input it means the snapshot is corrupt.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        line_number: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.line_number = line_number

    def __str__(self) -> str:
        where = "" if self.line_number is None else f"line {self.line_number}: "
        what = "" if self.field is None else f"{self.field}: "
        return f"{where}{what}{self.message}"


def canon_mtime(value: str) -> str:
    """Canonical comparison form of a contract-v2 mtime: the digits of the instant.

    Semantics are exactly the awk ``canon_mt`` the committed verdicts were
    issued under, frozen as ``CANON_MT_AWK`` in ``benchmark/tests/test_contract.py``::

        function canon_mt(s){ sub(/(Z|\\+00:00|\\+0000)$/,"",s); gsub(/[^0-9]/,"",s); return s }

    (the zone anchor is spelled ``\\Z`` here: awk's ``$`` is end-of-string, and
    Python's ``$`` would also strip a zone sitting before a trailing newline)

    so ``…Z``, ``…+00:00`` and ``…+0000`` denoting the same second compare equal.
    This is deliberately not "improved": the committed verdicts were issued
    under it and must be reproducible byte-for-byte.

    Because it keeps digits only, it also equates garbage sharing those digits.
    Gate the shape with :data:`MTIME_RE` first — :func:`parse_line` and
    :func:`emit` already do.
    """
    return _NON_DIGIT_RE.sub("", _ZONE_RE.sub("", value, count=1))


def _check_key(key: bytes) -> None:
    if not isinstance(key, bytes):
        raise ContractViolation(
            f"key must be bytes copied from the listing response, never a decoded str: {key!r}",
            field="key",
        )
    if not key:
        raise ContractViolation("key is empty; an S3 key is at least one byte", field="key")
    for byte in UNFRAMEABLE_BYTES:
        if byte in key:
            raise ContractViolation(
                f"key contains {bytes([byte])!r}, which the one-record-per-line, "
                f"5-column format cannot carry, and this contract escapes nothing: {key!r}",
                field="key",
            )


def _check_size(value: str) -> None:
    if not _SIZE_RE.fullmatch(value):
        raise ContractViolation(f"size must be decimal digits: {value!r}", field="size")


def _check_etag(value: str) -> None:
    if '"' in value:
        raise ContractViolation(
            f"etag must be emitted UNQUOTED; S3 returns it wrapped in literal "
            f"double quotes: {value!r}",
            field="etag",
        )
    if not _TOKEN_RE.fullmatch(value):
        raise ContractViolation(
            f"etag must be printable ASCII with no spaces: {value!r}", field="etag"
        )


def _check_mtime(value: str) -> None:
    if not MTIME_RE.fullmatch(value):
        raise ContractViolation(
            f"mtime must be YYYY-MM-DDTHH:MM:SS with Z/+00:00/+0000, never a bare or "
            f"timezone-less string: {value!r}",
            field="mtime",
        )


def _check_storage_class(value: str) -> None:
    if not _TOKEN_RE.fullmatch(value):
        raise ContractViolation(
            f"storage_class must be printable ASCII with no spaces: {value!r}",
            field="storage_class",
        )


_CHECKS = {
    "size": _check_size,
    "etag": _check_etag,
    "mtime": _check_mtime,
    "storage_class": _check_storage_class,
}


def _normalize(name: str, value: str | None) -> str | None:
    """Fold the ``-`` sentinel to ``None`` and validate anything else."""
    if value is None or value == UNEXPOSED:
        return None
    _CHECKS[name](value)
    return value


@dataclass(frozen=True, slots=True)
class Record:
    """One contract-v2 record, validated at construction.

    ``None`` and the literal ``"-"`` both mean "this mode does not expose the
    field" and are stored as ``None``. The key is exempt: ``-`` is a legal S3 key
    and is never read as a sentinel.
    """

    key: bytes
    size: str | None = None
    etag: str | None = None
    mtime: str | None = None
    storage_class: str | None = None

    def __post_init__(self) -> None:
        _check_key(self.key)
        for name in FIELD_NAMES[1:]:
            object.__setattr__(self, name, _normalize(name, getattr(self, name)))

    def to_line(self) -> bytes:
        """The record as contract-v2 bytes, without a trailing record separator."""
        fields = [self.size, self.etag, self.mtime, self.storage_class]
        return SEPARATOR.join(
            [self.key, *((UNEXPOSED if f is None else f).encode("ascii") for f in fields)]
        )


def emit(
    out: IO[bytes],
    key: bytes,
    *,
    size: int | str | None = None,
    etag: str | None = None,
    mtime: str | None = None,
    storage_class: str | None = None,
) -> None:
    """Validate one record and write it to ``out`` as contract-v2 bytes.

    The single emit boundary for every adapter. ``out`` must be binary
    (``sys.stdout.buffer``): the key is written as the bytes the listing carried,
    with no encode step to mangle it.

    Raises :class:`ContractViolation` on a key the framing cannot carry (TAB,
    NEWLINE or CR — see the module docstring for why this is a refusal rather
    than an escape), a quoted ETag, a non-digit size, or an mtime outside
    :data:`MTIME_RE`.
    """
    record = Record(
        key=key,
        size=None if size is None else str(size),
        etag=etag,
        mtime=mtime,
        storage_class=storage_class,
    )
    out.write(record.to_line() + RECORD_SEPARATOR)


def parse_line(line: bytes, *, line_number: int | None = None) -> Record:
    """Read one contract-v2 record back. The inverse of :func:`emit`.

    ``line`` carries no trailing record separator. Validation is exactly the emit
    boundary's, so a record that round-trips is a record both sides agree on.
    """
    fields = line.split(SEPARATOR)
    if len(fields) != FIELD_COUNT:
        hint = ""
        if len(fields) == 3:
            hint = " — 3 fields is a pre-2026-07-17 artifact"
        raise ContractViolation(
            f"record has {len(fields)} field(s), contract v2 expects {FIELD_COUNT} "
            f"(key<TAB>size<TAB>etag<TAB>mtime<TAB>storage_class){hint}",
            line_number=line_number,
        )
    decoded: list[str] = []
    for name, raw in zip(FIELD_NAMES[1:], fields[1:], strict=True):
        try:
            decoded.append(raw.decode("ascii"))
        except UnicodeDecodeError:
            raise ContractViolation(
                f"field is not ASCII: {raw!r}", field=name, line_number=line_number
            ) from None
    try:
        return Record(fields[0], *decoded)
    except ContractViolation as exc:
        exc.line_number = line_number
        raise


def read_records(stream: IO[bytes]) -> Iterator[Record]:
    """Parse a contract-v2 stream, one record per line.

    A final record without a trailing newline counts — undercounting it is how a
    duplicate slips past a completeness check.
    """
    for line_number, raw in enumerate(stream, start=1):
        line = raw[: -len(RECORD_SEPARATOR)] if raw.endswith(RECORD_SEPARATOR) else raw
        yield parse_line(line, line_number=line_number)
