"""Contract v2: the emit/parse boundary, and the awk semantics it inherited.

The mtime tests are differential against awk: they take the ``canon_mt``
function and ``MTIME_RE_AWK`` frozen below and run them under the real ``awk``.
The 57 surviving committed verdicts were issued under those exact semantics, so "agrees
with what I think awk does" is not enough.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from benchmark.runtime.contract import (
    MTIME_RE,
    ContractViolation,
    Record,
    canon_mtime,
    emit,
    parse_line,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def read_records(stream: io.BytesIO) -> Iterator[Record]:
    for line_number, raw in enumerate(stream, start=1):
        yield parse_line(raw.removesuffix(b"\n"), line_number=line_number)

CANON_MT_AWK = (
    r'function canon_mt(s){ sub(/(Z|\+00:00|\+0000)$/,"",s); '
    r'gsub(/[^0-9]/,"",s); return s }'
)
"""The shell verifier's ``canon_mt``, character for character.

The file it was lifted from is gone, so this constant is now the only copy: it
records the semantics **the 57 surviving committed verdicts were issued under**, and the
tests below run it through the real awk. It is a frozen historical fact, not a
mirror of live code — changing it asserts that those verdicts were formed some
other way, which they were not.
"""

MTIME_RE_AWK = (
    r"^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
    r"T[0-9][0-9]:[0-9][0-9]:[0-9][0-9](Z|[+]00:00|[+]0000)$"
)
"""The shell verifier's shape gate, frozen on the same terms as ``CANON_MT_AWK``."""

MTIMES = [
    "2026-07-17T12:34:56Z",
    "2026-07-17T12:34:56+00:00",
    "2026-07-17T12:34:56+0000",
    "2026-07-17T12:34:56",
    "2026-07-17T12:34:56.123Z",
    "2026-07-17T12:34:56+01:00",
    "2026-07-17 12:34:56Z",
    "2026/07/17T12:34:56Z",
    "20260717123456",
    "2026-07-17T12:34:56+00:00Z",
    "Z",
    "+0000",
    "-",
    "",
    "not a timestamp",
    "26-07-17T12:34:56Z",
    # The divergence class this differential exists to police: Python's ``$``
    # also matches immediately before a trailing newline, awk's does not. A
    # value carrying a newline is also the value that would break the framing,
    # so it must be expressible here.
    "2026-07-17T12:34:56Z\n",
    "2026-07-17T12:34:56+00:00\n",
    "2026-07-17T12:34:56Z\n\n",
    "2026-07-17T12:34:56Z\nnot a timestamp",
    "\n2026-07-17T12:34:56Z",
    "2026-07-17T12:34:56Z\r",
    "\n",
]


def _awk(program: str, value: str, *, args: list[str] | None = None) -> str:
    """Apply ``program``'s ``main(rec)`` to exactly one value, newlines and all.

    One invocation per value. The value is fed as ``value + "\n"`` and rebuilt
    from the records in ``END``, which is exact for every value — including one
    containing, or ending in, a newline. The previous line-per-input harness
    could not express such a value at all, and a value with a trailing newline
    is precisely where Python's ``$`` and awk's disagree: the 16-case table was
    structurally blind to the only input class the two sides differ on.
    """
    if shutil.which("awk") is None:
        pytest.skip("awk is not installed; cannot run the differential against the verifier")
    completed = subprocess.run(
        [
            "awk",
            *(args or []),
            f'{program}\n{{ rec = (NR == 1 ? $0 : rec "\\n" $0) }}\nEND {{ print main(rec) }}',
        ],
        input=value + "\n",
        capture_output=True,
        text=True,
        check=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    return completed.stdout[:-1]


def test_the_awk_harness_can_express_a_value_containing_a_newline() -> None:
    """The differential is only worth running if its input channel is lossless."""
    assert _awk("function main(s){ return length(s) }", "a\nb\n") == "4"
    assert _awk("function main(s){ return length(s) }", "") == "0"
    assert _awk("function main(s){ return length(s) }", "\r") == "1"


def test_canon_mtime_matches_the_verifiers_awk() -> None:
    program = CANON_MT_AWK + "\nfunction main(s){ return canon_mt(s) }"
    expected = [_awk(program, value) for value in MTIMES]
    assert [canon_mtime(value) for value in MTIMES] == expected


def test_canon_mtime_equates_the_three_utc_spellings() -> None:
    z = canon_mtime("2026-07-17T12:34:56Z")
    assert z == "20260717123456"
    assert canon_mtime("2026-07-17T12:34:56+00:00") == z
    assert canon_mtime("2026-07-17T12:34:56+0000") == z


def test_canon_mtime_also_equates_garbage_sharing_those_digits() -> None:
    """Why the shape is gated first, before ``canon_mt`` ever runs."""
    assert canon_mtime("2026/07/17 12.34.56") == canon_mtime("2026-07-17T12:34:56Z")
    assert canon_mtime("not a timestamp") == canon_mtime("-") == ""


def test_mtime_re_matches_the_verifiers_shape_gate() -> None:
    awk_re = MTIME_RE_AWK
    program = 'function main(s){ return (s ~ re) ? "1" : "0" }'
    expected = [_awk(program, value, args=["-v", f"re={awk_re}"]) for value in MTIMES]
    # ``match``, not ``fullmatch``: this asserts the *pattern* is anchored the way
    # awk's is, so a ``$`` that leaks a trailing newline through is caught here
    # and not only at whichever call site happens to use ``fullmatch``.
    assert [("1" if MTIME_RE.match(value) else "0") for value in MTIMES] == expected
    assert [MTIME_RE.match(value) is not None for value in MTIMES] == [
        MTIME_RE.fullmatch(value) is not None for value in MTIMES
    ]


# ------------------------------------------------------------------ emit / parse


def _line(**kwargs: object) -> bytes:
    out = io.BytesIO()
    emit(out, **kwargs)  # type: ignore[arg-type]
    return out.getvalue()


def test_emit_writes_five_tab_separated_fields() -> None:
    line = _line(
        key=b"gsod/2026/72509594728.csv",
        size=1234,
        etag="d41d8cd98f00b204e9800998ecf8427e",
        mtime="2026-07-17T12:34:56Z",
        storage_class="STANDARD",
    )
    assert line == (
        b"gsod/2026/72509594728.csv\t1234\td41d8cd98f00b204e9800998ecf8427e\t"
        b"2026-07-17T12:34:56Z\tSTANDARD\n"
    )
    assert line.rstrip(b"\n").count(b"\t") == 4
    assert _line(key=b"k", size=0) == b"k\t0\t-\t-\t-\n"  # an int size renders the same


def test_emit_fills_unexposed_fields_with_the_sentinel() -> None:
    assert _line(key=b"a/b") == b"a/b\t-\t-\t-\t-\n"
    assert _line(key=b"a/b", size="-", storage_class="-") == b"a/b\t-\t-\t-\t-\n"


def test_emit_rejects_a_quoted_etag() -> None:
    with pytest.raises(ContractViolation, match="UNQUOTED"):
        _line(key=b"k", etag='"d41d8cd98f00b204e9800998ecf8427e"')


def test_emit_rejects_a_non_digit_size() -> None:
    with pytest.raises(ContractViolation, match="decimal digits"):
        _line(key=b"k", size="1.5")


@pytest.mark.parametrize(
    "mtime",
    ["2026-07-17T12:34:56", "2026-07-17 12:34:56", "2026-07-17T12:34:56.123Z", "20260717123456"],
)
def test_emit_rejects_a_malformed_mtime(mtime: str) -> None:
    """Caught at the shape gate, before canonicalisation can equate it with anything."""
    with pytest.raises(ContractViolation) as exc:
        _line(key=b"k", mtime=mtime)
    assert exc.value.field == "mtime"


@pytest.mark.parametrize("field", ["size", "etag", "mtime", "storage_class"])
@pytest.mark.parametrize("suffix", ["\n", "\r", "\n\n"])
def test_emit_rejects_a_scalar_field_that_would_break_the_framing(field: str, suffix: str) -> None:
    """One trailing newline in any scalar field turns one record into two lines."""
    values = {
        "size": "12",
        "etag": "d41d8cd98f00b204e9800998ecf8427e",
        "mtime": "2026-07-17T12:34:56Z",
        "storage_class": "STANDARD",
    }
    with pytest.raises(ContractViolation) as exc:
        _line(key=b"k", **{field: values[field] + suffix})
    assert exc.value.field == field


@pytest.mark.parametrize("zone", ["Z", "+00:00", "+0000"])
def test_emit_accepts_every_contract_zone_spelling(zone: str) -> None:
    assert _line(key=b"k", mtime=f"2026-07-17T12:34:56{zone}").endswith(
        f"2026-07-17T12:34:56{zone}\t-\n".encode()
    )


# ------------------------------------------------------------------- key fidelity


@pytest.mark.parametrize("byte", [b"\t", b"\n", b"\r"])
def test_emit_rejects_a_key_the_framing_cannot_carry(byte: bytes) -> None:
    """Documented decision: reject loudly, never escape (see the module docstring)."""
    with pytest.raises(ContractViolation) as exc:
        _line(key=b"pre" + byte + b"post")
    assert exc.value.field == "key"
    assert "escapes nothing" in exc.value.message


def test_emit_carries_non_utf8_key_bytes_through_unchanged() -> None:
    key = b"gsod/\xff\xfe\x80/\xc3(latin-1 \xe9).csv"
    line = _line(key=key, size=7)
    assert line == key + b"\t7\t-\t-\t-\n"
    assert parse_line(line.rstrip(b"\n")).key == key


def test_a_key_of_one_hyphen_is_a_key_not_a_sentinel() -> None:
    assert _line(key=b"-") == b"-\t-\t-\t-\t-\n"
    assert parse_line(b"-\t-\t-\t-\t-") == Record(key=b"-")


def test_emit_rejects_an_empty_key() -> None:
    with pytest.raises(ContractViolation, match="empty"):
        _line(key=b"")


def test_emit_rejects_a_str_key_as_a_contract_violation() -> None:
    """Not a raw TypeError: this is the one emit boundary eleven adapters share."""
    with pytest.raises(ContractViolation) as exc:
        _line(key="gsod/2026/x.csv")
    assert exc.value.field == "key"


# ------------------------------------------------------------------------ parsing


def test_parse_line_is_the_inverse_of_emit() -> None:
    record = Record(b"a/b\xff", "12", "abc123", "2026-07-17T12:34:56+00:00", "GLACIER")
    assert parse_line(record.to_line()) == record


def test_parse_line_folds_the_sentinel_to_none() -> None:
    record = parse_line(b"a/b\t-\t-\t-\t-")
    assert record == Record(key=b"a/b")


def test_parse_line_rejects_a_wrong_field_count() -> None:
    with pytest.raises(ContractViolation, match="expects 5"):
        parse_line(b"a/b\t12")


def test_parse_line_names_the_pre_contract_v2_shape() -> None:
    with pytest.raises(ContractViolation, match="pre-2026-07-17 artifact"):
        parse_line(b"a/b\t12\tabc123")


def test_parse_line_rejects_a_non_ascii_scalar_field() -> None:
    with pytest.raises(ContractViolation) as exc:
        parse_line(b"a/b\t12\t\xff\t-\t-")
    assert exc.value.field == "etag"


def test_read_records_counts_a_final_record_with_no_trailing_newline() -> None:
    stream = io.BytesIO(b"a\t1\t-\t-\t-\nb\t2\t-\t-\t-")
    assert [record.key for record in read_records(stream)] == [b"a", b"b"]


def test_read_records_reports_the_offending_line_number() -> None:
    stream = io.BytesIO(b"a\t1\t-\t-\t-\nb\t2\t-\tnope\t-\n")
    with pytest.raises(ContractViolation) as exc:
        list(read_records(stream))
    assert exc.value.line_number == 2
    assert str(exc.value).startswith("line 2: mtime: ")
