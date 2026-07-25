"""Tests for the gate: corpus pinning, the exit contract, and the state record.

The corpus-size test is the one that runs against the real repo. It is deliberate:
if a committed verdict artifact ever stops being discovered, CI says so here
rather than the gate quietly reporting a clean sweep of whatever survived.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.differential import __main__ as gate
from tests.differential.corpus import (
    Payload,
    SingleCase,
    UnionCase,
    discover_singles,
    discover_unions,
)
from tests.differential.oracle import (
    EXIT_GREEN,
    EXIT_MISMATCH,
    EXIT_ORACLE_UNAVAILABLE,
    Oracle,
    OracleUnavailable,
)
from tests.differential.replay import Result

REPO = Path(__file__).resolve().parents[1]


# --- the pin itself -------------------------------------------------------


def test_committed_corpus_is_the_size_the_gate_is_pinned_to() -> None:
    assert len(discover_singles(REPO)) == gate.EXPECTED_SINGLES
    assert len(discover_unions(REPO)) == gate.EXPECTED_UNIONS


# --- state file -----------------------------------------------------------


def _emit(path: Path, unit: str, status: str) -> None:
    gate.emit_state(
        path=path,
        unit=unit,
        status=status,
        gate="python3 -m tests.differential",
        gate_sha256="0" * 64,
        head="deadbeef",
        started="2026-07-25T00:00:00Z",
        finished="2026-07-25T00:04:00Z",
        counts={"singles_ok": 67, "singles_bad": 0, "unions_ok": 2, "unions_bad": 0},
        selection={"restricted": False},
    )


def test_emit_state_keeps_prior_units(tmp_path: Path) -> None:
    state = tmp_path / "cleanup.json"
    _emit(state, "U0", "green")
    _emit(state, "U1", "red")
    units = json.loads(state.read_text())["units"]
    assert set(units) == {"U0", "U1"}
    assert len(state.with_suffix(".log.jsonl").read_text().splitlines()) == 2


def test_emit_state_refuses_to_overwrite_a_corrupt_record(tmp_path: Path) -> None:
    # Silently starting over would destroy every prior unit's record.
    state = tmp_path / "cleanup.json"
    state.write_text("{not json")
    with pytest.raises(gate.StateFileError, match="not valid JSON"):
        _emit(state, "U0", "green")
    assert state.read_text() == "{not json"


def test_emit_state_refuses_a_file_that_is_not_a_state_record(tmp_path: Path) -> None:
    state = tmp_path / "cleanup.json"
    state.write_text('{"something": "else"}')
    with pytest.raises(gate.StateFileError, match="no 'units' object"):
        _emit(state, "U0", "green")


# --- the gate, with the replay stubbed out --------------------------------


def _case(index: int) -> SingleCase:
    rel = f"tools/t/receipts/case-{index:02d}"
    return SingleCase(
        receipt=REPO / rel,
        rel=rel,
        tool="t",
        scope_args=["--scope", "full"],
        stream="stdout",
        input_path=f"receipts/t/case-{index:02d}.txt",
        payloads=[Payload(f"receipts/t/case-{index:02d}.txt", "0" * 64)],
    )


def _union(index: int) -> UnionCase:
    rel = f"tools/t/receipts/union-{index}"
    return UnionCase(
        union_md=REPO / rel / "union-verify.md",
        rel=rel,
        tool="t",
        shards=[f"{rel}/a"],
        remainder=None,
        payloads=[Payload(f"receipts/t/union-{index}.txt", "0" * 64)],
    )


def _stub_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    singles: int = gate.EXPECTED_SINGLES,
    unions: int = gate.EXPECTED_UNIONS,
    replay: Any = None,
) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    oracle = Oracle(
        repo=REPO,
        data_dir=tmp_path / "data",
        manifests=tmp_path / "data" / "manifests",
        receipts=tmp_path / "data" / "receipts",
        registry=REPO / "tests/fixtures/registry-254c8cfe.md",
    )
    monkeypatch.setattr(gate, "preflight", lambda repo: oracle)
    monkeypatch.setattr(gate, "discover_singles", lambda repo: [_case(i) for i in range(singles)])
    monkeypatch.setattr(gate, "discover_unions", lambda repo: [_union(i) for i in range(unions)])
    monkeypatch.setattr(gate, "check_payloads", lambda oracle, payloads: len(list(payloads)))

    def farm(root: Path, repo: Path, manifests: Path, receipts: Path) -> Path:
        seen["root"] = root
        (root / "cwd").mkdir(parents=True, exist_ok=True)
        return root / "cwd"

    monkeypatch.setattr(gate, "make_link_farm", farm)

    def ok(case: Any, oracle: Oracle, farm: Path, work: Path) -> Result:
        return Result(case.name, True)

    monkeypatch.setattr(gate, "replay_single", replay or ok)
    monkeypatch.setattr(gate, "replay_union", replay or ok)
    return seen


def _run(tmp_path: Path, *args: str) -> tuple[int, dict[str, Any]]:
    state = tmp_path / "state" / "cleanup.json"
    code = gate.main([f"--repo={REPO}", f"--state={state}", "--jobs=2", *args])
    record: dict[str, Any] = json.loads(state.read_text())["units"]["U0"] if state.is_file() else {}
    return code, record


def test_full_run_is_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_gate(monkeypatch, tmp_path)
    code, record = _run(tmp_path)
    assert code == EXIT_GREEN
    assert record["status"] == "green"
    assert record["counts"]["singles_ok"] == gate.EXPECTED_SINGLES
    assert record["selection"]["restricted"] is False


def test_a_shrunken_corpus_is_not_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 60 of 67 verdict artifacts gone: the denominator must not shrink with the
    # numerator. This is the false green the harness exists to prevent.
    _stub_gate(monkeypatch, tmp_path, singles=7)
    code, record = _run(tmp_path)
    assert code == EXIT_MISMATCH
    assert record["status"] == "corpus-mismatch"


def test_a_grown_corpus_is_not_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_gate(monkeypatch, tmp_path, singles=gate.EXPECTED_SINGLES + 1)
    code, record = _run(tmp_path)
    assert code == EXIT_MISMATCH
    assert record["status"] == "corpus-mismatch"


def test_a_missing_union_is_not_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub_gate(monkeypatch, tmp_path, unions=1)
    code, record = _run(tmp_path)
    assert code == EXIT_MISMATCH
    assert record["status"] == "corpus-mismatch"


def test_a_filter_matching_nothing_never_stamps_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_gate(monkeypatch, tmp_path)
    code, record = _run(tmp_path, "--filter=zzz-no-such-case")
    assert code == EXIT_MISMATCH
    assert record["status"] == "no-cases"
    assert record["counts"] == {
        "singles_ok": 0,
        "singles_bad": 0,
        "unions_ok": 0,
        "unions_bad": 0,
    }


def test_a_filtered_run_records_partial_not_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_gate(monkeypatch, tmp_path)
    code, record = _run(tmp_path, "--filter=case-0")
    assert code == EXIT_GREEN
    assert record["status"] == "partial"
    assert record["selection"]["filter"] == "case-0"
    assert record["counts"]["singles_ok"] == 10


def test_singles_only_records_partial_not_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_gate(monkeypatch, tmp_path)
    code, record = _run(tmp_path, "--singles-only")
    assert code == EXIT_GREEN
    assert record["status"] == "partial"
    assert record["selection"]["singles_only"] is True


def test_the_state_record_carries_the_real_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_gate(monkeypatch, tmp_path)
    _, record = _run(tmp_path, "--unions-only")
    assert "--unions-only" in record["gate"]
    assert record["selection"]["unions_only"] is True


def test_a_mismatch_is_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def bad(case: Any, oracle: Oracle, farm: Path, work: Path) -> Result:
        return Result(case.name, case.name.endswith("case-00"), "bytes differ", "log tail")

    _stub_gate(monkeypatch, tmp_path, replay=bad)
    code, record = _run(tmp_path)
    assert code == EXIT_MISMATCH
    assert record["status"] == "red"


def test_a_worker_crash_records_error_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(case: Any, oracle: Oracle, farm: Path, work: Path) -> Result:
        raise ValueError("worker exploded")

    seen = _stub_gate(monkeypatch, tmp_path, replay=boom)
    code, record = _run(tmp_path)
    assert code == EXIT_MISMATCH
    assert record["status"] == "error"
    assert not seen["root"].exists()


def test_a_missing_payload_is_oracle_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A partial restore of the data directory must never read as "the port is
    # wrong": it is the oracle that is incomplete.
    _stub_gate(monkeypatch, tmp_path)

    def missing(oracle: Oracle, payloads: Any) -> int:
        raise OracleUnavailable("raw payload named by the corpus is missing: receipts/x")

    monkeypatch.setattr(gate, "check_payloads", missing)
    code, record = _run(tmp_path)
    assert code == EXIT_ORACLE_UNAVAILABLE
    assert record["status"] == "oracle-unavailable"


def test_an_unwritable_state_file_never_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_gate(monkeypatch, tmp_path)
    state = tmp_path / "state" / "cleanup.json"
    state.parent.mkdir(parents=True)
    state.write_text("{not json")
    code = gate.main([f"--repo={REPO}", f"--state={state}", "--jobs=2"])
    assert code == EXIT_MISMATCH
    assert state.read_text() == "{not json"
