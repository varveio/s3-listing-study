"""Profile-generic TwinStamp identity, discovery, and resolution behavior."""

from __future__ import annotations

from typing import Any

import pytest

from twinstamp import (
    LOGICAL_ATTEMPT,
    PHYSICAL_EXECUTION,
    AnyTwoCurrentChildrenAmbiguous,
    LeafAssessment,
    LogicalAttemptUnit,
    PublicationConflict,
    ResultSlot,
    Seal,
    SealState,
    SelectionState,
    Submission,
    UnrecognizedEvidenceUnit,
    resolve_slot,
)
from twinstamp.stores import (
    AmbiguousCreateState,
    fixed_publication_order,
    resolve_ambiguous_create,
)
from twinstamp.testing import MemoryObjectStore

PREFIX = "answers/work/run-1"
PHYSICAL_KEY = "11111111-1111-4111-8111-111111111111"
LOGICAL_KEY = "logical-task-0-retry-0-runnable-0"


def _resolve(
    store: MemoryObjectStore,
    *,
    profile: Any,
    validate: Any,
    settled: bool = True,
) -> Any:
    return resolve_slot(
        store=store,
        slot=ResultSlot(
            work_item="work", slot="run-1", prefix=PREFIX, profile=profile
        ),
        submission=Submission("submission-1"),
        settled=settled,
        validate=validate,
        policy=AnyTwoCurrentChildrenAmbiguous(),
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


def test_profile_is_carried_structurally_by_the_generic_slot() -> None:
    slot: ResultSlot[LogicalAttemptUnit] = ResultSlot(
        work_item="work", slot="run-1", prefix=PREFIX, profile=LOGICAL_ATTEMPT
    )
    assert slot.profile is LOGICAL_ATTEMPT


def test_foreign_profile_child_is_retained_as_an_invalid_anomaly() -> None:
    store = MemoryObjectStore({f"{PREFIX}/{LOGICAL_KEY}/seal.json": b"{}"})

    def validate(discovered: Any, _submission: Submission) -> LeafAssessment[Any, str]:
        assert isinstance(discovered.unit, UnrecognizedEvidenceUnit)
        return LeafAssessment(SealState.INVALID, "foreign")

    resolved = _resolve(store, profile=PHYSICAL_EXECUTION, validate=validate)
    assert resolved.profile is PHYSICAL_EXECUTION
    assert resolved.selection.state is SelectionState.INVALID
    unit = resolved.leaves[0].discovered.unit
    assert isinstance(unit, UnrecognizedEvidenceUnit)
    assert unit.anomaly == "foreign_profile"
    assert unit.foreign_profile == "logical-attempt"


def test_logical_engine_retries_are_distinct_coordinates() -> None:
    retry = "logical-task-0-retry-1-runnable-0"
    store = MemoryObjectStore(
        {
            f"{PREFIX}/{LOGICAL_KEY}/seal.json": b"{}",
            f"{PREFIX}/{retry}/seal.json": b"{}",
        }
    )

    def validate(discovered: Any, _submission: Submission) -> LeafAssessment[Any, str]:
        return LeafAssessment(SealState.VALID, discovered.key, Seal("result.json"))

    resolved = _resolve(store, profile=LOGICAL_ATTEMPT, validate=validate)
    units = [leaf.discovered.unit for leaf in resolved.leaves]
    assert units == [
        LogicalAttemptUnit(task_index=0, retry_index=0, runnable_index=0),
        LogicalAttemptUnit(task_index=0, retry_index=1, runnable_index=0),
    ]
    assert resolved.selection.state is SelectionState.DUPLICATE


def test_same_coordinate_logical_collision_is_a_publication_conflict() -> None:
    store = MemoryObjectStore({f"{PREFIX}/{LOGICAL_KEY}/seal.json": b"{}"})

    def validate(discovered: Any, _submission: Submission) -> LeafAssessment[Any, str]:
        assert isinstance(discovered.unit, LogicalAttemptUnit)
        conflict = PublicationConflict(
            discovered.unit,
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
    store = MemoryObjectStore({f"{PREFIX}/{LOGICAL_KEY}/seal.json": b"{}"})

    def validate(discovered: Any, _submission: Submission) -> LeafAssessment[Any, str]:
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
        validate=lambda *_args: LeafAssessment(
            SealState.VALID, "unused", Seal("result.json")
        ),
        settled=False,
    )
    assert resolved.selection.state is SelectionState.PENDING
    assert resolved.leaves == ()
    assert store.list_calls == []


def test_publication_order_is_fixed_and_marker_last() -> None:
    assert fixed_publication_order(("z.raw", "a.raw"), "result.json") == (
        "a.raw",
        "z.raw",
        "result.json",
    )


def test_ambiguous_create_requires_versioned_digest_readback() -> None:
    assert resolve_ambiguous_create(
        expected_digest="abc", observed_digest=None, observed_version=None
    ).state is AmbiguousCreateState.UNRESOLVED
    assert resolve_ambiguous_create(
        expected_digest="abc", observed_digest="abc", observed_version=7
    ).state is AmbiguousCreateState.CONFIRMED
    assert resolve_ambiguous_create(
        expected_digest="abc", observed_digest="def", observed_version=8
    ).state is AmbiguousCreateState.CONFLICT
