from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from twinstamp.coordination import SubmissionSpec


def canonical_job_json(job: dict[str, Any]) -> str:
    return json.dumps(job, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class BatchJobSpec:
    project: str
    location: str
    base_job_id: str
    job_id: str
    job: dict[str, Any]
    controller_timeout_s: int
    submission: int = 1
    job_json: str | None = None

    @property
    def resource_name(self) -> str:
        return f"projects/{self.project}/locations/{self.location}/jobs/{self.job_id}"

    def submission_spec(self) -> SubmissionSpec[BatchJobSpec]:
        encoded = (self.job_json or canonical_job_json(self.job)).encode()
        return SubmissionSpec(self.job_id, encoded, hashlib.sha256(encoded).hexdigest(), self)


@dataclass(frozen=True, slots=True)
class CaseControllerProgress:
    job_id: str
    phase: str
    provider_state: str | None
    failure_type: str | None
    provider_resource_name: str | None = None
    provider_settled: bool = False
    current_submission: int = 1
    current_job_id: str | None = None
    accepted_failure: bool = False


def retry_job(spec: BatchJobSpec, submission: int) -> BatchJobSpec:
    if submission != spec.submission + 1:
        raise ValueError("submission must be exactly current + 1")
    stem, separator, original = spec.base_job_id.rpartition("-s")
    if not separator or original != "1" or submission < 1 or submission > 99:
        raise ValueError("base Batch job ID is not a submission-1 identity")
    current_job_id = f"{stem}-s{submission}"
    job = deepcopy(spec.job)
    try:
        commands = job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
    except (KeyError, IndexError, TypeError):
        commands = None
    if not isinstance(commands, list) or any(not isinstance(value, str) for value in commands):
        raise ValueError("Batch worker command is not retryable")
    for flag, old, new in (
        ("--job-id", spec.job_id, current_job_id),
        ("--submission-number", str(spec.submission), str(submission)),
    ):
        positions = [index for index, value in enumerate(commands) if value == flag]
        if len(positions) != 1 or positions[0] + 1 >= len(commands):
            raise ValueError("Batch worker command omits retry identity")
        value_index = positions[0] + 1
        if commands[value_index] != old:
            raise ValueError("Batch worker command retry identity does not match")
        commands[value_index] = new
    return BatchJobSpec(
        spec.project,
        spec.location,
        spec.base_job_id,
        current_job_id,
        job,
        spec.controller_timeout_s,
        submission,
        canonical_job_json(job),
    )
