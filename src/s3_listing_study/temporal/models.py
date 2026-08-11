from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BatchJobSpec:
    project: str
    location: str
    job_id: str
    job: dict[str, Any]
    controller_timeout_s: int

    @property
    def resource_name(self) -> str:
        return f"projects/{self.project}/locations/{self.location}/jobs/{self.job_id}"


@dataclass(frozen=True)
class CampaignWorkflowInput:
    cases: tuple[BatchJobSpec, ...]
    campaign_digest: str


@dataclass(frozen=True)
class BatchJobOutcome:
    resource_name: str
    state: str
    failure_type: str | None = None


@dataclass(frozen=True)
class BatchJobHandle:
    resource_name: str
    state: str
    failure_type: str | None = None


@dataclass(frozen=True)
class RetryCaseRequest:
    job_id: str
    submission: int


@dataclass(frozen=True)
class CaseControllerProgress:
    job_id: str
    child_run_id: str | None
    phase: str
    provider_state: str | None
    failure_type: str | None
    provider_resource_name: str | None = None
    provider_settled: bool = False
    current_submission: int = 1
    current_job_id: str | None = None
