"""Import-independent submission coordination over durable intent and jobs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeAlias, TypeVar

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


@dataclass(frozen=True, slots=True)
class Created:
    resource_name: str
    state: str
    settled: bool = False

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim("created", self.resource_name)

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(self.state, self.settled)


@dataclass(frozen=True, slots=True)
class AdoptedExact:
    resource_name: str
    state: str
    settled: bool = False

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim("adopted_exact", self.resource_name)

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(self.state, self.settled)


@dataclass(frozen=True, slots=True)
class RejectedNoEffect:
    failure_type: str
    state: str = "NOT_CREATED"

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim("rejected_no_effect")

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(self.state, True, self.failure_type)


@dataclass(frozen=True, slots=True)
class Collision:
    failure_type: str
    resource_name: str | None = None
    state: str | None = None
    settled: bool = False

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim("collision", self.resource_name)

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(self.state, self.settled, self.failure_type)


@dataclass(frozen=True, slots=True)
class Ambiguous:
    reason: str
    error_type: str | None = None

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim("ambiguous")

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(None, False)


EnsureFact: TypeAlias = Created | AdoptedExact | RejectedNoEffect | Collision | Ambiguous


@dataclass(frozen=True, slots=True)
class ObservedExact:
    resource_name: str
    state: str
    settled: bool = False

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim("observed_exact", self.resource_name)

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(self.state, self.settled)


@dataclass(frozen=True, slots=True)
class ObservedCollision:
    failure_type: str
    resource_name: str | None = None
    state: str | None = None
    settled: bool = False

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim("observed_collision", self.resource_name)

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(self.state, self.settled, self.failure_type)


@dataclass(frozen=True, slots=True)
class NotVisible:
    reason: str

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim("not_visible")

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(None, False)


@dataclass(frozen=True, slots=True)
class ObservationAmbiguous:
    reason: str
    error_type: str | None = None

    @property
    def effect(self) -> ProviderEffectClaim:
        return ProviderEffectClaim("observation_ambiguous")

    @property
    def settlement(self) -> ProviderSettlementClaim:
        return ProviderSettlementClaim(None, False)


ObservationFact: TypeAlias = (
    ObservedExact | ObservedCollision | NotVisible | ObservationAmbiguous
)


@dataclass(frozen=True, slots=True)
class SubmissionClaim(Generic[JobSpecT]):
    """Journal-owned claim metadata plus the immutable submission spec."""

    spec: SubmissionSpec[JobSpecT]
    token: str = ""


@dataclass(frozen=True, slots=True)
class EnsureResult(Generic[ProgressT]):
    progress: ProgressT
    fact: EnsureFact | None


class JobBackend(Protocol[JobSpecT]):
    def ensure(self, spec: SubmissionSpec[JobSpecT]) -> EnsureFact: ...
    def observe(self, spec: SubmissionSpec[JobSpecT]) -> ObservationFact: ...


@dataclass(frozen=True, slots=True)
class FunctionBackend(Generic[JobSpecT]):
    ensure_fn: Callable[[SubmissionSpec[JobSpecT]], EnsureFact]
    observe_fn: Callable[[SubmissionSpec[JobSpecT]], ObservationFact]

    def ensure(self, spec: SubmissionSpec[JobSpecT]) -> EnsureFact:
        return self.ensure_fn(spec)

    def observe(self, spec: SubmissionSpec[JobSpecT]) -> ObservationFact:
        return self.observe_fn(spec)


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
    backend: JobBackend[JobSpecT],
    now: str,
) -> EnsureResult[ProgressT]:
    """Reserve/claim intent, perform one provider ensure, and durably record the fact."""

    claim = journal.claim_submission(key, now=now)
    if claim is None:
        return EnsureResult(journal.existing_submission(key), None)
    fact = backend.ensure(claim.spec)
    return EnsureResult(journal.record_ensure(claim, fact, now=now), fact)


def ensure_claim(
    claim: SubmissionClaim[JobSpecT],
    *,
    journal: IntentJournal[JobSpecT, ProgressT],
    backend: JobBackend[JobSpecT],
    now: str,
) -> EnsureResult[ProgressT]:
    """Perform provider ensure for a pre-reserved claim and durably record the fact."""

    fact = backend.ensure(claim.spec)
    return EnsureResult(journal.record_ensure(claim, fact, now=now), fact)


def observe_submissions(
    *,
    journal: IntentJournal[JobSpecT, ProgressT],
    backend: JobBackend[JobSpecT],
    now: str,
) -> list[ProgressT]:
    """Observe active provider effects once and let the journal settle safe transitions."""

    for claim in journal.observation_claims(now=now):
        journal.record_observation(claim, backend.observe(claim.spec), now=now)
    return journal.progress()
