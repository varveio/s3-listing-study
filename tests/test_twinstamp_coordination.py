"""Pure submission coordination facts and orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import pytest

from twinstamp.coordination import (
    AdoptedExact,
    Ambiguous,
    Created,
    EnsureFact,
    NotVisible,
    ObservationAmbiguous,
    ObservationFact,
    ObservedExact,
    RejectedNoEffect,
    SubmissionClaim,
    SubmissionSpec,
    ensure_submission,
    observe_submissions,
)


@dataclass(frozen=True, slots=True)
class Progress:
    key: str
    fact: object | None = None


class Journal:
    def __init__(self, claim: SubmissionClaim[str]) -> None:
        self.claim = claim
        self.events: list[str] = []
        self.recorded: list[object] = []

    def claim_submission(self, key: str, *, now: str) -> SubmissionClaim[str] | None:
        assert now == "now"
        self.events.append(f"claim:{key}")
        return self.claim if key == self.claim.spec.key else None

    def existing_submission(self, key: str) -> Progress:
        self.events.append(f"existing:{key}")
        return Progress(key)

    def record_ensure(
        self, claim: SubmissionClaim[str], fact: EnsureFact, *, now: str
    ) -> Progress:
        assert claim == self.claim
        assert now == "now"
        self.events.append(f"record:{type(fact).__name__}")
        self.recorded.append(fact)
        return Progress(claim.spec.key, fact)

    def observation_claims(self, *, now: str) -> list[SubmissionClaim[str]]:
        assert now == "now"
        self.events.append("observe-claims")
        return [self.claim]

    def record_observation(
        self, claim: SubmissionClaim[str], fact: ObservationFact, *, now: str
    ) -> Progress:
        assert claim == self.claim
        assert now == "now"
        self.events.append(f"observe:{type(fact).__name__}")
        self.recorded.append(fact)
        return Progress(claim.spec.key, fact)

    def progress(self) -> list[Progress]:
        self.events.append("progress")
        return [Progress(self.claim.spec.key, self.recorded[-1] if self.recorded else None)]


class Backend:
    def __init__(self, *, ensure: EnsureFact, observe: ObservationFact) -> None:
        self.ensure_fact = ensure
        self.observe_fact = observe
        self.events: list[str] = []

    def ensure(self, spec: SubmissionSpec[str]) -> EnsureFact:
        self.events.append(f"ensure:{spec.key}")
        return self.ensure_fact

    def observe(self, spec: SubmissionSpec[str]) -> ObservationFact:
        self.events.append(f"observe:{spec.key}")
        return self.observe_fact


def spec(key: str = "job-1", payload: str = "payload") -> SubmissionSpec[str]:
    canonical = payload.encode()
    return SubmissionSpec(key, canonical, hashlib.sha256(canonical).hexdigest(), payload)


def test_submission_spec_verifies_exact_canonical_bytes() -> None:
    canonical = b'{"job":1}\n'
    digest = hashlib.sha256(canonical).hexdigest()
    assert SubmissionSpec("job", canonical, digest, {"job": 1}).canonical_job_spec == canonical
    with pytest.raises(ValueError, match="does not match"):
        SubmissionSpec("job", canonical + b" ", digest, {})


def test_ensure_reserves_before_provider_and_records_fact() -> None:
    claim = SubmissionClaim(spec(), "first")
    journal = Journal(claim)
    backend = Backend(ensure=Created("resource", "QUEUED"), observe=ObservedExact("r", "RUNNING"))

    result = ensure_submission("job-1", journal=journal, ensure=backend.ensure, now="now")

    assert isinstance(result.fact, Created)
    assert result.progress.fact == result.fact
    assert journal.events == ["claim:job-1", "record:Created"]
    assert backend.events == ["ensure:job-1"]


def test_adopted_exact_is_only_a_fact_not_policy() -> None:
    fact = AdoptedExact("resource", "QUEUED")
    result = ensure_submission(
        "job-1",
        journal=Journal(SubmissionClaim(spec(), "first")),
        ensure=Backend(ensure=fact, observe=ObservedExact("r", "RUNNING")).ensure,
        now="now",
    )

    assert result.fact is fact
    assert result.fact.settlement.failure_type is None


@pytest.mark.parametrize(
    "fact",
    [
        RejectedNoEffect("PermanentGoogleError"),
        Ambiguous("unknown create outcome"),
        NotVisible("not visible"),
        ObservationAmbiguous("unknown observation"),
    ],
)
def test_fact_settlement_claims_are_structural(fact: Any) -> None:
    assert fact.effect.kind
    if isinstance(fact, RejectedNoEffect):
        assert fact.settlement.settled
        assert fact.settlement.state == "NOT_CREATED"
    else:
        assert not fact.settlement.settled


def test_observe_submissions_records_observation_fact_without_absence_policy() -> None:
    claim = SubmissionClaim(spec(), "observe")
    journal = Journal(claim)
    backend = Backend(
        ensure=Created("resource", "QUEUED"),
        observe=ObservationAmbiguous("provider read timed out"),
    )

    progress = observe_submissions(journal=journal, observe=backend.observe, now="now")

    assert isinstance(progress[0].fact, ObservationAmbiguous)
    assert journal.events == ["observe-claims", "observe:ObservationAmbiguous", "progress"]
    assert backend.events == ["observe:job-1"]
