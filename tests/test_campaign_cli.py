"""Dry-run, immutable metadata, and ledger-backed Batch submission."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from s3_listing_study.manager.bench import plan as bench
from s3_listing_study.manager.campaign import Attempt, CampaignError, ledger
from s3_listing_study.manager.campaign import cli as campaign_cli
from s3_listing_study.manager.campaign.batch import render_job as render_batch_job
from tests.test_campaign_batch import DIGEST, attempt

ROOT = Path(__file__).resolve().parents[1]


def write_inputs(tmp_path: Path, *, auth: str = "anonymous") -> tuple[Path, Path]:
    plan = tmp_path / "example-bucket.yaml"
    plan.write_text(
        textwrap.dedent(
            f"""\
            spec_version: 2
            bucket: example-bucket
            region: us-east-1
            defaults:
              reps: 1
              timeout_s: 3600
              auth: {auth}
              vcpus: 2
              memory_gb: 4
            tools:
              aws-cli:
                cases:
                  - {{mode: s3api-v2-text, container_memory_gb: 2}}
            """
        ),
        encoding="utf-8",
    )
    image_set = tmp_path / "images.json"
    registration = json.loads((ROOT / "tools/aws-cli/build/image.json").read_text(encoding="utf-8"))
    image_set.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "images": {
                    "aws-cli": {
                        "derived_image": DIGEST,
                        "image_uri": f"us-east1-docker.pkg.dev/study/images/aws-cli@{DIGEST}",
                        "shared_base_digest": "sha256:" + "b" * 64,
                        "shared_base_uri": "registry.example/base@sha256:" + "b" * 64,
                        "tool_build_sha256": registration["tool_build_sha256"],
                        "tool_artifact": registration["tool_artifact"],
                        "tool_version": registration["tool_version"],
                        "adapter_bundle_sha256": registration["adapter_bundle_sha256"],
                        "shared_base_source_sha256": registration["shared_base_source_sha256"],
                        "harness_revision": "a" * 40,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return plan, image_set


def write_plan(path: Path, *, bucket: str, tool: str = "aws-cli") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""\
            spec_version: 2
            bucket: {bucket}
            region: us-east-1
            defaults:
              reps: 1
              timeout_s: 3600
              auth: anonymous
              vcpus: 2
              memory_gb: 4
            tools:
              {tool}:
            """
        ),
        encoding="utf-8",
    )
    return path


def write_image_set(path: Path, tools: set[str]) -> Path:
    images: dict[str, dict[str, Any]] = {}
    for tool in tools:
        registration = json.loads(
            (ROOT / f"tools/{tool}/build/image.json").read_text(encoding="utf-8")
        )
        images[tool] = {
            "derived_image": DIGEST,
            "image_uri": f"us-east1-docker.pkg.dev/study/images/{tool}@{DIGEST}",
            "shared_base_digest": "sha256:" + "b" * 64,
            "shared_base_uri": "registry.example/base@sha256:" + "b" * 64,
            "tool_build_sha256": registration["tool_build_sha256"],
            "tool_artifact": registration["tool_artifact"],
            "tool_version": registration["tool_version"],
            "adapter_bundle_sha256": registration["adapter_bundle_sha256"],
            "shared_base_source_sha256": registration["shared_base_source_sha256"],
            "harness_revision": "a" * 40,
        }
    path.write_text(
        json.dumps({"schema_version": 2, "images": images}),
        encoding="utf-8",
    )
    return path


def arguments(tmp_path: Path, plan: Path, image_set: Path) -> list[str]:
    return [
        "--path",
        str(plan),
        "--campaign",
        "2026-08-10-first",
        "--image-set",
        str(image_set),
        "--project",
        "study",
        "--location",
        "us-east1",
        "--results-bucket",
        "study-results",
        "--anonymous-worker-sa",
        "worker@study.iam.gserviceaccount.com",
        "--ledger",
        str(tmp_path / "ledger.sqlite3"),
    ]


def completed(
    returncode: int = 0, *, stdout: bytes = b"", stderr: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_spot_is_the_default_provisioning_model(tmp_path: Path) -> None:
    parsed = campaign_cli.build_parser().parse_args(
        arguments(tmp_path, tmp_path / "plan.yaml", tmp_path / "images.json")
    )
    assert parsed.provisioning == "SPOT"


def test_repeatable_canonical_buckets_form_one_deterministic_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    buckets = tmp_path / "buckets"
    write_plan(buckets / "first.yaml", bucket="first")
    write_plan(buckets / "second.yaml", bucket="second")
    image_set = write_image_set(tmp_path / "images.json", {"aws-cli"})
    roster_checks: list[str] = []

    monkeypatch.setattr(bench, "buckets_dir", lambda: buckets)
    monkeypatch.setattr(bench, "default_path", lambda bucket: buckets / f"{bucket}.yaml")
    monkeypatch.setattr(campaign_cli, "registered_tools", lambda: {"aws-cli"})
    monkeypatch.setattr(campaign_cli, "validate_registered_images", lambda _images: None)
    original_check_roster = bench.check_roster

    def check_roster(plan: Any, registered: Any) -> None:
        roster_checks.append(plan.bucket)
        original_check_roster(plan, registered)

    monkeypatch.setattr(bench, "check_roster", check_roster)
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("dry-run called a subprocess"),
    )
    argv = [
        "--bucket",
        "first",
        "--bucket",
        "second",
        *arguments(tmp_path, buckets / "unused.yaml", image_set)[2:],
        "--dry-run",
    ]

    assert campaign_cli.submit_campaign_main(argv) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert roster_checks == ["first", "second"]
    assert [plan["bucket"] for plan in rendered["campaign.json"]["plans"]] == [
        "first",
        "second",
    ]
    assert [attempt["bucket"] for attempt in rendered["campaign.json"]["attempts"]] == [
        "first",
        "second",
    ]
    assert len(rendered["jobs"]) == 2
    assert len({job["job_id"] for job in rendered["jobs"]}) == 2


def test_image_set_must_cover_the_union_of_all_plan_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = write_plan(tmp_path / "first.yaml", bucket="first")
    second = write_plan(tmp_path / "second.yaml", bucket="second", tool="s5cmd")
    image_set = write_image_set(tmp_path / "images.json", {"aws-cli"})
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("invalid campaign called a subprocess"),
    )
    argv = [
        "--path",
        str(first),
        "--path",
        str(second),
        *arguments(tmp_path, first, image_set)[2:],
        "--dry-run",
    ]

    assert campaign_cli.submit_campaign_main(argv) == 1
    assert "image set does not exactly cover the plans (missing s5cmd)" in capsys.readouterr().err
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_duplicate_plan_bucket_is_rejected_before_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = write_plan(tmp_path / "one" / "same.yaml", bucket="same")
    duplicate = write_plan(tmp_path / "two" / "same.yaml", bucket="same")
    image_set = write_image_set(tmp_path / "images.json", {"aws-cli"})
    monkeypatch.setattr(
        campaign_cli,
        "render_job",
        lambda *_args, **_kwargs: pytest.fail("duplicate campaign rendered a job"),
    )
    argv = [
        "--path",
        str(first),
        "--path",
        str(duplicate),
        *arguments(tmp_path, first, image_set)[2:],
        "--dry-run",
    ]

    assert campaign_cli.submit_campaign_main(argv) == 1
    assert "more than one plan for bucket 'same'" in capsys.readouterr().err


def test_image_set_refuses_an_unknown_schema(tmp_path: Path) -> None:
    _plan, image_set = write_inputs(tmp_path)
    document = json.loads(image_set.read_text(encoding="utf-8"))
    document["schema_version"] = 1
    image_set.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(campaign_cli.SubmissionError, match="schema_version must be 2 or 3"):
        campaign_cli._read_image_set(image_set)


def test_dry_run_is_deterministic_and_touches_no_subprocess_or_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, image_set = write_inputs(tmp_path)
    argv = [*arguments(tmp_path, plan, image_set), "--dry-run"]
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("dry-run called a subprocess"),
    )

    assert campaign_cli.submit_campaign_main(argv) == 0
    first = capsys.readouterr().out
    assert campaign_cli.submit_campaign_main(argv) == 0
    second = capsys.readouterr().out

    assert first == second
    rendered = json.loads(first)
    assert rendered["campaign.json"]["plans"][0]["sha256"]
    assert rendered["jobs"][0]["job"]["taskGroups"][0]["taskSpec"]["maxRetryCount"] == 0
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_historical_schema_two_does_not_rehash_against_current_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, image_set = write_inputs(tmp_path)
    document = json.loads(image_set.read_text(encoding="utf-8"))
    document["images"]["aws-cli"]["tool_version"] = "fabricated"
    image_set.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("mismatched image contacted a subprocess"),
    )
    assert (
        campaign_cli.submit_campaign_main([*arguments(tmp_path, plan, image_set), "--dry-run"]) == 0
    )
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["campaign.json"]["images"]["aws-cli"]["tool_version"] == "fabricated"
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_freeze_accepts_only_byte_identical_existing_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(campaign_cli, "_run", lambda *_args, **_kwargs: completed())
    assert campaign_cli._freeze("gs://bucket/new", b"new\n") is True

    calls: list[tuple[tuple[str, ...], bytes | None]] = []
    answers = iter(
        [
            completed(1, stderr=b"412 Precondition Failed"),
            completed(stdout=b"same\n"),
        ]
    )

    def fake_run(argv: Any, *, payload: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        calls.append((tuple(argv), payload))
        return next(answers)

    monkeypatch.setattr(campaign_cli, "_run", fake_run)
    assert campaign_cli._freeze("gs://bucket/object", b"same\n") is False
    assert calls == [
        (
            (
                "gcloud",
                "storage",
                "cp",
                "-",
                "gs://bucket/object",
                "--if-generation-match=0",
            ),
            b"same\n",
        ),
        (("gcloud", "storage", "cat", "gs://bucket/object"), None),
    ]

    answers = iter([completed(1, stderr=b"already exists"), completed(stdout=b"different\n")])
    with pytest.raises(campaign_cli.SubmissionError, match="different content"):
        campaign_cli._freeze("gs://bucket/object", b"same\n")


def test_submission_writes_intent_before_gcloud_and_records_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = attempt()
    second: Attempt = replace(
        first,
        job_id="c-2026-08-10-first-swath-recursive-ffffffff-r2-s1",
        run_ordinal=2,
        prefix=first.prefix.removesuffix("run-1") + "run-2",
    )
    ledger_path = tmp_path / "ledger.sqlite3"
    seen: list[str] = []

    def fake_run(argv: Any, *, payload: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        del payload
        job_id = argv[4]
        connection = sqlite3.connect(ledger_path)
        try:
            state = connection.execute(
                "SELECT state FROM attempts WHERE job_id = ?", (job_id,)
            ).fetchone()
        finally:
            connection.close()
        assert state == ("submitting",)
        seen.append(job_id)
        return completed(0) if job_id == first.job_id else completed(7, stderr=b"quota")

    monkeypatch.setattr(campaign_cli, "_run", fake_run)
    statuses, failed = campaign_cli._submit_jobs(
        (first, second),
        ({"one": 1}, {"two": 2}),
        project="study",
        location="us-east1",
        ledger_path=ledger_path,
    )

    assert seen == [first.job_id, second.job_id]
    assert failed is True
    assert statuses == [
        {"job_id": first.job_id, "state": "submitted"},
        {"job_id": second.job_id, "state": "failed"},
    ]
    with ledger.open_ledger(ledger_path) as connection:
        rows = ledger.attempts(connection, campaign=first.campaign)
        assert {row["job_id"]: row["state"] for row in rows} == {
            first.job_id: "submitted",
            second.job_id: "failed",
        }
        failure = connection.execute(
            "SELECT detail FROM events WHERE job_id = ? AND event = 'failed'", (second.job_id,)
        ).fetchone()[0]
        assert json.loads(failure) == {"returncode": 7, "stderr": "quota"}


def seed_state(path: Path, selected: Attempt, state: str = "submitting") -> None:
    with ledger.open_ledger(path) as connection:
        ledger.record_intent(
            connection,
            attempt=selected.as_dict(),
            campaign=selected.campaign,
            now="2026-08-10T12:00:00Z",
        )
        if state != "submitting":
            ledger.record_state(
                connection,
                job_id=selected.job_id,
                state=state,
                now="2026-08-10T12:00:01Z",
            )


def test_restart_reissues_an_intent_left_before_the_api_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = attempt()
    path = tmp_path / "ledger.sqlite3"
    seed_state(path, selected)
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: Any, *, payload: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        del payload
        calls.append(tuple(argv))
        return completed()

    monkeypatch.setattr(campaign_cli, "_run", fake_run)
    statuses, failed = campaign_cli._submit_jobs(
        (selected,), ({"job": 1},), project="study", location="us-east1", ledger_path=path
    )
    assert len(calls) == 1
    assert statuses == [{"job_id": selected.job_id, "state": "submitted"}]
    assert failed is False


def test_restart_recovers_an_intent_when_batch_already_accepted_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = attempt()
    path = tmp_path / "ledger.sqlite3"
    seed_state(path, selected)
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: completed(1, stderr=b"ALREADY_EXISTS: job exists"),
    )
    statuses, failed = campaign_cli._submit_jobs(
        (selected,), ({"job": 1},), project="study", location="us-east1", ledger_path=path
    )
    assert statuses == [{"job_id": selected.job_id, "state": "submitted"}]
    assert failed is False
    with ledger.open_ledger(path) as connection:
        row = ledger.attempts(connection, campaign=selected.campaign)[0]
        assert row["state"] == "submitted"
        detail = connection.execute(
            "SELECT detail FROM events WHERE job_id = ? AND event = 'submitted'",
            (selected.job_id,),
        ).fetchone()[0]
        assert json.loads(detail)["recovered_from_already_exists"] is True


def test_restart_skips_a_job_already_recorded_as_submitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = attempt()
    path = tmp_path / "ledger.sqlite3"
    seed_state(path, selected, "submitted")
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("submitted job was created again"),
    )
    statuses, failed = campaign_cli._submit_jobs(
        (selected,), ({"job": 1},), project="study", location="us-east1", ledger_path=path
    )
    assert statuses == [{"job_id": selected.job_id, "state": "submitted"}]
    assert failed is False


@pytest.mark.parametrize("state", ["failed", "abandoned"])
def test_restart_never_reissues_terminal_unsuccessful_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    selected = attempt()
    path = tmp_path / "ledger.sqlite3"
    seed_state(path, selected, state)
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail(f"{state} job was created again"),
    )
    statuses, failed = campaign_cli._submit_jobs(
        (selected,), ({"job": 1},), project="study", location="us-east1", ledger_path=path
    )
    assert statuses == [{"job_id": selected.job_id, "state": state}]
    assert failed is True


def test_restart_refuses_a_ledger_row_that_does_not_match_the_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = attempt()
    requested = replace(recorded, prefix=recorded.prefix + "-different")
    path = tmp_path / "ledger.sqlite3"
    seed_state(path, recorded)
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("mismatched ledger row was submitted"),
    )
    statuses, failed = campaign_cli._submit_jobs(
        (requested,), ({"job": 1},), project="study", location="us-east1", ledger_path=path
    )
    assert statuses == [{"job_id": requested.job_id, "state": "ledger-mismatch"}]
    assert failed is True


def test_partial_campaign_skips_completed_work_and_continues_later_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = attempt()
    second = replace(
        first,
        job_id="c-2026-08-10-first-swath-recursive-ffffffff-r2-s1",
        run_ordinal=2,
        prefix=first.prefix.removesuffix("run-1") + "run-2",
    )
    path = tmp_path / "ledger.sqlite3"
    seed_state(path, first, "submitted")
    calls: list[str] = []

    def fake_run(argv: Any, *, payload: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        del payload
        calls.append(argv[4])
        return completed()

    monkeypatch.setattr(campaign_cli, "_run", fake_run)
    statuses, failed = campaign_cli._submit_jobs(
        (first, second),
        ({"job": 1}, {"job": 2}),
        project="study",
        location="us-east1",
        ledger_path=path,
    )
    assert calls == [second.job_id]
    assert [status["state"] for status in statuses] == ["submitted", "submitted"]
    assert failed is False


def test_new_ledger_intent_treats_an_existing_batch_name_as_a_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = attempt()
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: completed(1, stderr=b"ALREADY_EXISTS: job exists"),
    )
    statuses, failed = campaign_cli._submit_jobs(
        (selected,), ({"job": 1},), project="study", location="us-east1", ledger_path=path
    )
    assert statuses == [{"job_id": selected.job_id, "state": "failed"}]
    assert failed is True


def test_actual_cli_freezes_plan_then_manifest_before_submitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, image_set = write_inputs(tmp_path)
    calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def fake_run(argv: Any, *, payload: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        calls.append((tuple(argv), payload))
        return completed()

    monkeypatch.setattr(campaign_cli, "_run", fake_run)
    assert campaign_cli.submit_campaign_main(arguments(tmp_path, plan, image_set)) == 0
    assert calls[0][0][1:4] == ("storage", "cp", "-")
    assert calls[0][0][4].endswith("/inputs/plans/example-bucket.yaml")
    assert calls[0][1] == plan.read_bytes()
    assert calls[1][0][4].endswith("/campaign.json")
    assert calls[1][1] is not None
    assert json.loads(calls[1][1])["campaign"] == "2026-08-10-first"
    assert calls[2][0][:5] == (
        "gcloud",
        "batch",
        "jobs",
        "submit",
        json.loads(capsys.readouterr().out)["submissions"][0]["job_id"],
    )


def test_actual_cli_freezes_every_plan_before_one_manifest_and_all_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = write_plan(tmp_path / "first.yaml", bucket="first")
    second = write_plan(tmp_path / "second.yaml", bucket="second")
    image_set = write_image_set(tmp_path / "images.json", {"aws-cli"})
    calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def fake_run(argv: Any, *, payload: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        calls.append((tuple(argv), payload))
        return completed()

    monkeypatch.setattr(campaign_cli, "_run", fake_run)
    argv = [
        "--path",
        str(first),
        "--path",
        str(second),
        *arguments(tmp_path, first, image_set)[2:],
    ]

    assert campaign_cli.submit_campaign_main(argv) == 0
    assert [call[0][4] for call in calls[:2]] == [
        "gs://study-results/campaigns/2026-08-10-first/inputs/plans/first.yaml",
        "gs://study-results/campaigns/2026-08-10-first/inputs/plans/second.yaml",
    ]
    assert [call[1] for call in calls[:2]] == [first.read_bytes(), second.read_bytes()]
    assert calls[2][0][4].endswith("/campaign.json")
    manifest_payload = calls[2][1]
    assert manifest_payload is not None
    manifest_document = json.loads(manifest_payload)
    assert [plan["bucket"] for plan in manifest_document["plans"]] == ["first", "second"]
    assert [call[0][1:4] for call in calls[3:]] == [
        ("batch", "jobs", "submit"),
        ("batch", "jobs", "submit"),
    ]


def test_later_plan_hash_mismatch_prevents_every_cloud_and_ledger_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = write_plan(tmp_path / "first.yaml", bucket="first")
    second = write_plan(tmp_path / "second.yaml", bucket="second")
    image_set = write_image_set(tmp_path / "images.json", {"aws-cli"})
    cloud_calls: list[tuple[str, ...]] = []
    original_render_job = render_batch_job
    render_count = 0

    def render_job(selected: Any, config: Any) -> Any:
        nonlocal render_count
        render_count += 1
        if render_count == 1:
            second.write_text(second.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
        return original_render_job(selected, config)

    def fake_run(argv: Any, *, payload: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        del payload
        cloud_calls.append(tuple(argv))
        return completed()

    monkeypatch.setattr("s3_listing_study.manager.campaign.cli.render_job", render_job)
    monkeypatch.setattr(campaign_cli, "_run", fake_run)
    argv = [
        "--path",
        str(first),
        "--path",
        str(second),
        *arguments(tmp_path, first, image_set)[2:],
    ]

    assert campaign_cli.submit_campaign_main(argv) == 1
    assert render_count == 2
    assert f"plan changed after it was resolved: {second}" in capsys.readouterr().err
    assert cloud_calls == []
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_later_job_render_failure_prevents_every_cloud_and_ledger_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = write_plan(tmp_path / "first.yaml", bucket="first")
    second = write_plan(tmp_path / "second.yaml", bucket="second")
    image_set = write_image_set(tmp_path / "images.json", {"aws-cli"})
    original_render_job = render_batch_job
    render_count = 0

    def render_job(selected: Any, config: Any) -> Any:
        nonlocal render_count
        render_count += 1
        if render_count == 2:
            raise CampaignError("later render failed")
        return original_render_job(selected, config)

    monkeypatch.setattr("s3_listing_study.manager.campaign.cli.render_job", render_job)
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("render failure reached a cloud command"),
    )
    argv = [
        "--path",
        str(first),
        "--path",
        str(second),
        *arguments(tmp_path, first, image_set)[2:],
    ]

    assert campaign_cli.submit_campaign_main(argv) == 1
    assert render_count == 2
    assert "later render failed" in capsys.readouterr().err
    assert not (tmp_path / "ledger.sqlite3").exists()
