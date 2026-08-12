"""Pure orchestration of discovery, validation, history, and selection."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from twinstamp.discovery import discover_units
from twinstamp.identity import Submission
from twinstamp.profiles import EvidenceProfile
from twinstamp.resolution import (
    CanonicalEvidenceUnit,
    EvidenceIssue,
    LeafAssessment,
    LeafEvidence,
    SealState,
    Selection,
    SelectionState,
    SlotResolution,
    UnrecognizedEvidenceUnit,
)
from twinstamp.sealcheck import CanonicalJsonMarker, MarkerState
from twinstamp.stores import ObjectStoreReader

U = TypeVar("U")
T = TypeVar("T")

LeafValidator = Callable[[CanonicalEvidenceUnit[U], Submission], LeafAssessment[U, T]]


def reconcile(
    store: ObjectStoreReader,
    prefix: str,
    profile: EvidenceProfile[U],
    submission: Submission,
    marker: CanonicalJsonMarker,
    validate: LeafValidator[U, T],
    *,
    settled: bool = True,
    max_children: int = 256,
) -> SlotResolution[U, T]:
    """Reconcile one settled slot without collapsing its evidence state axes.

    Args:
        store: Object-store reader used only after the provider effect settles.
        prefix: Caller-owned object-store namespace.
        profile: Homogeneous evidence-unit grammar for the namespace.
        submission: Current deliberate provider-job generation.
        settled: Whether the provider effect is settled.  ``False`` returns a
            pending resolution without listing storage.
        marker: Bounded canonical marker convention observed once per
            recognized unit.
        validate: Caller-supplied domain assessment, invoked only for a
            recognized unit with a present canonical marker.
        max_children: Hard accepted-child discovery bound.

    Returns:
        A resolution retaining all leaves and submissions plus an independent
        selection result.  Historical leaves are retained but excluded from
        current-leaf selection.

    Raises:
        ValueError: If a validator attributes invalid evidence to history or
            names a different marker.
        ChildLimitExceeded: If discovery exceeds ``max_children``.
    """

    if not prefix:
        raise ValueError("prefix must not be empty")

    if not settled:
        return SlotResolution(prefix, profile, submission, (), Selection(SelectionState.PENDING))

    leaves: list[LeafEvidence[U, T]] = []
    for discovered in discover_units(store, prefix, profile, max_children=max_children):
        if isinstance(discovered.unit, UnrecognizedEvidenceUnit):
            leaves.append(
                LeafEvidence(
                    discovered,
                    submission,
                    LeafAssessment.system(SealState.INVALID, EvidenceIssue.UNRECOGNIZED_UNIT),
                )
            )
            continue
        observed = marker.observe(store, f"{prefix}/{discovered.key}")
        if observed.state is not MarkerState.PRESENT:
            state, issue = (
                (SealState.UNSEALED, EvidenceIssue.MARKER_ABSENT)
                if observed.state is MarkerState.ABSENT
                else (SealState.INVALID, EvidenceIssue.MARKER_INVALID)
            )
            leaves.append(
                LeafEvidence(
                    discovered,
                    submission,
                    LeafAssessment.system(state, issue),
                    observed,
                )
            )
            continue
        assessment = validate(
            CanonicalEvidenceUnit(discovered.key, discovered.unit, observed), submission
        )
        if assessment.seal is not None and assessment.seal.marker_key != observed.key:
            raise ValueError("a seal must name the marker that reconciliation observed")
        owner = assessment.submission or submission
        historical = owner != submission
        if historical and assessment.seal_state is not SealState.VALID:
            raise ValueError("historical evidence must pass full validation")
        leaves.append(LeafEvidence(discovered, owner, assessment, observed, historical))

    ordered = tuple(leaves)
    current_leaves = tuple(leaf for leaf in ordered if not leaf.historical)
    return SlotResolution(prefix, profile, submission, ordered, _select(current_leaves))


def _select(current: tuple[LeafEvidence[U, T], ...]) -> Selection:
    """Apply the strict exactly-one-current-unit rule."""
    if not current:
        return Selection(SelectionState.MISSING)
    if len(current) > 1:
        return Selection(SelectionState.DUPLICATE)
    leaf = current[0]
    if leaf.assessment.seal_state is SealState.VALID:
        return Selection(SelectionState.SELECTED, leaf.discovered.key)
    if leaf.assessment.seal_state is SealState.UNSEALED:
        return Selection(SelectionState.UNSEALED)
    return Selection(SelectionState.INVALID)
