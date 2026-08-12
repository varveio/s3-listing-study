"""Profile-generic evidence and selection values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

from twinstamp.identity import ResultSlot, Submission
from twinstamp.profiles import EvidenceProfile

U = TypeVar("U")
T = TypeVar("T")


class SealState(StrEnum):
    UNSEALED = "unsealed"
    VALID = "valid"
    INVALID = "invalid"


class SelectionState(StrEnum):
    PENDING = "pending"
    MISSING = "missing"
    SELECTED = "selected"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    UNSEALED = "unsealed"
    PUBLICATION_CONFLICT = "publication_conflict"


@dataclass(frozen=True, slots=True)
class UnrecognizedEvidenceUnit:
    key: str
    anomaly: str
    foreign_profile: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationConflict(Generic[U]):
    """Two writers contended for one logical coordinate and cannot be split."""

    unit: U
    object_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class Seal:
    """A validated marker-last witness for one complete evidence unit."""

    marker_key: str

    def __post_init__(self) -> None:
        if not self.marker_key:
            raise ValueError("seal marker key must not be empty")


@dataclass(frozen=True, slots=True)
class LeafAssessment(Generic[U, T]):
    seal_state: SealState
    evidence: T
    seal: Seal | None = None
    execution_outcome: object | None = None
    domain_verdict: object | None = None
    publication_conflict: PublicationConflict[U] | None = None

    def __post_init__(self) -> None:
        if (self.seal_state is SealState.VALID) != (self.seal is not None):
            raise ValueError("exactly valid evidence must carry a seal witness")
        if self.publication_conflict is not None and self.seal_state is not SealState.INVALID:
            raise ValueError("a publication conflict must be invalid evidence")


@dataclass(frozen=True, slots=True)
class DiscoveredUnit(Generic[U]):
    key: str
    unit: U | UnrecognizedEvidenceUnit


@dataclass(frozen=True, slots=True)
class LeafEvidence(Generic[U, T]):
    discovered: DiscoveredUnit[U]
    submission: Submission
    assessment: LeafAssessment[U, T]
    historical: bool = False


@dataclass(frozen=True, slots=True)
class SubmissionResolution(Generic[U, T]):
    submission: Submission
    current: bool
    units: tuple[LeafEvidence[U, T], ...]


@dataclass(frozen=True, slots=True)
class Selection:
    state: SelectionState
    selected_key: str | None = None

    def __post_init__(self) -> None:
        if (self.state is SelectionState.SELECTED) != (self.selected_key is not None):
            raise ValueError("only a selected resolution names a selected key")


@dataclass(frozen=True, slots=True)
class SlotResolution(Generic[U, T]):
    slot: ResultSlot[U]
    submissions: tuple[SubmissionResolution[U, T], ...]
    leaves: tuple[LeafEvidence[U, T], ...]
    selection: Selection

    @property
    def profile(self) -> EvidenceProfile[U]:
        return self.slot.profile


@dataclass(frozen=True, slots=True)
class HistoricalClassification(Generic[U, T]):
    submission: Submission
    assessment: LeafAssessment[U, T]

    def __post_init__(self) -> None:
        if self.assessment.seal_state is not SealState.VALID:
            raise ValueError("historical evidence must pass full seal revalidation")
