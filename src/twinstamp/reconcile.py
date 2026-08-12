"""Pure orchestration of discovery, validation, history, and selection."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from twinstamp.discovery import discover_units
from twinstamp.identity import ResultSlot, Submission
from twinstamp.policy import SelectionPolicy
from twinstamp.resolution import (
    DiscoveredUnit,
    HistoricalClassification,
    LeafAssessment,
    LeafEvidence,
    SealState,
    Selection,
    SelectionState,
    SlotResolution,
    SubmissionResolution,
    UnrecognizedEvidenceUnit,
)
from twinstamp.stores import ObjectStoreReader

U = TypeVar("U")
T = TypeVar("T")

LeafValidator = Callable[[DiscoveredUnit[U], Submission], LeafAssessment[U, T]]
HistoricalClassifier = Callable[
    [DiscoveredUnit[U], LeafAssessment[U, T]], HistoricalClassification[U, T] | None
]


def resolve_slot(
    *,
    store: ObjectStoreReader,
    slot: ResultSlot[U],
    submission: Submission,
    settled: bool,
    validate: LeafValidator[U, T],
    policy: SelectionPolicy[U, T],
    classify_historical: HistoricalClassifier[U, T] | None = None,
    max_children: int = 256,
) -> SlotResolution[U, T]:
    """Resolve one slot without collapsing evidence dimensions into one status."""

    if not settled:
        current = SubmissionResolution[U, T](submission, True, ())
        return SlotResolution(slot, (current,), (), Selection(SelectionState.PENDING))

    leaves: list[LeafEvidence[U, T]] = []
    for discovered in discover_units(
        store, slot.prefix, slot.profile, max_children=max_children
    ):
        assessment = validate(discovered, submission)
        if (
            assessment.publication_conflict is not None
            and assessment.publication_conflict.unit != discovered.unit
        ):
            raise ValueError("a publication conflict must name its discovered unit")
        if (
            isinstance(discovered.unit, UnrecognizedEvidenceUnit)
            and assessment.seal_state is not SealState.INVALID
        ):
            raise ValueError("a foreign or malformed unit key cannot contain valid evidence")
        historical = (
            classify_historical(discovered, assessment)
            if classify_historical is not None and assessment.seal_state is SealState.INVALID
            else None
        )
        if historical is None:
            leaves.append(LeafEvidence(discovered, submission, assessment))
        else:
            leaves.append(
                LeafEvidence(
                    discovered,
                    historical.submission,
                    historical.assessment,
                    historical=True,
                )
            )

    ordered = tuple(leaves)
    current_leaves = tuple(leaf for leaf in ordered if not leaf.historical)
    grouped: dict[Submission, list[LeafEvidence[U, T]]] = {submission: list(current_leaves)}
    for leaf in ordered:
        if leaf.historical:
            grouped.setdefault(leaf.submission, []).append(leaf)
    submissions = (
        SubmissionResolution(submission, True, tuple(grouped.pop(submission))),
        *(
            SubmissionResolution(key, False, tuple(value))
            for key, value in sorted(grouped.items(), key=lambda item: item[0].key)
        ),
    )
    return SlotResolution(slot, submissions, ordered, policy.select(current_leaves))
