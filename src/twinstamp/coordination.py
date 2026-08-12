"""Import-independent submission coordination over durable intent and jobs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import ClassVar, Generic, Literal, Protocol, TypeAlias, TypeVar

JobSpecT = TypeVar("JobSpecT")
ProgressT = TypeVar("ProgressT")
EffectKind: TypeAlias = Literal[
    "created",
    "adopted_exact",
    "rejected_no_effect",
    "collision",
    "ambiguous",
    "observed_exact",
    "observed_collision",
    "not_visible",
    "observation_ambiguous",
]
ClaimValues: TypeAlias = tuple[EffectKind, str | None, str | None, bool, str | None]


@dataclass(frozen=True, slots=True)
class SubmissionSpec(Generic[JobSpecT]):
    """One immutable provider submission, bound to exact canonical bytes."""

    key: str
    canonical_job_spec: bytes
    submission_spec_hash: str
    payload: JobSpecT

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("submission key must be non-empty")
        expected = hashlib.sha256(self.canonical_job_spec).hexdigest()
        if self.submission_spec_hash != expected:
            raise ValueError("submission_spec_hash does not match canonical_job_spec")


@dataclass(frozen=True, slots=True)
class ProviderEffectClaim:
    kind: EffectKind
    resource_name: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSettlementClaim:
    state: str | None
    settled: bool
    failure_type: str | None = None


class _ClaimedFact:
    def _claims(self) -> ClaimValues:
        if isinstance(self, Created | AdoptedExact | ObservedExact):
            return self._kind, self.resource_name, self.state, self.settled, None
        if isinstance(self, RejectedNoEffect):
            return "rejected_no_effect", None, self.state, True, self.failure_type
        if isinstance(self, Collision | ObservedCollision):
            kind: EffectKind = "collision" if isinstance(self, Collision) else "observed_collision"
            return kind, self.resource_name, self.state, self.settled, self.failure_type
        if isinstance(self, Ambiguous):
            return "ambiguous", None, None, False, None
        if isinstance(self, NotVisible):
            return "not_visible", None, None, False, None
        if isinstance(self, ObservationAmbiguous):
            return "observation_ambiguous", None, None, False, None
        raise AssertionError(f"unknown provider fact: {type(self).__name__}")

    @property
    def effect(self) -> ProviderEffectClaim:
        kind, resource, _state, _settled, _failure = self._claims()
        return ProviderEffectClaim(kind, resource)

    @property
    def settlement(self) -> ProviderSettlementClaim:
        _kind, _resource, state, settled, failure = self._claims()
        return ProviderSettlementClaim(state, settled, failure)


@dataclass(frozen=True, slots=True)
class Created(_ClaimedFact):
    _kind: ClassVar[EffectKind] = "created"
    resource_name: str
    state: str
    settled: bool = False


@dataclass(frozen=True, slots=True)
class AdoptedExact(_ClaimedFact):
    _kind: ClassVar[EffectKind] = "adopted_exact"
    resource_name: str
    state: str
    settled: bool = False


@dataclass(frozen=True, slots=True)
class RejectedNoEffect(_ClaimedFact):
    failure_type: str
    state: str = "NOT_CREATED"


@dataclass(frozen=True, slots=True)
class Collision(_ClaimedFact):
    failure_type: str
    resource_name: str | None = None
    state: str | None = None
    settled: bool = False


@dataclass(frozen=True, slots=True)
class Ambiguous(_ClaimedFact):
    reason: str
    error_type: str | None = None


EnsureFact: TypeAlias = Created | AdoptedExact | RejectedNoEffect | Collision | Ambiguous


@dataclass(frozen=True, slots=True)
class ObservedExact(_ClaimedFact):
    _kind: ClassVar[EffectKind] = "observed_exact"
    resource_name: str
    state: str
    settled: bool = False


@dataclass(frozen=True, slots=True)
class ObservedCollision(_ClaimedFact):
    failure_type: str
    resource_name: str | None = None
    state: str | None = None
    settled: bool = False


@dataclass(frozen=True, slots=True)
class NotVisible(_ClaimedFact):
    reason: str


@dataclass(frozen=True, slots=True)
class ObservationAmbiguous(_ClaimedFact):
    reason: str
    error_type: str | None = None


ObservationFact: TypeAlias = ObservedExact | ObservedCollision | NotVisible | ObservationAmbiguous


@dataclass(frozen=True, slots=True)
class SubmissionClaim(Generic[JobSpecT]):
    """Journal-owned claim metadata plus the immutable submission spec."""

    spec: SubmissionSpec[JobSpecT]
    token: str = ""


@dataclass(frozen=True, slots=True)
class EnsureResult(Generic[ProgressT]):
    progress: ProgressT
    fact: EnsureFact | None


class IntentJournal(Protocol[JobSpecT, ProgressT]):
    def claim_submission(self, key: str, *, now: str) -> SubmissionClaim[JobSpecT] | None: ...
    def existing_submission(self, key: str) -> ProgressT: ...
    def record_ensure(
        self, claim: SubmissionClaim[JobSpecT], fact: EnsureFact, *, now: str
    ) -> ProgressT: ...
    def observation_claims(self, *, now: str) -> Iterable[SubmissionClaim[JobSpecT]]: ...
    def record_observation(
        self, claim: SubmissionClaim[JobSpecT], fact: ObservationFact, *, now: str
    ) -> ProgressT | None: ...
    def progress(self) -> list[ProgressT]: ...


def ensure_submission(
    key: str,
    *,
    journal: IntentJournal[JobSpecT, ProgressT],
    ensure: Callable[[SubmissionSpec[JobSpecT]], EnsureFact],
    now: str,
) -> EnsureResult[ProgressT]:
    """Reserve/claim intent, perform one provider ensure, and durably record the fact."""

    claim = journal.claim_submission(key, now=now)
    if claim is None:
        return EnsureResult(journal.existing_submission(key), None)
    fact = ensure(claim.spec)
    return EnsureResult(journal.record_ensure(claim, fact, now=now), fact)


def ensure_claim(
    claim: SubmissionClaim[JobSpecT],
    *,
    journal: IntentJournal[JobSpecT, ProgressT],
    ensure: Callable[[SubmissionSpec[JobSpecT]], EnsureFact],
    now: str,
) -> EnsureResult[ProgressT]:
    """Perform provider ensure for a pre-reserved claim and durably record the fact."""

    fact = ensure(claim.spec)
    return EnsureResult(journal.record_ensure(claim, fact, now=now), fact)


def observe_submissions(
    *,
    journal: IntentJournal[JobSpecT, ProgressT],
    observe: Callable[[SubmissionSpec[JobSpecT]], ObservationFact],
    now: str,
) -> list[ProgressT]:
    """Observe active provider effects once and let the journal settle safe transitions."""

    for claim in journal.observation_claims(now=now):
        journal.record_observation(claim, observe(claim.spec), now=now)
    return journal.progress()
