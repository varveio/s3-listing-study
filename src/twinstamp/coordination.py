"""Import-independent submission coordination over durable intent and jobs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeAlias, TypeVar

JobSpecT = TypeVar("JobSpecT")
ProgressT = TypeVar("ProgressT")


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
    """Provider resource structurally associated with a coordination fact."""

    resource_name: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSettlementClaim:
    """Provider state and whether it definitively ended the requested effect."""

    state: str | None
    settled: bool
    failure_type: str | None = None


class _ClaimedFact:
    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim()

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(None, False)


class _ExactFact(_ClaimedFact):
    resource_name: str
    state: str
    settled: bool

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim(self.resource_name)

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(self.state, self.settled)


class _CollisionFact(_ClaimedFact):
    failure_type: str
    resource_name: str | None
    state: str | None
    settled: bool

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim(self.resource_name)

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(self.state, self.settled, self.failure_type)


@dataclass(frozen=True, slots=True)
class Created(_ExactFact):
    """The ensure call created the exact requested resource."""

    resource_name: str
    state: str
    settled: bool = False


@dataclass(frozen=True, slots=True)
class AdoptedExact(_ExactFact):
    """The resource already existed and exactly matched immutable intent."""

    resource_name: str
    state: str
    settled: bool = False


@dataclass(frozen=True, slots=True)
class RejectedNoEffect(_ClaimedFact):
    """The provider definitively rejected the request without creating anything."""

    failure_type: str
    state: str = "NOT_CREATED"

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(self.state, True, self.failure_type)


@dataclass(frozen=True, slots=True)
class Collision(_CollisionFact):
    """The deterministic resource identity belongs to different immutable intent."""

    failure_type: str
    resource_name: str | None = None
    state: str | None = None
    settled: bool = False


@dataclass(frozen=True, slots=True)
class Ambiguous(_ClaimedFact):
    """The ensure call may have taken effect, so safe settlement is unknown."""

    reason: str
    error_type: str | None = None


EnsureFact: TypeAlias = Created | AdoptedExact | RejectedNoEffect | Collision | Ambiguous


@dataclass(frozen=True, slots=True)
class ObservedExact(_ExactFact):
    """Observation found the exact requested resource and its current state."""

    resource_name: str
    state: str
    settled: bool = False


@dataclass(frozen=True, slots=True)
class ObservedCollision(_CollisionFact):
    """Observation found different immutable intent at the resource identity."""

    failure_type: str
    resource_name: str | None = None
    state: str | None = None
    settled: bool = False


@dataclass(frozen=True, slots=True)
class NotVisible(_ClaimedFact):
    """Observation found no visible resource but cannot prove it never existed."""

    reason: str


@dataclass(frozen=True, slots=True)
class ObservationAmbiguous(_ClaimedFact):
    """Observation failed without establishing resource state or settlement."""

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
    """Journal progress and the ensure fact, if a provider call was made.

    ``fact is None`` means the journal declined a claim and no provider call
    occurred; ``progress`` is then the already-recorded state.
    """

    progress: ProgressT
    fact: EnsureFact | None


class IntentJournal(Protocol[JobSpecT, ProgressT]):
    """Durable reservation and projection boundary for provider coordination.

    ``claim_submission`` atomically reserves work and returns ``None`` when the
    caller must not contact the provider. ``observation_claims`` yields active
    effects safe to inspect. Record methods durably project only facts the
    implementation can settle; ``record_observation`` intentionally returns no
    progress because a later ``progress`` snapshot is authoritative.
    """

    def claim_submission(self, key: str, *, now: str) -> SubmissionClaim[JobSpecT] | None: ...
    def existing_submission(self, key: str) -> ProgressT: ...
    def record_ensure(
        self, claim: SubmissionClaim[JobSpecT], fact: EnsureFact, *, now: str
    ) -> ProgressT: ...
    def observation_claims(self) -> Iterable[SubmissionClaim[JobSpecT]]: ...
    def record_observation(
        self, claim: SubmissionClaim[JobSpecT], fact: ObservationFact, *, now: str
    ) -> None: ...
    def progress(self) -> list[ProgressT]: ...


def ensure_submission(
    key: str,
    *,
    journal: IntentJournal[JobSpecT, ProgressT],
    ensure: Callable[[SubmissionSpec[JobSpecT]], EnsureFact],
    now: str,
) -> EnsureResult[ProgressT]:
    """Reserve intent, call ``ensure`` once, and durably record its outcome.

    The callable must return an :data:`EnsureFact` or raise. A raised exception
    is recorded as :class:`Ambiguous` before the original exception propagates,
    because the provider effect may already have happened.
    """

    claim = journal.claim_submission(key, now=now)
    if claim is None:
        return EnsureResult(journal.existing_submission(key), None)
    return _ensure_and_record(claim, journal=journal, ensure=ensure, now=now)


def ensure_claim(
    claim: SubmissionClaim[JobSpecT],
    *,
    journal: IntentJournal[JobSpecT, ProgressT],
    ensure: Callable[[SubmissionSpec[JobSpecT]], EnsureFact],
    now: str,
) -> EnsureResult[ProgressT]:
    """Ensure a caller-reserved claim with the same exception contract as normal ensure."""

    return _ensure_and_record(claim, journal=journal, ensure=ensure, now=now)


def _ensure_and_record(
    claim: SubmissionClaim[JobSpecT],
    *,
    journal: IntentJournal[JobSpecT, ProgressT],
    ensure: Callable[[SubmissionSpec[JobSpecT]], EnsureFact],
    now: str,
) -> EnsureResult[ProgressT]:
    """Call one provider ensure and project an ambiguity before propagating failure."""

    try:
        fact = ensure(claim.spec)
    except Exception as exc:
        journal.record_ensure(
            claim,
            Ambiguous(str(exc) or "provider ensure raised", type(exc).__name__),
            now=now,
        )
        raise
    return EnsureResult(journal.record_ensure(claim, fact, now=now), fact)


def observe_submissions(
    *,
    journal: IntentJournal[JobSpecT, ProgressT],
    observe: Callable[[SubmissionSpec[JobSpecT]], ObservationFact],
    now: str,
) -> list[ProgressT]:
    """Observe active provider effects once and let the journal settle safe transitions."""

    for claim in journal.observation_claims():
        journal.record_observation(claim, observe(claim.spec), now=now)
    return journal.progress()
