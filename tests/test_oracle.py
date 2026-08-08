"""Tests for the oracle preflight — the thing that keeps 42 from reading as 1."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from tests.differential.corpus import Payload
from tests.differential.oracle import (
    Oracle,
    OracleUnavailable,
    check_payloads,
    resolve_payload,
)


def _oracle(tmp_path: Path) -> Oracle:
    repo = tmp_path / "repo"
    data = tmp_path / "data"
    (repo / "tools").mkdir(parents=True)
    (data / "manifests").mkdir(parents=True)
    (data / "receipts").mkdir(parents=True)
    return Oracle(
        repo=repo,
        data_dir=data,
        manifests=data / "manifests",
        receipts=data / "receipts",
        registry=repo / "tests/fixtures/registry-254c8cfe.md",
    )


def _write(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def test_resolve_payload_matches_the_link_farm(tmp_path: Path) -> None:
    oracle = _oracle(tmp_path)
    assert resolve_payload(oracle, "receipts/s5cmd/a.txt") == oracle.receipts / "s5cmd/a.txt"
    assert resolve_payload(oracle, "manifests/m.tsv.gz") == oracle.manifests / "m.tsv.gz"
    assert resolve_payload(oracle, "tools/s5cmd/receipts/r/stderr.txt") == (
        oracle.repo / "tools/s5cmd/receipts/r/stderr.txt"
    )


def test_check_payloads_accepts_a_complete_restore(tmp_path: Path) -> None:
    oracle = _oracle(tmp_path)
    external = _write(oracle.receipts / "s5cmd/a.txt", b"listing\n")
    in_repo = _write(oracle.repo / "tools/s5cmd/receipts/r/stderr.txt", b"")
    payloads = [
        Payload("receipts/s5cmd/a.txt", external),
        Payload("tools/s5cmd/receipts/r/stderr.txt", in_repo),
        Payload("receipts/s5cmd/a.txt", external),  # named twice; hashed once
    ]
    assert check_payloads(oracle, payloads) == 2


def test_check_payloads_reports_a_partial_restore_as_oracle_unavailable(tmp_path: Path) -> None:
    # The whole point of 42: a missing payload must never surface as exit 1,
    # which would say "your port is wrong" about an incomplete oracle.
    oracle = _oracle(tmp_path)
    present = _write(oracle.receipts / "s5cmd/a.txt", b"listing\n")
    payloads = [
        Payload("receipts/s5cmd/a.txt", present),
        Payload("receipts/s5cmd/gone.txt", present),
    ]
    with pytest.raises(OracleUnavailable, match=re.escape("missing: receipts/s5cmd/gone.txt")):
        check_payloads(oracle, payloads)


def test_check_payloads_rejects_bytes_that_do_not_match_the_receipt(tmp_path: Path) -> None:
    oracle = _oracle(tmp_path)
    _write(oracle.receipts / "s5cmd/a.txt", b"tampered\n")
    cited = hashlib.sha256(b"listing\n").hexdigest()
    with pytest.raises(OracleUnavailable, match="digest mismatch"):
        check_payloads(oracle, [Payload("receipts/s5cmd/a.txt", cited)])


def test_check_payloads_rejects_a_record_with_no_digest(tmp_path: Path) -> None:
    oracle = _oracle(tmp_path)
    _write(oracle.receipts / "s5cmd/a.txt", b"listing\n")
    with pytest.raises(OracleUnavailable, match="no sha256"):
        check_payloads(oracle, [Payload("receipts/s5cmd/a.txt", "")])
