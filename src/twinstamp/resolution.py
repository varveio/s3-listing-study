"""Profile-generic evidence and selection values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

from twinstamp.identity import Submission
from twinstamp.profiles import EvidenceProfile
from twinstamp.sealcheck import MarkerObservation

U = TypeVar("U")
T = TypeVar("T")


class SealState(StrEnum):
    """Validation state of one leaf's evidence publication.

    These states say nothing about selection, execution success, or
    domain correctness.  Only ``VALID`` is accompanied by a :class:`Seal`.
    """

    UNSEALED = "unsealed"
    VALID = "valid"
    INVALID = "invalid"


class EvidenceIssue(StrEnum):
    """Core-owned reason a leaf never reached domain validation."""

    UNRECOGNIZED_UNIT = "unrecognized_unit"
    MARKER_ABSENT = "marker_absent"
    MARKER_INVALID = "marker_invalid"


class SelectionState(StrEnum):
    """The slot-level outcome over current leaves.

    This state is distinct from :class:`SealState`: for example, duplicate
    valid leaves yield ``DUPLICATE`` and one unsealed leaf may yield
    ``UNSEALED``.
    """

    PENDING = "pending"
    MISSING = "missing"
    SELECTED = "selected"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    UNSEALED = "unsealed"


@dataclass(frozen=True, slots=True)
class UnrecognizedEvidenceUnit:
    """A discovered child whose key is invalid for the selected profile."""

    key: str


@dataclass(frozen=True, slots=True)
class Seal:
    """A nonempty marker key witnessing a fully validated, marker-last unit.

    A seal attests to complete evidence publication only.  It does not attest
    to process success, domain correctness, or a cryptographic signature.
    """

    marker_key: str

    def __post_init__(self) -> None:
        if not self.marker_key:
            raise ValueError("seal marker key must not be empty")


@dataclass(frozen=True, slots=True)
class LeafAssessment(Generic[U, T]):
    """Domain validation or core-owned state for one discovered evidence unit.

    ``evidence`` and the optional outcome/verdict are opaque to the core.
    ``submission`` attributes a fully validated leaf to an earlier submission;
    leaving it absent attributes the leaf to the current submission.
    Exactly ``VALID`` assessments carry a ``seal``.

    Raises:
        ValueError: If seal presence does not match ``seal_state``.
    """

    seal_state: SealState
    evidence: T | None
    seal: Seal | None = None
    execution_outcome: object | None = None
    domain_verdict: object | None = None
    submission: Submission | None = None
    issue: EvidenceIssue | None = None

    def __post_init__(self) -> None:
        if (self.seal_state is SealState.VALID) != (self.seal is not None):
            raise ValueError("exactly valid evidence must carry a seal witness")
        if self.issue is not None:
            if self.evidence is not None or self.seal_state is SealState.VALID:
                raise ValueError(
                    "core-owned evidence issues cannot carry domain evidence or a seal"
                )
        elif self.evidence is None:
            raise ValueError("domain assessments must carry evidence")

    @classmethod
    def valid(
        cls,
        evidence: T,
        *,
        marker_key: str,
        submission: Submission | None = None,
        execution_outcome: object | None = None,
        domain_verdict: object | None = None,
    ) -> LeafAssessment[U, T]:
        """Build a valid assessment carrying its required seal witness."""

        return cls(
            SealState.VALID,
            evidence,
            Seal(marker_key),
            execution_outcome=execution_outcome,
            domain_verdict=domain_verdict,
            submission=submission,
        )

    @classmethod
    def unsealed(cls, evidence: T) -> LeafAssessment[U, T]:
        """Build an incomplete, unsealed assessment."""

        return cls(SealState.UNSEALED, evidence)

    @classmethod
    def invalid(cls, evidence: T) -> LeafAssessment[U, T]:
        """Build an invalid assessment without a seal witness."""

        return cls(SealState.INVALID, evidence)

    @classmethod
    def system(cls, state: SealState, issue: EvidenceIssue) -> LeafAssessment[U, T]:
        """Build a core-owned assessment for evidence not sent to the validator."""

        expected = {
            EvidenceIssue.UNRECOGNIZED_UNIT: SealState.INVALID,
            EvidenceIssue.MARKER_ABSENT: SealState.UNSEALED,
            EvidenceIssue.MARKER_INVALID: SealState.INVALID,
        }[issue]
        if state is not expected:
            raise ValueError("system evidence state must match its issue")
        return cls(state, None, issue=issue)


@dataclass(frozen=True, slots=True)
class DiscoveredUnit(Generic[U]):
    """A canonical child key paired with its parsed unit or retained anomaly."""

    key: str
    unit: U | UnrecognizedEvidenceUnit


@dataclass(frozen=True, slots=True)
class CanonicalEvidenceUnit(Generic[U]):
    """A recognized unit with one present, canonical marker observation."""

    key: str
    unit: U
    marker: MarkerObservation

    def __post_init__(self) -> None:
        if self.marker.document is None:
            raise ValueError("canonical evidence input requires a parsed marker document")


@dataclass(frozen=True, slots=True)
class LeafEvidence(Generic[U, T]):
    """An assessed unit associated with its current or historical submission."""

    discovered: DiscoveredUnit[U]
    submission: Submission
    assessment: LeafAssessment[U, T]
    marker: MarkerObservation | None = None
    historical: bool = False


@dataclass(frozen=True, slots=True)
class SubmissionResolution(Generic[U, T]):
    """All retained leaves associated with one submission generation.

    ``current`` identifies the submission passed to reconciliation; other
    entries are caller-classified historical evidence.
    """

    submission: Submission
    current: bool
    units: tuple[LeafEvidence[U, T], ...]


@dataclass(frozen=True, slots=True)
class Selection:
    """A selection result independent from individual leaf seal states.

    Only ``SELECTED`` carries ``selected_key``; every other state must leave it
    absent.
    """

    state: SelectionState
    selected_key: str | None = None

    def __post_init__(self) -> None:
        if (self.state is SelectionState.SELECTED) != (self.selected_key is not None):
            raise ValueError("only a selected resolution names a selected key")


@dataclass(frozen=True, slots=True)
class SlotResolution(Generic[U, T]):
    """The complete reconciliation snapshot for one slot.

    ``leaves`` is the single source of truth. ``submissions`` is a computed
    grouping by current or historical generation, and ``selection`` is the
    independent strict exact-one result.
    """

    prefix: str
    profile: EvidenceProfile[U]
    submission: Submission
    leaves: tuple[LeafEvidence[U, T], ...]
    selection: Selection

    @property
    def submissions(self) -> tuple[SubmissionResolution[U, T], ...]:
        """Group the canonical leaf collection without storing it twice."""

        grouped: dict[Submission, list[LeafEvidence[U, T]]] = {self.submission: []}
        for leaf in self.leaves:
            grouped.setdefault(leaf.submission, []).append(leaf)
        return (
            SubmissionResolution(self.submission, True, tuple(grouped.pop(self.submission))),
            *(
                SubmissionResolution(key, False, tuple(value))
                for key, value in sorted(grouped.items(), key=lambda item: item[0].key)
            ),
        )

    @property
    def selected(self) -> LeafEvidence[U, T] | None:
        """Return the selected current leaf, or ``None`` when none was selected."""

        key = self.selection.selected_key
        if key is None:
            return None
        return next(
            leaf for leaf in self.leaves if not leaf.historical and leaf.discovered.key == key
        )

    @property
    def evidence(self) -> tuple[T, ...]:
        """Return caller evidence in discovery order."""

        return tuple(
            leaf.assessment.evidence for leaf in self.leaves if leaf.assessment.evidence is not None
        )

    @property
    def selected_evidence(self) -> T | None:
        """Return caller evidence for the selected leaf, if any."""

        leaf = self.selected
        return leaf.assessment.evidence if leaf is not None else None
