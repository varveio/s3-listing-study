"""Tests for staging, the link farm, and byte comparison."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.differential.replay import make_link_farm, normalize_generated, unstamp

_HEADER = b"# Union report\n\n- Generated (UTC): 2026-07-17T08:09:26Z\n\n## Shards\n"


def test_normalize_generated_blanks_only_the_generated_line() -> None:
    a = normalize_generated(_HEADER)
    b = normalize_generated(_HEADER.replace(b"2026-07-17T08:09:26Z", b"2026-07-25T23:59:59Z"))
    assert a == b
    assert b"## Shards" in a


def test_normalize_generated_keeps_crlf_distinct_from_lf() -> None:
    # read_text() would fold these together and pass a CRLF report as identical.
    lf = b"| 0 | a/ | `x` |\n"
    crlf = b"| 0 | a/ | `x` |\r\n"
    assert normalize_generated(_HEADER + lf) != normalize_generated(_HEADER + crlf)


def test_normalize_generated_survives_undecodable_bytes() -> None:
    # A payload byte the locale codec cannot decode must be reported as a
    # mismatch, not raise out of the worker.
    raw = _HEADER + b"| 0 | \xff\xfe | `x` |\n"
    assert normalize_generated(raw) == raw.replace(
        b"- Generated (UTC): 2026-07-17T08:09:26Z", b"- Generated (UTC): <replay>"
    )
    assert normalize_generated(raw) != normalize_generated(_HEADER + b"| 0 | ab | `x` |\n")


def test_make_link_farm_points_at_the_oracle_roots(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    manifests = tmp_path / "data" / "manifests"
    receipts = tmp_path / "data" / "receipts"
    manifests.mkdir(parents=True)
    receipts.mkdir(parents=True)

    farm = make_link_farm(tmp_path / "root", repo, manifests, receipts)

    assert (farm / "manifests").resolve() == manifests.resolve()
    assert (farm / "receipts").resolve() == receipts.resolve()
    assert (farm / "tools").resolve() == (repo / "tools").resolve()


def test_make_link_farm_is_rerunnable(tmp_path: Path) -> None:
    # --keep is the documented debugging flag, so a second run into the same
    # directory is the likeliest invocation of all.
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    manifests = tmp_path / "data" / "manifests"
    receipts = tmp_path / "data" / "receipts"
    manifests.mkdir(parents=True)
    receipts.mkdir(parents=True)

    root = tmp_path / "root"
    make_link_farm(root, repo, manifests, receipts)
    farm = make_link_farm(root, repo, manifests, receipts)

    assert (farm / "receipts").resolve() == receipts.resolve()


def test_unstamp_restores_the_placeholder(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.md"
    receipt.write_text("# Receipt\n\n**PASS** — see `verify.md`\n\n## Notes\n")
    unstamp(receipt)
    assert receipt.read_text() == (
        "# Receipt\n\n_(filled in by `harness/verify-listing.sh`)_\n\n## Notes\n"
    )


def test_unstamp_refuses_a_receipt_without_exactly_one_stamp(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.md"
    receipt.write_text("# Receipt\n\n**PASS** — see `verify.md`\n**FAIL** — see `verify.md`\n")
    with pytest.raises(RuntimeError, match="carries 2 stamped verdicts"):
        unstamp(receipt)
