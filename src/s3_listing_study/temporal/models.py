from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BatchJobSpec:
    project: str
    location: str
    job_id: str
    job: dict[str, Any]

    @property
    def resource_name(self) -> str:
        return f"projects/{self.project}/locations/{self.location}/jobs/{self.job_id}"


@dataclass(frozen=True)
class CampaignWorkflowInput:
    cases: tuple[BatchJobSpec, ...]


@dataclass(frozen=True)
class BatchJobOutcome:
    resource_name: str
    state: str
