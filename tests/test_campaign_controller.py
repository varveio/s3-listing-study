"""SQLite controller settlement, retry, finalization, and pacing contract."""

from __future__ import annotations

import json
import sqlite3
from multiprocessing import get_context
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, cast

import pytest
from google.api_core.exceptions import AlreadyExists, ServiceUnavailable

from s3_listing_study.manager.campaign import cli as campaign_cli
from s3_listing_study.manager.campaign import control, controller, ledger, provider, report
from s3_listing_study.manager.campaign.control import finalize_parser, retry_parser
from s3_listing_study.manager.campaign.models import (
    BatchJobSpec,
    CaseControllerProgress,
)
from tests.test_campaign_batch import attempt
from twinstamp import (
    AdoptedExact,
    Ambiguous,
    Collision,
    Created,
    NotVisible,
    ObservationAmbiguous,
    ObservedCollision,
    ObservedExact,
    RejectedNoEffect,
    SubmissionClaim,
    SubmissionSpec,
)

CAMPAIGN = "2026-08-10-first"
NOW = "2026-08-11T12:00:00Z"
FROZEN_PRE_EXTRACTION_SCHEMA = """
CREATE TABLE attempts (
 job_id TEXT PRIMARY KEY, campaign TEXT NOT NULL, run_ordinal INTEGER NOT NULL,
 submission INTEGER NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, bucket TEXT NOT NULL, region TEXT NOT NULL, tool TEXT NOT NULL,
 case_id TEXT NOT NULL, mode TEXT NOT NULL, machine_type TEXT NOT NULL,
 vcpus INTEGER NOT NULL, memory_gb INTEGER NOT NULL, container_memory_gb INTEGER,
 timeout_s INTEGER NOT NULL, env_json TEXT NOT NULL, derived_image TEXT NOT NULL,
 case_fingerprint TEXT NOT NULL, fingerprint TEXT NOT NULL, prefix TEXT NOT NULL,
 case_json TEXT NOT NULL, UNIQUE (campaign, fingerprint, run_ordinal, submission));
CREATE TABLE events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, at TEXT NOT NULL,
 event TEXT NOT NULL, detail TEXT, FOREIGN KEY (job_id) REFERENCES attempts (job_id));
CREATE INDEX events_by_job ON events (job_id, id);
CREATE TABLE campaigns (
 campaign TEXT PRIMARY KEY, project TEXT NOT NULL, location TEXT NOT NULL,
 results_bucket TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 finalized_at TEXT);
CREATE TABLE controller_inputs (
 base_job_id TEXT PRIMARY KEY, campaign TEXT NOT NULL, job_json TEXT NOT NULL,
 controller_timeout_s INTEGER NOT NULL, FOREIGN KEY (campaign) REFERENCES campaigns (campaign));
CREATE TABLE controller_cases (
 base_job_id TEXT PRIMARY KEY, campaign TEXT NOT NULL, phase TEXT NOT NULL,
 current_submission INTEGER NOT NULL, current_job_id TEXT NOT NULL UNIQUE,
 job_json TEXT NOT NULL, controller_timeout_s INTEGER NOT NULL, provider_state TEXT,
 failure_type TEXT, provider_resource_name TEXT, provider_settled INTEGER NOT NULL DEFAULT 0,
 accepted_failure INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
 FOREIGN KEY (campaign) REFERENCES campaigns (campaign));
CREATE INDEX controller_cases_by_campaign ON controller_cases (campaign, base_job_id);
"""


def payload(spec: SubmissionSpec[BatchJobSpec]) -> BatchJobSpec:
    return spec.payload


def job(job_id: str) -> dict[str, Any]:
    return {
        "labels": {"s3-study-attempt": "a" * 52},
        "taskGroups": [
            {
                "taskCount": "1",
                "parallelism": "1",
                "taskSpec": {
                    "runnables": [
                        {
                            "container": {
                                "imageUri": "registry/image@sha256:" + "a" * 64,
                                "commands": [
                                    "--job-id",
                                    job_id,
                                    "--submission-number",
                                    "1",
                                ],
                            }
                        }
                    ],
                    "maxRetryCount": 0,
                },
            }
        ],
        "allocationPolicy": {"instances": [{"policy": {"machineType": "n4-highcpu-2"}}]},
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }


def awaiting_retry(path: Path) -> str:
    selected = attempt()
    base_job_id = selected.job_id
    with ledger.open_ledger(path) as connection:
        ledger.register_campaign(
            connection,
            campaign=CAMPAIGN,
            project="study",
            location="us-east1",
            results_bucket="results",
            manifest_sha256="a" * 64,
            cases=[
                {
                    "base_job_id": base_job_id,
                    "job": job(base_job_id),
                    "controller_timeout_s": 9000,
                }
            ],
            now=NOW,
        )
        ledger.record_intent(connection, attempt=selected.as_dict(), campaign=CAMPAIGN, now=NOW)
    journal = ledger.SQLiteIntentJournal(path, CAMPAIGN)
    claim = journal.claim_submission(base_job_id, now=NOW)
    assert claim is not None
    journal.record_ensure(
        claim,
        Created(f"projects/study/locations/us-east1/jobs/{base_job_id}", "FAILED", True),
        now=NOW,
    )
    return base_job_id


def test_late_ensure_after_terminal_observation_returns_current_progress(tmp_path: Path) -> None:
    path = tmp_path / "campaign.sqlite3"
    selected = attempt()
    with ledger.open_ledger(path) as connection:
        ledger.register_campaign(
            connection,
            campaign=CAMPAIGN,
            project="study",
            location="us-east1",
            results_bucket="results",
            manifest_sha256="a" * 64,
            cases=[
                {
                    "base_job_id": selected.job_id,
                    "job": job(selected.job_id),
                    "controller_timeout_s": 9000,
                }
            ],
            now=NOW,
        )
        ledger.record_intent(connection, attempt=selected.as_dict(), campaign=CAMPAIGN, now=NOW)
    journal = ledger.SQLiteIntentJournal(path, CAMPAIGN)
    claim = journal.claim_submission(selected.job_id, now=NOW)
    assert claim is not None
    resource = f"projects/study/locations/us-east1/jobs/{selected.job_id}"
    observed = journal.record_observation(
        SubmissionClaim(claim.spec, "observe"), ObservedExact(resource, "SUCCEEDED", True), now=NOW
    )
    assert observed is not None and observed.phase == "terminal"

    late = journal.record_ensure(claim, Created(resource, "RUNNING", False), now=NOW)

    assert late == observed
    [current] = controller.progress(ledger_path=path, campaign=CAMPAIGN)
    assert current == observed


def test_definitive_no_effect_waits_for_operator_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = attempt()
    monkeypatch.setattr(
        provider,
        "ensure_batch_job",
        lambda _spec: RejectedNoEffect("PermanentGoogleError"),
    )

    statuses = controller.start_campaign(
        ledger_path=tmp_path / "campaign.sqlite3",
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        results_bucket="results",
        manifest_sha256="a" * 64,
        attempts=[selected.as_dict()],
        jobs=[job(selected.job_id)],
        controller_timeouts=[9000],
    )

    assert statuses == [{"job_id": selected.job_id, "state": "NOT_CREATED"}]
    [progress] = controller.progress(ledger_path=tmp_path / "campaign.sqlite3", campaign=CAMPAIGN)
    assert progress.phase == "awaiting_retry"
    assert progress.provider_settled
    assert progress.provider_resource_name is None


def test_retry_reservation_excludes_concurrent_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    base_job_id = awaiting_retry(path)

    def ensure(spec: SubmissionSpec[BatchJobSpec]) -> Created | AdoptedExact | Ambiguous:
        batch = payload(spec)
        assert batch.job_id.endswith("-s2")
        commands = batch.job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
        assert commands == ["--job-id", batch.job_id, "--submission-number", "2"]
        with pytest.raises(ledger.LedgerError, match="active cases"):
            controller.finalize(ledger_path=path, campaign=CAMPAIGN)
        return Created(batch.resource_name, "SUCCEEDED", True)

    monkeypatch.setattr(provider, "ensure_batch_job", ensure)
    progress = controller.retry_case(
        ledger_path=path, campaign=CAMPAIGN, base_job_id=base_job_id, submission=2
    )

    assert progress.phase == "terminal"
    assert progress.current_submission == 2
    assert progress.current_job_id is not None and progress.current_job_id.endswith("-s2")


def test_stale_provider_observation_cannot_overwrite_retry_or_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    base_job_id = awaiting_retry(path)
    monkeypatch.setattr(
        provider,
        "ensure_batch_job",
        lambda spec: Created(payload(spec).resource_name, "FAILED", True),
    )
    retried = controller.retry_case(
        ledger_path=path, campaign=CAMPAIGN, base_job_id=base_job_id, submission=2
    )
    assert retried.phase == "awaiting_retry"
    [finalized] = controller.finalize(ledger_path=path, campaign=CAMPAIGN)
    assert finalized.accepted_failure

    stale = SubmissionClaim(
        BatchJobSpec("study", "us-east1", base_job_id, base_job_id, job(base_job_id), 9000)
        .submission_spec(),
        "observe",
    )
    assert (
        ledger.SQLiteIntentJournal(path, CAMPAIGN).record_observation(
            stale,
            ObservedExact(
                f"projects/study/locations/us-east1/jobs/{base_job_id}", "SUCCEEDED", True
            ),
            now=NOW,
        )
        is None
    )
    [current] = controller.progress(ledger_path=path, campaign=CAMPAIGN)
    assert current.current_submission == 2
    assert current.provider_state == "FAILED"
    assert current.accepted_failure


def test_same_job_stale_observation_cannot_regress_terminal_or_finalized_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    success_path = tmp_path / "success.sqlite3"
    selected = attempt()
    monkeypatch.setattr(
        provider,
        "ensure_batch_job",
        lambda spec: Created(payload(spec).resource_name, "SUCCEEDED", True),
    )
    controller.start_campaign(
        ledger_path=success_path,
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        results_bucket="results",
        manifest_sha256="a" * 64,
        attempts=[selected.as_dict()],
        jobs=[job(selected.job_id)],
        controller_timeouts=[9000],
    )
    claim = SubmissionClaim(
        BatchJobSpec(
            "study", "us-east1", selected.job_id, selected.job_id, job(selected.job_id), 9000
        ).submission_spec(),
        "observe",
    )
    assert (
        ledger.SQLiteIntentJournal(success_path, CAMPAIGN).record_observation(
            claim,
            ObservedExact(
                f"projects/study/locations/us-east1/jobs/{selected.job_id}", "RUNNING", False
            ),
            now=NOW,
        )
        is None
    )
    [successful] = controller.progress(ledger_path=success_path, campaign=CAMPAIGN)
    assert successful.phase == "terminal"
    assert successful.provider_state == "SUCCEEDED"

    finalized_path = tmp_path / "finalized.sqlite3"
    base_job_id = awaiting_retry(finalized_path)
    controller.finalize(ledger_path=finalized_path, campaign=CAMPAIGN)
    claim = SubmissionClaim(
        BatchJobSpec("study", "us-east1", base_job_id, base_job_id, job(base_job_id), 9000)
        .submission_spec(),
        "observe",
    )
    assert (
        ledger.SQLiteIntentJournal(finalized_path, CAMPAIGN).record_observation(
            claim,
            ObservedExact(
                f"projects/study/locations/us-east1/jobs/{base_job_id}", "SUCCEEDED", True
            ),
            now=NOW,
        )
        is None
    )
    [finalized] = controller.progress(ledger_path=finalized_path, campaign=CAMPAIGN)
    assert finalized.accepted_failure
    assert finalized.provider_state == "FAILED"


def test_retry_requires_exact_next_submission_and_finalize_accepts_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    base_job_id = awaiting_retry(path)
    monkeypatch.setattr(
        provider,
        "ensure_batch_job",
        lambda _spec: pytest.fail("invalid retry contacted Batch"),
    )
    with pytest.raises(controller.ControllerError, match=r"exactly current \+ 1"):
        controller.retry_case(
            ledger_path=path, campaign=CAMPAIGN, base_job_id=base_job_id, submission=3
        )

    [progress] = controller.finalize(ledger_path=path, campaign=CAMPAIGN)
    assert progress.phase == "terminal"
    assert progress.accepted_failure
    assert progress.provider_state == "FAILED"


def test_persisted_retry_reservation_uses_clean_redrive_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    base_job_id = awaiting_retry(path)

    def ambiguous(_spec: SubmissionSpec[BatchJobSpec]) -> Ambiguous:
        return Ambiguous("create outcome unknown", "ProviderError")

    monkeypatch.setattr(provider, "ensure_batch_job", ambiguous)
    unsettled = controller.retry_case(
        ledger_path=path,
        campaign=CAMPAIGN,
        base_job_id=base_job_id,
        submission=2,
    )
    assert unsettled.phase == "running"
    assert unsettled.current_submission == 2
    assert unsettled.provider_resource_name is None

    calls: list[str] = []

    def redrive(spec: SubmissionSpec[BatchJobSpec]) -> AdoptedExact:
        batch = payload(spec)
        calls.append(batch.job_id)
        return AdoptedExact(batch.resource_name, "QUEUED")

    monkeypatch.setattr(provider, "ensure_batch_job", redrive)
    progress = controller.retry_case(
        ledger_path=path,
        campaign=CAMPAIGN,
        base_job_id=base_job_id,
        submission=2,
    )

    assert calls == [progress.current_job_id]
    assert progress.phase == "running"
    assert progress.current_submission == 2
    assert progress.failure_type is None


def test_submission_starts_in_waves_of_eight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = attempt().as_dict()
    attempts: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    for index in range(9):
        selected = dict(base)
        selected["job_id"] = f"c-case-{index}-s1"
        selected["fingerprint"] = f"{index:064x}"
        selected["attempt_fingerprint"] = selected["fingerprint"]
        selected["prefix"] = f"campaigns/{CAMPAIGN}/results/b/t/c/run-{index + 1}"
        selected["run_ordinal"] = index + 1
        attempts.append(selected)
        jobs.append(job(selected["job_id"]))
    sleeps: list[float] = []
    monkeypatch.setattr("s3_listing_study.manager.campaign.controller.time.sleep", sleeps.append)
    monkeypatch.setattr(
        provider,
        "ensure_batch_job",
        lambda spec: Created(payload(spec).resource_name, "QUEUED"),
    )

    controller.start_campaign(
        ledger_path=tmp_path / "campaign.sqlite3",
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        results_bucket="results",
        manifest_sha256="a" * 64,
        attempts=attempts,
        jobs=jobs,
        controller_timeouts=[9000] * 9,
    )

    assert sleeps == [1.0]


def test_concurrent_start_redrive_serializes_the_provider_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    selected = attempt()
    entered = Event()
    release = Event()
    second_provider_call = Event()
    guard = Lock()
    calls = 0

    def ensure(spec: SubmissionSpec[BatchJobSpec], **_kwargs: Any) -> Created:
        nonlocal calls
        with guard:
            calls += 1
            if calls == 2:
                second_provider_call.set()
        entered.set()
        assert release.wait(2)
        return Created(payload(spec).resource_name, "QUEUED")

    monkeypatch.setattr(provider, "ensure_batch_job", ensure)
    kwargs = {
        "ledger_path": path,
        "campaign": CAMPAIGN,
        "project": "study",
        "location": "us-east1",
        "results_bucket": "results",
        "manifest_sha256": "a" * 64,
        "attempts": [selected.as_dict()],
        "jobs": [job(selected.job_id)],
        "controller_timeouts": [9000],
    }
    errors: list[BaseException] = []

    def start() -> None:
        try:
            controller.start_campaign(**kwargs)  # type: ignore[arg-type]
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = Thread(target=start)
    second = Thread(target=start)
    first.start()
    assert entered.wait(2)
    second.start()
    assert not second_provider_call.wait(0.2)
    release.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert calls == 1


def test_concurrent_start_redrive_serializes_provider_effect_across_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    try:
        context = get_context("fork")
    except ValueError:  # pragma: no cover - fork is available on the Linux runner.
        pytest.skip("process lock test requires fork")
    path = tmp_path / "campaign.sqlite3"
    selected = attempt()
    entered = context.Event()
    release = context.Event()
    second_provider_call = context.Event()
    calls = context.Value("i", 0)
    errors = context.Queue()

    def ensure(spec: SubmissionSpec[BatchJobSpec], **_kwargs: Any) -> Created:
        with calls.get_lock():
            calls.value += 1
            if calls.value == 2:
                second_provider_call.set()
        entered.set()
        assert release.wait(2)
        return Created(payload(spec).resource_name, "QUEUED")

    monkeypatch.setattr(provider, "ensure_batch_job", ensure)
    kwargs = {
        "ledger_path": path,
        "campaign": CAMPAIGN,
        "project": "study",
        "location": "us-east1",
        "results_bucket": "results",
        "manifest_sha256": "a" * 64,
        "attempts": [selected.as_dict()],
        "jobs": [job(selected.job_id)],
        "controller_timeouts": [9000],
    }

    def start() -> None:
        try:
            controller.start_campaign(**kwargs)  # type: ignore[arg-type]
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.put(repr(exc))

    first = context.Process(target=start)
    second = context.Process(target=start)
    first.start()
    assert entered.wait(2)
    second.start()
    assert not second_provider_call.wait(0.2)
    release.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive() and not second.is_alive()
    assert errors.empty()
    assert calls.value == 1


def test_concurrent_retry_redrive_serializes_the_provider_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    base_job_id = awaiting_retry(path)
    entered = Event()
    release = Event()
    second_provider_call = Event()
    guard = Lock()
    calls = 0

    def ensure(spec: SubmissionSpec[BatchJobSpec], **_kwargs: Any) -> Created:
        nonlocal calls
        with guard:
            calls += 1
            if calls == 2:
                second_provider_call.set()
        entered.set()
        assert release.wait(2)
        return Created(payload(spec).resource_name, "QUEUED")

    monkeypatch.setattr(provider, "ensure_batch_job", ensure)
    errors: list[BaseException] = []

    def retry() -> None:
        try:
            controller.retry_case(
                ledger_path=path,
                campaign=CAMPAIGN,
                base_job_id=base_job_id,
                submission=2,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = Thread(target=retry)
    second = Thread(target=retry)
    first.start()
    assert entered.wait(2)
    second.start()
    assert not second_provider_call.wait(0.2)
    release.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert calls == 1
    [current] = controller.progress(ledger_path=path, campaign=CAMPAIGN)
    assert current.current_submission == 2
    assert current.provider_resource_name is not None


def test_transient_start_failure_does_not_stop_later_cases_and_is_redrivable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    first = attempt().as_dict()
    second = dict(first)
    second["job_id"] = "c-2026-08-10-first-swath-recursive-ffffffff-r2-s1"
    second["fingerprint"] = "b" * 64
    second["attempt_fingerprint"] = second["fingerprint"]
    second["prefix"] = str(first["prefix"]).removesuffix("run-1") + "run-2"
    second["run_ordinal"] = 2
    attempts = [first, second]
    jobs = [job(str(item["job_id"])) for item in attempts]
    calls: list[str] = []

    def ensure(spec: SubmissionSpec[BatchJobSpec]) -> Created | AdoptedExact | Ambiguous:
        batch = payload(spec)
        calls.append(batch.job_id)
        if len(calls) == 1:
            return Ambiguous("create outcome unknown", "ProviderError")
        if batch.job_id == first["job_id"]:
            return AdoptedExact(batch.resource_name, "QUEUED")
        return Created(batch.resource_name, "QUEUED")

    monkeypatch.setattr(provider, "ensure_batch_job", ensure)
    statuses = controller.start_campaign(
        ledger_path=path,
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        results_bucket="results",
        manifest_sha256="a" * 64,
        attempts=attempts,
        jobs=jobs,
        controller_timeouts=[9000, 9000],
    )

    assert statuses[0]["state"] == "unsettled"
    assert statuses[0]["error_type"] == "ProviderError"
    assert statuses[1]["state"] == "QUEUED"
    progress = controller.progress(ledger_path=path, campaign=CAMPAIGN)
    assert progress[0].phase == "running" and progress[0].provider_resource_name is None
    assert progress[1].provider_resource_name is not None

    statuses = controller.start_campaign(
        ledger_path=path,
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        results_bucket="results",
        manifest_sha256="a" * 64,
        attempts=attempts,
        jobs=jobs,
        controller_timeouts=[9000, 9000],
    )
    assert calls[-1] == str(first["job_id"])
    assert statuses[0]["state"] == "QUEUED"
    [redriven] = controller.progress(ledger_path=path, campaign=CAMPAIGN)[:1]
    assert redriven.failure_type is None


def test_provider_adoption_normalizes_only_materialized_fields() -> None:
    base_job_id = "c-case-s1"
    selected = BatchJobSpec("study", "us-east1", base_job_id, base_job_id, job(base_job_id), 9000)
    actual = provider._job_from_json(job(base_job_id))
    actual.name = selected.resource_name
    actual.task_groups[0].name = selected.resource_name + "/taskGroups/group0"
    actual.allocation_policy.labels["batch-job-id"] = base_job_id
    actual.allocation_policy.location.allowed_locations.append("regions/us-east1")
    assert provider.validated_adoption(selected, actual)

    actual.allocation_policy.instances[0].policy.machine_type = "n4-highcpu-4"
    assert not provider.validated_adoption(selected, actual)


def test_mismatched_provider_adoption_is_collision() -> None:
    base_job_id = "c-case-s1"
    selected = BatchJobSpec("study", "us-east1", base_job_id, base_job_id, job(base_job_id), 9000)
    actual = provider._job_from_json(job(base_job_id))
    actual.name = selected.resource_name
    actual.allocation_policy.instances[0].policy.machine_type = "n4-highcpu-4"

    class ExistingClient:
        def create_job(self, **_kwargs: Any) -> Any:
            raise AlreadyExists("exists")  # type: ignore[no-untyped-call]

        def get_job(self, **_kwargs: Any) -> Any:
            return actual

    fact = provider.ensure_batch_job(selected.submission_spec(), client=cast(Any, ExistingClient()))

    assert isinstance(fact, Collision)
    assert fact.failure_type == "BatchJobCollision"


def test_provider_reports_exact_adoption_as_policy_free_fact() -> None:
    base_job_id = "c-case-s1"
    selected = BatchJobSpec("study", "us-east1", base_job_id, base_job_id, job(base_job_id), 9000)
    actual = provider._job_from_json(job(base_job_id))
    actual.name = selected.resource_name

    class ExistingClient:
        def create_job(self, **_kwargs: Any) -> Any:
            raise AlreadyExists("exists")  # type: ignore[no-untyped-call]

        def get_job(self, **_kwargs: Any) -> Any:
            return actual

    first = provider.ensure_batch_job(
        selected.submission_spec(), client=cast(Any, ExistingClient())
    )
    redrive = provider.ensure_batch_job(
        selected.submission_spec(), client=cast(Any, ExistingClient())
    )

    assert isinstance(first, AdoptedExact)
    assert isinstance(redrive, AdoptedExact)


def test_provider_create_errors_with_unknown_effect_are_typed_ambiguous() -> None:
    selected = BatchJobSpec("study", "us-east1", "c-case-s1", "c-case-s1", job("c-case-s1"), 9000)

    class FailingClient:
        def create_job(self, **_kwargs: Any) -> Any:
            raise ServiceUnavailable("retryable create failure")  # type: ignore[no-untyped-call]

    fact = provider.ensure_batch_job(selected.submission_spec(), client=cast(Any, FailingClient()))

    assert isinstance(fact, Ambiguous)
    assert fact.error_type == "ServiceUnavailable"


def test_provider_unexpected_create_identity_is_typed_ambiguous() -> None:
    selected = BatchJobSpec("study", "us-east1", "c-case-s1", "c-case-s1", job("c-case-s1"), 9000)
    created = provider._job_from_json(job("c-case-s1"))
    created.name = selected.resource_name + "-other"

    class UnexpectedClient:
        def create_job(self, **_kwargs: Any) -> Any:
            return created

    fact = provider.ensure_batch_job(
        selected.submission_spec(), client=cast(Any, UnexpectedClient())
    )

    assert isinstance(fact, Ambiguous)
    assert fact.error_type == "ProviderError"


def test_provider_observation_errors_with_unknown_effect_are_typed_ambiguous() -> None:
    selected = BatchJobSpec("study", "us-east1", "c-case-s1", "c-case-s1", job("c-case-s1"), 9000)

    class FailingClient:
        def get_job(self, **_kwargs: Any) -> Any:
            raise ServiceUnavailable("retryable observe failure")  # type: ignore[no-untyped-call]

    fact = provider.observe_batch_job(selected.submission_spec(), client=cast(Any, FailingClient()))

    assert isinstance(fact, ObservationAmbiguous)
    assert fact.error_type == "ServiceUnavailable"


@pytest.mark.parametrize("fact", [NotVisible("not visible"), ObservationAmbiguous("ambiguous")])
def test_non_visible_or_ambiguous_observation_stays_unsettled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fact: NotVisible | ObservationAmbiguous
) -> None:
    path = tmp_path / "campaign.sqlite3"
    selected = attempt()
    monkeypatch.setattr(
        provider,
        "ensure_batch_job",
        lambda spec: Created(payload(spec).resource_name, "QUEUED"),
    )
    controller.start_campaign(
        ledger_path=path,
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        results_bucket="results",
        manifest_sha256="a" * 64,
        attempts=[selected.as_dict()],
        jobs=[job(selected.job_id)],
        controller_timeouts=[9000],
    )
    monkeypatch.setattr(provider, "observe_batch_job", lambda _spec: fact)

    [progress] = controller.reconcile_once(ledger_path=path, campaign=CAMPAIGN)

    assert progress.phase == "running"
    assert not progress.provider_settled
    assert progress.provider_state == "QUEUED"
    assert progress.provider_resource_name == (
        f"projects/study/locations/us-east1/jobs/{selected.job_id}"
    )


def test_observed_collision_settles_as_retryable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    selected = attempt()
    resource = f"projects/study/locations/us-east1/jobs/{selected.job_id}"
    monkeypatch.setattr(provider, "ensure_batch_job", lambda _spec: Created(resource, "QUEUED"))
    controller.start_campaign(
        ledger_path=path,
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        results_bucket="results",
        manifest_sha256="a" * 64,
        attempts=[selected.as_dict()],
        jobs=[job(selected.job_id)],
        controller_timeouts=[9000],
    )
    monkeypatch.setattr(
        provider,
        "observe_batch_job",
        lambda _spec: ObservedCollision("BatchJobCollision", resource, "SUCCEEDED", True),
    )

    [progress] = controller.reconcile_once(ledger_path=path, campaign=CAMPAIGN)

    assert progress.phase == "awaiting_retry"
    assert progress.provider_settled
    assert progress.failure_type == "BatchJobCollision"


def test_persisted_running_case_uses_clean_redrive_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    selected = attempt()
    rendered = job(selected.job_id)
    with ledger.open_ledger(path) as connection:
        ledger.register_campaign(
            connection,
            campaign=CAMPAIGN,
            project="study",
            location="us-east1",
            results_bucket="results",
            manifest_sha256="a" * 64,
            cases=[
                {
                    "base_job_id": selected.job_id,
                    "job": rendered,
                    "controller_timeout_s": 9000,
                }
            ],
            now=NOW,
        )
        ledger.record_intent(connection, attempt=selected.as_dict(), campaign=CAMPAIGN, now=NOW)
    assert ledger.SQLiteIntentJournal(path, CAMPAIGN).claim_submission(selected.job_id, now=NOW)
    calls: list[str] = []

    def ensure(spec: SubmissionSpec[BatchJobSpec]) -> AdoptedExact:
        batch = payload(spec)
        calls.append(batch.job_id)
        return AdoptedExact(batch.resource_name, "QUEUED")

    monkeypatch.setattr(provider, "ensure_batch_job", ensure)
    controller.start_campaign(
        ledger_path=path,
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        results_bucket="results",
        manifest_sha256="a" * 64,
        attempts=[selected.as_dict()],
        jobs=[rendered],
        controller_timeouts=[9000],
    )

    assert calls == [selected.job_id]
    [progress] = controller.progress(ledger_path=path, campaign=CAMPAIGN)
    assert progress.failure_type is None


def test_first_adoption_collision_survives_provider_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    selected = attempt()
    monkeypatch.setattr(
        provider,
        "ensure_batch_job",
        lambda spec: AdoptedExact(payload(spec).resource_name, "QUEUED"),
    )
    controller.start_campaign(
        ledger_path=path,
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        results_bucket="results",
        manifest_sha256="a" * 64,
        attempts=[selected.as_dict()],
        jobs=[job(selected.job_id)],
        controller_timeouts=[9000],
    )
    monkeypatch.setattr(
        provider,
        "observe_batch_job",
        lambda spec: ObservedExact(payload(spec).resource_name, "SUCCEEDED", True),
    )

    [progress] = controller.reconcile_once(ledger_path=path, campaign=CAMPAIGN)

    assert progress.phase == "awaiting_retry"
    assert progress.provider_settled
    assert progress.provider_state == "SUCCEEDED"
    assert progress.failure_type == "BatchJobCollision"


def test_report_source_has_no_temporal_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/s3_listing_study/manager/campaign/report.py").read_text(encoding="utf-8")
    assert "temporalio" not in source
    assert "Temporal" not in source


def test_controller_job_json_is_canonical(tmp_path: Path) -> None:
    path = tmp_path / "campaign.sqlite3"
    base_job_id = awaiting_retry(path)
    with ledger.open_ledger(path) as connection:
        [row] = ledger.controller_cases(connection, CAMPAIGN)
    assert row["base_job_id"] == base_job_id
    assert row["job_json"] == json.dumps(
        json.loads(row["job_json"]), sort_keys=True, separators=(",", ":")
    )


def test_old_controller_database_supports_progress_journal_and_redrive_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "old.sqlite3"
    selected = attempt()
    rendered = job(selected.job_id)
    job_json = json.dumps(rendered, sort_keys=True, separators=(",", ":"))
    case = selected.as_dict()
    resources = case["resources"]
    connection = sqlite3.connect(path)
    try:
        connection.executescript(FROZEN_PRE_EXTRACTION_SCHEMA)
        connection.execute(
            "INSERT INTO campaigns (campaign, project, location, results_bucket,"
            " manifest_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (CAMPAIGN, "study", "us-east1", "results", "a" * 64, NOW),
        )
        connection.execute(
            "INSERT INTO controller_inputs (base_job_id, campaign, job_json,"
            " controller_timeout_s) VALUES (?, ?, ?, ?)",
            (selected.job_id, CAMPAIGN, job_json, 9000),
        )
        connection.execute(
            "INSERT INTO controller_cases (base_job_id, campaign, phase, current_submission,"
            " current_job_id, job_json, controller_timeout_s, updated_at)"
            " VALUES (?, ?, 'running', 1, ?, ?, ?, ?)",
            (selected.job_id, CAMPAIGN, selected.job_id, job_json, 9000, NOW),
        )
        connection.execute(
            "INSERT INTO attempts (job_id, campaign, run_ordinal, submission, state,"
            " created_at, updated_at, bucket, region, tool, case_id, mode, machine_type,"
            " vcpus, memory_gb, container_memory_gb, timeout_s, env_json, derived_image,"
            " case_fingerprint, fingerprint, prefix, case_json)"
            " VALUES (?, ?, ?, ?, 'submitting', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?)",
            (
                selected.job_id,
                CAMPAIGN,
                case["run_ordinal"],
                case["submission"],
                NOW,
                NOW,
                case["bucket"],
                case["region"],
                case["tool"],
                case["case_id"],
                case["mode"],
                resources["machine_type"],
                resources["vcpus"],
                resources["memory_gb"],
                resources["container_memory_gb"],
                case["timeout_s"],
                json.dumps(case["env"], sort_keys=True),
                case["derived_image"],
                case["case_fingerprint"],
                case["fingerprint"],
                case["prefix"],
                json.dumps(case, sort_keys=True),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    [progress] = controller.progress(ledger_path=path, campaign=CAMPAIGN)
    assert progress.phase == "running"
    claim = ledger.SQLiteIntentJournal(path, CAMPAIGN).claim_submission(selected.job_id, now=NOW)
    assert claim is not None
    assert claim.token == "redrive"
    assert claim.spec.canonical_job_spec == job_json.encode()

    monkeypatch.setattr(
        provider,
        "ensure_batch_job",
        lambda spec: Created(payload(spec).resource_name, "QUEUED"),
    )
    redriven = controller.retry_case(
        ledger_path=path, campaign=CAMPAIGN, base_job_id=selected.job_id, submission=1
    )

    assert redriven.provider_state == "QUEUED"
    with ledger.open_ledger(path) as current:
        [row] = ledger.controller_cases(current, CAMPAIGN)
    assert row["job_json"] == job_json


def test_reconnect_compares_original_batch_request_after_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "campaign.sqlite3"
    selected = attempt()
    original = job(selected.job_id)
    monkeypatch.setattr(
        provider,
        "ensure_batch_job",
        lambda spec: Created(payload(spec).resource_name, "QUEUED"),
    )

    def start(selected_job: dict[str, Any]) -> list[dict[str, Any]]:
        return controller.start_campaign(
            ledger_path=path,
            campaign=CAMPAIGN,
            project="study",
            location="us-east1",
            results_bucket="results",
            manifest_sha256="a" * 64,
            attempts=[selected.as_dict()],
            jobs=[selected_job],
            controller_timeouts=[9000],
        )

    start(original)
    changed = json.loads(json.dumps(original))
    changed["logsPolicy"] = {"destination": "PATH"}

    with pytest.raises(ledger.LedgerError, match="frozen controller request changed"):
        start(changed)


def test_campaign_singletons_and_publish_wait_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = [
        "--path",
        str(tmp_path / "plan.yaml"),
        "--campaign",
        CAMPAIGN,
        "--image-set",
        str(tmp_path / "images.json"),
        "--project",
        "study",
        "--location",
        "us-east1",
        "--results-bucket",
        "results",
        "--anonymous-worker-sa",
        "worker@study.invalid",
        "--ledger",
        str(tmp_path / "campaign.sqlite3"),
    ]
    with pytest.raises(SystemExit):
        campaign_cli.build_parser().parse_args(
            [*base, "--post-attempt-allowance-s", "1", "--post-attempt-allowance-s", "2"]
        )
    monkeypatch.setattr(
        campaign_cli,
        "_load_plans",
        lambda _args: pytest.fail("invalid publish combination read a plan"),
    )
    assert campaign_cli.submit_campaign_main([*base, "--publish-report"]) == 1
    assert "--publish-report requires --wait" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        retry_parser().parse_args(
            [
                "--campaign",
                CAMPAIGN,
                "--campaign",
                CAMPAIGN,
                "--ledger",
                "x",
                "--job-id",
                "x",
                "--submission",
                "2",
            ]
        )
    with pytest.raises(SystemExit):
        finalize_parser().parse_args(["--campaign", CAMPAIGN, "--ledger", "x", "--ledger", "y"])

    parsed = campaign_cli.build_parser().parse_args(
        [*base, "--post-attempt-allowance-s", "7", "--poll-interval-s", "0.25"]
    )
    assert parsed.post_attempt_allowance_s == "7"
    assert parsed.poll_interval_s == "0.25"
    assert (
        retry_parser()
        .parse_args(["--campaign", CAMPAIGN, "--ledger", "x", "--job-id", "x", "--submission", "2"])
        .submission
        == "2"
    )
    assert (
        report.build_parser()
        .parse_args(
            [
                "--campaign",
                CAMPAIGN,
                "--results-bucket",
                "results",
                "--ledger",
                "x",
                "--poll-interval-s",
                "0.25",
            ]
        )
        .poll_interval_s
        == "0.25"
    )


def test_control_commands_emit_only_engine_neutral_progress_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    item = CaseControllerProgress(
        job_id="job-s1",
        phase="terminal",
        provider_state="FAILED",
        failure_type="failure",
        provider_resource_name="projects/study/locations/us-east1/jobs/job-s1",
        provider_settled=True,
        current_submission=1,
        current_job_id="job-s1",
        accepted_failure=True,
    )
    expected_keys = {
        "job_id",
        "child_run_id",
        "phase",
        "provider_state",
        "failure_type",
        "provider_resource_name",
        "provider_settled",
        "current_submission",
        "current_job_id",
    }
    monkeypatch.setattr(controller, "retry_case", lambda **_kwargs: item)
    assert (
        control.retry_case_main(
            [
                "--campaign",
                CAMPAIGN,
                "--ledger",
                "campaign.sqlite3",
                "--job-id",
                "job-s1",
                "--submission",
                "2",
            ]
        )
        == 0
    )
    assert set(json.loads(capsys.readouterr().out)) == expected_keys

    monkeypatch.setattr(controller, "finalize", lambda **_kwargs: [item])
    assert (
        control.finalize_campaign_main(["--campaign", CAMPAIGN, "--ledger", "campaign.sqlite3"])
        == 0
    )
    [rendered] = json.loads(capsys.readouterr().out)
    assert set(rendered) == expected_keys


def test_final_operational_failure_still_has_successful_report_exit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    final = {
        "schema_version": 3,
        "report_final": True,
        "operational_success": False,
    }
    monkeypatch.setattr(report, "_run_report", lambda _args: final)

    assert (
        report.report_campaign_main(
            [
                "--campaign",
                CAMPAIGN,
                "--results-bucket",
                "results",
                "--ledger",
                "campaign.sqlite3",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == final
