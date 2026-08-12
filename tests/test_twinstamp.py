"""Profile-generic TwinStamp identity, discovery, and resolution behavior."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from twinstamp import (
    LOGICAL_ATTEMPT,
    PHYSICAL_EXECUTION,
    CanonicalJsonMarker,
    EvidenceIssue,
    LeafAssessment,
    LogicalAttemptUnit,
    MarkerIssue,
    MarkerState,
    PhysicalExecutionUnit,
    PublicationConflict,
    ResultSlot,
    Seal,
    SealState,
    SelectExactlyOne,
    SelectionState,
    Submission,
    UnrecognizedEvidenceUnit,
    parse_canonical_json_marker,
    reconcile,
)
from twinstamp.testing import MemoryObjectStore

PREFIX = "answers/work/run-1"
PHYSICAL_KEY = "11111111-1111-4111-8111-111111111111"
LOGICAL_KEY = "logical-task-0-retry-0-runnable-0"
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
        policy=SelectExactlyOne(),
    )


def test_profile_key_grammars_are_canonical_and_disjoint() -> None:
    physical = PHYSICAL_EXECUTION.parse(PHYSICAL_KEY)
    logical = LOGICAL_ATTEMPT.parse(LOGICAL_KEY)
    assert physical is not None
    assert logical == LogicalAttemptUnit(task_index=0, retry_index=0, runnable_index=0)
    assert PHYSICAL_EXECUTION.render(physical) == PHYSICAL_KEY
    assert LOGICAL_ATTEMPT.render(logical) == LOGICAL_KEY
    assert PHYSICAL_EXECUTION.parse(LOGICAL_KEY) is None
    assert LOGICAL_ATTEMPT.parse(PHYSICAL_KEY) is None
    assert LOGICAL_ATTEMPT.parse("logical-task-00-retry-0-runnable-0") is None


def test_profile_value_objects_reject_noncanonical_coordinates() -> None:
    with pytest.raises(ValueError, match="UUIDv4"):
        PhysicalExecutionUnit(uuid.uuid1())
    for value in (True, 1.5, "1"):
        with pytest.raises(TypeError, match="integers"):
            LogicalAttemptUnit(value, 0, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-negative"):
        LogicalAttemptUnit(-1, 0, 0)


def test_profile_is_carried_structurally_by_the_generic_slot() -> None:
    slot: ResultSlot[LogicalAttemptUnit] = ResultSlot(PREFIX, LOGICAL_ATTEMPT)
    assert slot.profile is LOGICAL_ATTEMPT


def test_foreign_profile_child_is_retained_as_an_invalid_anomaly() -> None:
    store = MemoryObjectStore({f"{PREFIX}/{LOGICAL_KEY}/seal.json": b"{}"})

    def validate(*_args: Any) -> LeafAssessment[Any, str]:
        raise AssertionError("foreign evidence must not reach domain validation")

    resolved = _resolve(store, profile=PHYSICAL_EXECUTION, validate=validate)
    assert resolved.profile is PHYSICAL_EXECUTION
    assert resolved.selection.state is SelectionState.INVALID
    unit = resolved.leaves[0].discovered.unit
    assert isinstance(unit, UnrecognizedEvidenceUnit)
    assert unit.anomaly == "foreign_profile"
    assert unit.foreign_profile == "logical-attempt"
    assert resolved.leaves[0].assessment.issue is EvidenceIssue.UNRECOGNIZED_UNIT
    assert store.read_calls == []


def test_logical_engine_retries_are_distinct_coordinates() -> None:
    retry = "logical-task-0-retry-1-runnable-0"
    store = MemoryObjectStore(
        {
            f"{PREFIX}/{LOGICAL_KEY}/seal.json": b"{}\n",
            f"{PREFIX}/{retry}/seal.json": b"{}\n",
        }
    )

    def validate(candidate: Any, _submission: Submission) -> LeafAssessment[Any, str]:
        return LeafAssessment(SealState.VALID, candidate.key, Seal(candidate.marker.key))

    resolved = _resolve(store, profile=LOGICAL_ATTEMPT, validate=validate)
    units = [leaf.discovered.unit for leaf in resolved.leaves]
    assert units == [
        LogicalAttemptUnit(task_index=0, retry_index=0, runnable_index=0),
        LogicalAttemptUnit(task_index=0, retry_index=1, runnable_index=0),
    ]
    assert resolved.selection.state is SelectionState.DUPLICATE


def test_same_coordinate_logical_collision_is_a_publication_conflict() -> None:
    store = MemoryObjectStore({f"{PREFIX}/{LOGICAL_KEY}/seal.json": b"{}\n"})

    def validate(candidate: Any, _submission: Submission) -> LeafAssessment[Any, str]:
        assert isinstance(candidate.unit, LogicalAttemptUnit)
        conflict = PublicationConflict(
            candidate.unit,
            f"{PREFIX}/{LOGICAL_KEY}/artifact.bin",
            "conditional_create_conflict",
        )
        return LeafAssessment(
            SealState.INVALID,
            "conflict",
            publication_conflict=conflict,
        )

    resolved = _resolve(store, profile=LOGICAL_ATTEMPT, validate=validate)
    assert len(resolved.leaves) == 1
    assert resolved.selection.state is SelectionState.PUBLICATION_CONFLICT
    assert resolved.leaves[0].assessment.publication_conflict is not None


def test_publication_conflict_rejects_a_different_unit() -> None:
    store = MemoryObjectStore({f"{PREFIX}/{LOGICAL_KEY}/seal.json": b"{}\n"})

    def validate(candidate: Any, _submission: Submission) -> LeafAssessment[Any, str]:
        conflict = PublicationConflict(
            LogicalAttemptUnit(task_index=0, retry_index=1, runnable_index=0),
            f"{PREFIX}/{LOGICAL_KEY}/artifact.bin",
            "conditional_create_conflict",
        )
        return LeafAssessment(
            SealState.INVALID,
            "conflict",
            publication_conflict=conflict,
        )

    with pytest.raises(ValueError, match="must name its discovered unit"):
        _resolve(store, profile=LOGICAL_ATTEMPT, validate=validate)


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
        policy=SelectExactlyOne(),
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
