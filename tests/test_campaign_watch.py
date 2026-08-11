"""Read-only Batch monitoring and monotonic campaign-ledger reconciliation."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from s3_listing_study.manager import cli as manager_cli
from s3_listing_study.manager.campaign import Attempt, ledger, watch
from tests.test_campaign_batch import attempt

CAMPAIGN = "2026-08-10-first"
NOW = "2026-08-10T12:00:00Z"
REAL_RESOLVE_PROJECT = watch._resolve_project


@pytest.fixture(autouse=True)
def canonical_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "_resolve_project", lambda _project: frozenset({"study", "123"}))


def selected_attempts() -> tuple[Attempt, Attempt]:
    first = attempt()
    second = replace(
        first,
        job_id="c-2026-08-10-first-swath-recursive-eeeeeeee-r2-s1",
        run_ordinal=2,
        prefix=first.prefix.removesuffix("run-1") + "run-2",
    )
    return first, second


def create_ledger(path: Path, *, count: int = 2) -> tuple[Attempt, ...]:
    selected = selected_attempts()[:count]
    with ledger.open_ledger(path) as connection:
        for item in selected:
            ledger.record_intent(
                connection, attempt=item.as_dict(), campaign=item.campaign, now=NOW
            )
            ledger.record_state(connection, job_id=item.job_id, state="submitted", now=NOW)
    return selected


def batch_job(item: Attempt, state: str, *, description: str | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {"state": state}
    if description is not None:
        status["statusEvents"] = [
            {
                "description": description,
                "eventTime": "2026-08-10T12:01:00Z",
                "type": state,
            }
        ]
    return {
        "name": f"projects/study/locations/us-east1/jobs/{item.job_id}",
        "status": status,
    }


def jobs_by_id(*jobs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(job["name"]).rsplit("/", 1)[-1]: job for job in jobs}


def reconcile(path: Path) -> dict[str, Any]:
    return watch.reconcile_once(
        campaign=CAMPAIGN,
        project="study",
        location="us-east1",
        ledger_path=path,
    )


def events(path: Path, job_id: str) -> list[sqlite3.Row]:
    with ledger.open_ledger(path) as connection:
        return connection.execute(
            "SELECT event, detail FROM events WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()


def test_manager_registers_and_dispatches_watch_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str] | None] = []

    def fake_watch(argv: list[str] | None) -> int:
        seen.append(None if argv is None else list(argv))
        return 7

    monkeypatch.setattr(
        watch,
        "watch_campaign_main",
        fake_watch,
    )

    assert manager_cli.main(["watch-campaign", "--campaign", CAMPAIGN]) == 7
    assert seen == [["--campaign", CAMPAIGN]]
    assert "watch-campaign" in manager_cli.build_parser().format_help()


def test_watch_arguments_are_required_and_poll_interval_must_be_positive() -> None:
    parser = watch.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    base = [
        "--campaign",
        CAMPAIGN,
        "--project",
        "study",
        "--location",
        "us-east1",
        "--ledger",
        "ledger.sqlite3",
    ]
    for invalid in ("0", "-1", "nan", "inf"):
        with pytest.raises(SystemExit):
            parser.parse_args([*base, "--poll-interval-s", invalid])
    assert parser.parse_args([*base, "--poll-interval-s", "0.25"]).poll_interval_s == 0.25


def test_unknown_campaign_is_refused_before_a_cloud_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.sqlite3"
    create_ledger(path, count=1)
    monkeypatch.setattr(
        watch,
        "_read_jobs",
        lambda **_kwargs: pytest.fail("unknown campaign contacted Batch"),
    )

    with pytest.raises(watch.WatchError, match="has no attempts"):
        watch.reconcile_once(
            campaign="2026-08-10-other",
            project="study",
            location="us-east1",
            ledger_path=path,
        )


def test_each_ledger_job_is_described_exactly_and_resource_name_is_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    job_id = "c-exact"
    response = {
        "name": f"projects/123456789/locations/us-east1/jobs/{job_id}",
        "status": {"state": "RUNNING"},
    }

    def run(argv: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        calls.append(tuple(argv))
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(response).encode(), stderr=b"")

    monkeypatch.setattr(watch, "_run", run)
    identities = frozenset({"study-id", "123456789"})
    assert (
        watch._describe_job(
            job_id=job_id,
            project="study-id",
            project_identities=identities,
            location="us-east1",
        )
        == response
    )
    assert calls == [
        (
            "gcloud",
            "batch",
            "jobs",
            "describe",
            job_id,
            "--project",
            "study-id",
            "--location",
            "us-east1",
            "--format=json",
        )
    ]

    response["name"] = f"projects/123456789/locations/us-west1/jobs/{job_id}"
    with pytest.raises(watch.WatchError, match="unexpected resource name"):
        watch._describe_job(
            job_id=job_id,
            project="study-id",
            project_identities=identities,
            location="us-east1",
        )

    response["name"] = f"projects/wrong/locations/us-east1/jobs/{job_id}"
    with pytest.raises(watch.WatchError, match="unexpected resource name"):
        watch._describe_job(
            job_id=job_id,
            project="study-id",
            project_identities=identities,
            location="us-east1",
        )


def test_project_lookup_accepts_canonical_number_and_rejects_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {"projectId": "study-id", "projectNumber": "123456789"}
    calls: list[tuple[str, ...]] = []

    def run(argv: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        calls.append(tuple(argv))
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(response).encode(), stderr=b"")

    monkeypatch.setattr(watch, "_run", run)
    assert REAL_RESOLVE_PROJECT("study-id") == frozenset({"study-id", "123456789"})
    assert calls == [
        (
            "gcloud",
            "projects",
            "describe",
            "study-id",
            "--format=json(projectId,projectNumber)",
        )
    ]

    with pytest.raises(watch.WatchError, match="non-matching identity"):
        REAL_RESOLVE_PROJECT("wrong")


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "message"),
    [
        (1, b"", b"permission denied", "project lookup failed"),
        (0, b"not-json", b"", "malformed JSON"),
        (0, b'{"projectId":"study-id"}', b"", "malformed project identity"),
    ],
)
def test_project_lookup_failures_are_surfaced(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: bytes,
    stderr: bytes,
    message: str,
) -> None:
    monkeypatch.setattr(
        watch,
        "_run",
        lambda _argv: subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr),
    )
    with pytest.raises(watch.WatchError, match=message):
        REAL_RESOLVE_PROJECT("study-id")


def test_project_lookup_failure_precedes_job_reads_and_ledger_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.sqlite3"
    (selected,) = create_ledger(path, count=1)
    monkeypatch.setattr(
        watch,
        "_resolve_project",
        lambda _project: (_ for _ in ()).throw(watch.WatchError("project lookup failed")),
    )
    monkeypatch.setattr(
        watch,
        "_read_jobs",
        lambda *_args, **_kwargs: pytest.fail("job read preceded project validation"),
    )

    with pytest.raises(watch.WatchError, match="project lookup failed"):
        reconcile(path)
    assert [row["event"] for row in events(path, selected.job_id)] == [
        "submitting",
        "submitted",
    ]


@pytest.mark.parametrize(
    ("batch_state", "ledger_state"),
    [("RUNNING", "running"), ("SUCCEEDED", "succeeded"), ("FAILED", "failed")],
)
def test_batch_states_map_to_ledger_states_with_status_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    batch_state: str,
    ledger_state: str,
) -> None:
    path = tmp_path / "ledger.sqlite3"
    (selected,) = create_ledger(path, count=1)
    monkeypatch.setattr(
        watch,
        "_read_jobs",
        lambda *_args, **_kwargs: jobs_by_id(
            batch_job(selected, batch_state, description="scheduler detail")
        ),
    )

    summary = reconcile(path)

    assert summary["states"] == {ledger_state: 1}
    last = events(path, selected.job_id)[-1]
    assert last["event"] == ledger_state
    detail = json.loads(last["detail"])
    assert detail["batch_state"] == batch_state
    assert detail["status_event"]["description"] == "scheduler detail"
    assert detail["source"] == "gcp-batch"


def test_transition_detail_is_allowlisted_latest_only_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.sqlite3"
    (selected,) = create_ledger(path, count=1)
    long_text = "x" * (watch.PROVIDER_TEXT_LIMIT + 50)
    job = batch_job(selected, "RUNNING")
    job["status"] = {
        "state": "RUNNING",
        "runDuration": long_text,
        "providerUnknown": "discard me",
        "statusEvents": [
            {"type": "QUEUED", "description": "old", "providerUnknown": "discard me"},
            {
                "type": long_text,
                "eventTime": long_text,
                "description": long_text,
                "providerUnknown": "discard me",
            },
        ],
    }
    monkeypatch.setattr(watch, "_read_jobs", lambda *_args, **_kwargs: jobs_by_id(job))

    reconcile(path)
    detail = json.loads(events(path, selected.job_id)[-1]["detail"])

    assert set(detail) == {"batch_state", "runDuration", "source", "status_event"}
    assert set(detail["status_event"]) == {"description", "eventTime", "type"}
    assert "old" not in json.dumps(detail)
    assert len(detail["runDuration"]) == watch.PROVIDER_TEXT_LIMIT
    assert all(
        len(detail["status_event"][field]) == watch.PROVIDER_TEXT_LIMIT
        for field in ("description", "eventTime", "type")
    )


@pytest.mark.parametrize("state", ["QUEUED", "SCHEDULED"])
def test_provisioning_and_repeated_observations_are_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    path = tmp_path / "ledger.sqlite3"
    (selected,) = create_ledger(path, count=1)
    observations = [batch_job(selected, state)]
    monkeypatch.setattr(watch, "_read_jobs", lambda *_args, **_kwargs: jobs_by_id(*observations))

    reconcile(path)
    reconcile(path)
    assert [row["event"] for row in events(path, selected.job_id)] == [
        "submitting",
        "submitted",
    ]

    observations[:] = [batch_job(selected, "RUNNING")]
    reconcile(path)
    reconcile(path)
    assert [row["event"] for row in events(path, selected.job_id)] == [
        "submitting",
        "submitted",
        "running",
    ]


def test_one_poll_reconciles_partial_progress_only_for_exact_ledger_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.sqlite3"
    first, second = create_ledger(path)
    requested: list[tuple[str, ...]] = []

    def read_jobs(job_ids: tuple[str, ...], **_kwargs: Any) -> dict[str, dict[str, Any]]:
        requested.append(job_ids)
        return jobs_by_id(batch_job(first, "RUNNING"), batch_job(second, "SCHEDULED"))

    monkeypatch.setattr(
        watch,
        "_read_jobs",
        read_jobs,
    )

    summary = reconcile(path)

    assert summary == {
        "campaign": CAMPAIGN,
        "complete": False,
        "successful": False,
        "states": {"running": 1, "submitted": 1},
        "terminal": 0,
        "total": 2,
    }
    assert requested == [(first.job_id, second.job_id)]


def test_watch_continues_through_partial_progress_and_exits_after_terminal_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "ledger.sqlite3"
    first, second = create_ledger(path)
    polls = iter(
        [
            [batch_job(first, "RUNNING"), batch_job(second, "SCHEDULED")],
            [batch_job(first, "SUCCEEDED"), batch_job(second, "SUCCEEDED")],
        ]
    )
    monkeypatch.setattr(watch, "_read_jobs", lambda *_args, **_kwargs: jobs_by_id(*next(polls)))
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    result = watch.watch_campaign_main(
        [
            "--campaign",
            CAMPAIGN,
            "--project",
            "study",
            "--location",
            "us-east1",
            "--ledger",
            str(path),
            "--poll-interval-s",
            "0.5",
        ]
    )

    assert result == 0
    assert sleeps == [0.5]
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert summaries[-1]["successful"] is True


def test_once_and_continuous_watch_return_nonzero_for_terminal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "ledger.sqlite3"
    first, second = create_ledger(path)
    monkeypatch.setattr(
        watch,
        "_read_jobs",
        lambda *_args, **_kwargs: jobs_by_id(
            batch_job(first, "FAILED"), batch_job(second, "SCHEDULED")
        ),
    )
    base = [
        "--campaign",
        CAMPAIGN,
        "--project",
        "study",
        "--location",
        "us-east1",
        "--ledger",
        str(path),
    ]

    assert watch.watch_campaign_main([*base, "--once"]) == 1
    partial = json.loads(capsys.readouterr().out)
    assert partial["complete"] is False
    assert partial["states"] == {"failed": 1, "submitted": 1}

    monkeypatch.setattr(
        watch,
        "_read_jobs",
        lambda *_args, **_kwargs: jobs_by_id(
            batch_job(first, "FAILED"), batch_job(second, "SUCCEEDED")
        ),
    )
    assert watch.watch_campaign_main(base) == 1
    terminal = json.loads(capsys.readouterr().out)
    assert terminal["complete"] is True
    assert terminal["successful"] is False


def test_missing_jobs_are_surfaced_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.sqlite3"
    first, second = create_ledger(path)

    def describe(*, job_id: str, **_kwargs: Any) -> dict[str, Any]:
        if job_id == second.job_id:
            raise watch.WatchError(f"Batch did not return ledger job: {job_id}")
        return batch_job(first, "RUNNING")

    monkeypatch.setattr(watch, "_describe_job", describe)

    with pytest.raises(watch.WatchError, match="did not return ledger job"):
        reconcile(path)
    assert [row["event"] for row in events(path, first.job_id)] == ["submitting", "submitted"]
    assert [row["event"] for row in events(path, second.job_id)] == ["submitting", "submitted"]


def test_unknown_state_and_terminal_contradiction_are_surfaced_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.sqlite3"
    (selected,) = create_ledger(path, count=1)
    observed = [batch_job(selected, "PAUSED")]
    monkeypatch.setattr(watch, "_read_jobs", lambda *_args, **_kwargs: jobs_by_id(*observed))

    with pytest.raises(watch.WatchError, match="unknown Batch state"):
        reconcile(path)

    observed[:] = [batch_job(selected, "SUCCEEDED")]
    reconcile(path)
    observed[:] = [batch_job(selected, "FAILED")]
    with pytest.raises(watch.WatchError, match="contradictory terminal"):
        reconcile(path)
    assert [row["event"] for row in events(path, selected.job_id)] == [
        "submitting",
        "submitted",
        "succeeded",
    ]


@pytest.mark.parametrize("stale_state", ["QUEUED", "SCHEDULED", "RUNNING", "SUCCEEDED"])
def test_stale_or_same_outcome_observations_do_not_regress_a_terminal_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale_state: str
) -> None:
    path = tmp_path / "ledger.sqlite3"
    (selected,) = create_ledger(path, count=1)
    with ledger.open_ledger(path) as connection:
        ledger.record_state(connection, job_id=selected.job_id, state="succeeded", now=NOW)
    monkeypatch.setattr(
        watch,
        "_read_jobs",
        lambda *_args, **_kwargs: jobs_by_id(batch_job(selected, stale_state)),
    )

    summary = reconcile(path)

    assert summary["states"] == {"succeeded": 1}
    assert [row["event"] for row in events(path, selected.job_id)] == [
        "submitting",
        "submitted",
        "succeeded",
    ]


def test_lost_compare_and_swap_preserves_a_concurrent_newer_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.sqlite3"
    (selected,) = create_ledger(path, count=1)
    monkeypatch.setattr(
        watch,
        "_read_jobs",
        lambda *_args, **_kwargs: jobs_by_id(batch_job(selected, "RUNNING")),
    )
    original = ledger.record_state_if_current

    def interleaved(connection: sqlite3.Connection, **kwargs: Any) -> bool:
        ledger.record_state(
            connection, job_id=selected.job_id, state="succeeded", now="2026-08-10T12:02:00Z"
        )
        return original(connection, **kwargs)

    monkeypatch.setattr(ledger, "record_state_if_current", interleaved)
    summary = reconcile(path)

    assert summary["states"] == {"succeeded": 1}
    assert [row["event"] for row in events(path, selected.job_id)] == [
        "submitting",
        "submitted",
        "succeeded",
    ]


@pytest.mark.parametrize(("observed", "winner"), [("SUCCEEDED", "failed"), ("FAILED", "succeeded")])
def test_lost_compare_and_swap_surfaces_an_opposite_terminal_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observed: str,
    winner: str,
) -> None:
    path = tmp_path / "ledger.sqlite3"
    (selected,) = create_ledger(path, count=1)
    monkeypatch.setattr(
        watch,
        "_read_jobs",
        lambda *_args, **_kwargs: jobs_by_id(batch_job(selected, observed)),
    )
    original = ledger.record_state_if_current
    interleaved = False

    def race(connection: sqlite3.Connection, **kwargs: Any) -> bool:
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            ledger.record_state(connection, job_id=selected.job_id, state=winner, now=NOW)
        return original(connection, **kwargs)

    monkeypatch.setattr(ledger, "record_state_if_current", race)

    with pytest.raises(watch.WatchError, match="contradictory terminal"):
        reconcile(path)
    assert [row["event"] for row in events(path, selected.job_id)] == [
        "submitting",
        "submitted",
        winner,
    ]


@pytest.mark.parametrize(
    "stdout",
    [b"not-json", b"[]", b"null"],
)
def test_malformed_gcloud_json_is_surfaced(monkeypatch: pytest.MonkeyPatch, stdout: bytes) -> None:
    monkeypatch.setattr(
        watch,
        "_run",
        lambda _argv: subprocess.CompletedProcess([], 0, stdout=stdout, stderr=b""),
    )
    with pytest.raises(watch.WatchError, match="malformed JSON"):
        watch._describe_job(
            job_id="expected",
            project="study",
            project_identities=frozenset({"study"}),
            location="us-east1",
        )


def test_gcloud_failure_is_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watch,
        "_run",
        lambda _argv: subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"permission denied"),
    )
    with pytest.raises(watch.WatchError, match="gcloud Batch describe failed: permission denied"):
        watch._describe_job(
            job_id="expected",
            project="study",
            project_identities=frozenset({"study"}),
            location="us-east1",
        )

    monkeypatch.setattr(
        watch,
        "_run",
        lambda _argv: subprocess.CompletedProcess(
            [], 1, stdout=b"", stderr=b"NOT_FOUND: requested entity was not found"
        ),
    )
    with pytest.raises(watch.WatchError, match="did not return ledger job: expected"):
        watch._describe_job(
            job_id="expected",
            project="study",
            project_identities=frozenset({"study"}),
            location="us-east1",
        )


def test_interrupt_returns_130_without_an_extra_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = 0

    def partial(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "campaign": CAMPAIGN,
            "complete": False,
            "successful": False,
            "states": {"running": 1},
            "terminal": 0,
            "total": 1,
        }

    monkeypatch.setattr(watch, "reconcile_once", partial)
    monkeypatch.setattr(time, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt))
    result = watch.watch_campaign_main(
        [
            "--campaign",
            CAMPAIGN,
            "--project",
            "study",
            "--location",
            "us-east1",
            "--ledger",
            str(tmp_path / "ledger.sqlite3"),
        ]
    )

    assert result == 130
    assert calls == 1
    captured = capsys.readouterr()
    assert "interrupted" in captured.err
    assert len(captured.out.splitlines()) == 1
