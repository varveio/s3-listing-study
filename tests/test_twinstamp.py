"""Profile-generic TwinStamp identity, discovery, and resolution behavior."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from twinstamp.identity import Submission
from twinstamp.profiles import PHYSICAL_EXECUTION, PhysicalExecutionUnit
from twinstamp.reconcile import reconcile
from twinstamp.resolution import (
    EvidenceIssue,
    LeafAssessment,
    Seal,
    SealState,
    SelectionState,
    UnrecognizedEvidenceUnit,
)
from twinstamp.sealcheck import (
    CanonicalJsonMarker,
    MarkerIssue,
    MarkerState,
    parse_canonical_json_marker,
)
from twinstamp.testing import MemoryObjectStore

PREFIX = "answers/work/run-1"
PHYSICAL_KEY = "11111111-1111-4111-8111-111111111111"
MARKER = CanonicalJsonMarker("seal.json", 128)


def _resolve(
    store: MemoryObjectStore,
    *,
    profile: Any,
    validate: Any,
    settled: bool = True,
) -> Any:
    return reconcile(
        store,
        PREFIX,
        profile,
        Submission("submission-1"),
        MARKER,
        validate,
        settled=settled,
    )


def test_physical_profile_key_grammar_is_canonical() -> None:
    physical = PHYSICAL_EXECUTION.parse(PHYSICAL_KEY)
    assert physical is not None
    assert PHYSICAL_EXECUTION.render(physical) == PHYSICAL_KEY
    assert PHYSICAL_EXECUTION.parse("not-a-uuid") is None


def test_physical_execution_rejects_non_v4_uuid() -> None:
    with pytest.raises(ValueError, match="UUIDv4"):
        PhysicalExecutionUnit(uuid.uuid1())


def test_invalid_profile_child_is_retained_as_an_unrecognized_unit() -> None:
    store = MemoryObjectStore({f"{PREFIX}/not-a-uuid/seal.json": b"{}"})

    def validate(*_args: Any) -> LeafAssessment[Any, str]:
        raise AssertionError("unrecognized evidence must not reach domain validation")

    resolved = _resolve(store, profile=PHYSICAL_EXECUTION, validate=validate)
    assert resolved.profile is PHYSICAL_EXECUTION
    assert resolved.selection.state is SelectionState.INVALID
    unit = resolved.leaves[0].discovered.unit
    assert isinstance(unit, UnrecognizedEvidenceUnit)
    assert unit.key == "not-a-uuid"
    assert resolved.leaves[0].assessment.issue is EvidenceIssue.UNRECOGNIZED_UNIT
    assert store.read_calls == []


def test_pending_resolution_performs_no_listing() -> None:
    store = MemoryObjectStore({f"{PREFIX}/{PHYSICAL_KEY}/seal.json": b"{}"})
    resolved = _resolve(
        store,
        profile=PHYSICAL_EXECUTION,
        validate=lambda *_args: LeafAssessment(SealState.VALID, "unused", Seal("result.json")),
        settled=False,
    )
    assert resolved.selection.state is SelectionState.PENDING
    assert resolved.leaves == ()
    assert store.list_calls == []


def test_invalid_current_evidence_is_revalidated_against_prior_submissions() -> None:
    store = MemoryObjectStore({f"{PREFIX}/{PHYSICAL_KEY}/seal.json": b"{}\n"})
    current = Submission("submission-3")
    prior = (Submission("submission-2"), Submission("submission-1"))

    def validate(candidate: Any, submission: Submission) -> LeafAssessment[Any, str]:
        return LeafAssessment.valid("old", marker_key=candidate.marker.key, submission=prior[1])

    resolved = reconcile(
        store,
        PREFIX,
        PHYSICAL_EXECUTION,
        current,
        MARKER,
        validate,
    )

    assert resolved.selection.state is SelectionState.MISSING
    assert resolved.selected is None
    assert resolved.leaves[0].historical
    assert resolved.leaves[0].submission == prior[1]
    assert resolved.leaves[0].assessment.evidence == "old"
    assert [group.submission for group in resolved.submissions] == [current, prior[1]]
    assert resolved.submissions[1].units == resolved.leaves


def test_marker_is_read_once_and_passed_as_canonical_json() -> None:
    key = f"{PREFIX}/{PHYSICAL_KEY}/seal.json"
    store = MemoryObjectStore({key: b'{"answer":42}\n'})

    def validate(candidate: Any, _submission: Submission) -> LeafAssessment[Any, str]:
        assert candidate.marker.state is MarkerState.PRESENT
        assert candidate.marker.document == {"answer": 42}
        return LeafAssessment.valid("ok", marker_key=candidate.marker.key)

    resolved = _resolve(store, profile=PHYSICAL_EXECUTION, validate=validate)
    assert resolved.selection.state is SelectionState.SELECTED
    assert store.read_calls == [(key, 128)]


@pytest.mark.parametrize(
    ("content", "state", "issue"),
    [
        (b'{"answer": 42}\n', MarkerState.INVALID, MarkerIssue.NONCANONICAL),
        (b"x" * 129, MarkerState.INVALID, MarkerIssue.TOO_LARGE),
    ],
)
def test_marker_format_and_size_failures_are_typed(
    content: bytes, state: MarkerState, issue: MarkerIssue
) -> None:
    store = MemoryObjectStore({f"{PREFIX}/{PHYSICAL_KEY}/seal.json": content})

    resolved = _resolve(
        store,
        profile=PHYSICAL_EXECUTION,
        validate=lambda *_args: (_ for _ in ()).throw(AssertionError("validator called")),
    )
    assert resolved.leaves[0].marker is not None
    assert resolved.leaves[0].marker.state is state
    assert resolved.leaves[0].marker.issue is issue
    assert resolved.leaves[0].assessment.issue is EvidenceIssue.MARKER_INVALID


def test_missing_marker_is_distinct_from_invalid_marker() -> None:
    store = MemoryObjectStore({f"{PREFIX}/{PHYSICAL_KEY}/payload.bin": b"x"})

    resolved = _resolve(
        store,
        profile=PHYSICAL_EXECUTION,
        validate=lambda *_args: (_ for _ in ()).throw(AssertionError("validator called")),
    )
    assert resolved.selection.state is SelectionState.UNSEALED
    assert len(store.read_calls) == 1
    assert resolved.leaves[0].assessment.issue is EvidenceIssue.MARKER_ABSENT


@pytest.mark.parametrize("content", [None, b'{"answer": 42}\n'])
def test_validator_is_not_called_for_missing_or_malformed_marker(content: bytes | None) -> None:
    objects = (
        {f"{PREFIX}/{PHYSICAL_KEY}/seal.json": content}
        if content is not None
        else {f"{PREFIX}/{PHYSICAL_KEY}/payload.bin": b"x"}
    )
    store = MemoryObjectStore(objects)

    _resolve(
        store,
        profile=PHYSICAL_EXECUTION,
        validate=lambda *_args: (_ for _ in ()).throw(AssertionError("validator called")),
    )


@pytest.mark.parametrize(
    "content",
    [b'{"value":NaN}\n', b'{"value":Infinity}\n', b'{"value":-Infinity}\n'],
)
def test_canonical_json_rejects_nonfinite_numbers(content: bytes) -> None:
    observed = parse_canonical_json_marker(content, key="seal.json")
    assert observed.state is MarkerState.INVALID
    assert observed.issue is MarkerIssue.INVALID_UTF8_JSON


def test_canonical_json_turns_huge_integer_error_into_invalid_observation() -> None:
    content = b'{"value":' + b"1" * 5000 + b"}\n"
    observed = parse_canonical_json_marker(content, key="seal.json")
    assert observed.state is MarkerState.INVALID
    assert observed.issue is MarkerIssue.INVALID_UTF8_JSON


def test_canonical_json_turns_recursion_error_into_invalid_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recursive(*_args: Any, **_kwargs: Any) -> Any:
        raise RecursionError

    monkeypatch.setattr("twinstamp.sealcheck.json.loads", recursive)
    observed = parse_canonical_json_marker(b"{}\n", key="seal.json")
    assert observed.state is MarkerState.INVALID
    assert observed.issue is MarkerIssue.INVALID_UTF8_JSON
