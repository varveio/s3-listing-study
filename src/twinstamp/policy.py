"""Selection policies kept separate from discovery and seal validity."""

from __future__ import annotations

from typing import Protocol, TypeVar

from twinstamp.resolution import LeafEvidence, SealState, Selection, SelectionState

U = TypeVar("U")
T = TypeVar("T")


class SelectionPolicy(Protocol[U, T]):
    def select(self, current: tuple[LeafEvidence[U, T], ...]) -> Selection: ...


class AnyTwoCurrentChildrenAmbiguous:
    """The study's frozen rule: any two current children are duplicates."""

    def select(self, current: tuple[LeafEvidence[U, T], ...]) -> Selection:
        if not current:
            return Selection(SelectionState.MISSING)
        if len(current) > 1:
            return Selection(SelectionState.DUPLICATE)
        leaf = current[0]
        if leaf.assessment.publication_conflict is not None:
            return Selection(SelectionState.PUBLICATION_CONFLICT)
        if leaf.assessment.seal_state is SealState.VALID:
            return Selection(SelectionState.SELECTED, leaf.discovered.key)
        if leaf.assessment.seal_state is SealState.UNSEALED:
            return Selection(SelectionState.UNSEALED)
        return Selection(SelectionState.INVALID)


class ValidSealsOnly:
    """A convention default which does not let torn leaves hide one valid seal."""

    def select(self, current: tuple[LeafEvidence[U, T], ...]) -> Selection:
        conflicts = [leaf for leaf in current if leaf.assessment.publication_conflict is not None]
        if conflicts:
            return Selection(SelectionState.PUBLICATION_CONFLICT)
        valid = [leaf for leaf in current if leaf.assessment.seal_state is SealState.VALID]
        if len(valid) > 1:
            return Selection(SelectionState.DUPLICATE)
        if valid:
            return Selection(SelectionState.SELECTED, valid[0].discovered.key)
        if not current:
            return Selection(SelectionState.MISSING)
        if any(leaf.assessment.seal_state is SealState.INVALID for leaf in current):
            return Selection(SelectionState.INVALID)
        return Selection(SelectionState.UNSEALED)
