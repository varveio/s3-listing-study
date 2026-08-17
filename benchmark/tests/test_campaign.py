from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from google.api_core.exceptions import AlreadyExists, BadRequest, Forbidden, NotFound
from google.cloud import batch_v1

from benchmark import campaign
from benchmark.contract import CREDENTIAL_ENV_VAR, TOOLBOX_TOOLS
from benchmark.plan import Plan

ROOT = Path(__file__).parents[2]
DIGEST = "a" * 64
AUTH_SECRET = "projects/p/secrets/aws-credentials/versions/1"


def tool_image(tool: str) -> dict[str, str]:
    return {
        "tool_version": "1.0",
        "tool_build_sha256": "b" * 64,
        "tool_artifact_kind": "release-binary",
        "tool_artifact_locator": f"https://example.test/{tool}",
        "tool_artifact_sha256": "f" * 64,
        "recipe_sha256": "7" * 64,
        "build_inputs_sha256": "8" * 64,
        "adapter_bundle_sha256": "c" * 64,
        "subject_workdir": "/",
    }


def image_set() -> campaign.ImageSet:
    return campaign.ImageSet(
        f"registry/toolbox@sha256:{DIGEST}",
        "9" * 64,
        "8" * 64,
        "d" * 40,
        {tool: tool_image(tool) for tool in TOOLBOX_TOOLS},
        "e" * 64,
    )


def image(tool: str) -> dict[str, str]:
    return image_set().image_for(tool)


def image_set_document() -> dict[str, object]:
    selected = image_set()
    return {
        "schema_version": 4,
        "image_uri": selected.image_uri,
        "toolbox_manifest_sha256": selected.toolbox_manifest_sha256,
        "toolbox_recipe_sha256": selected.toolbox_recipe_sha256,
        "harness_revision": selected.harness_revision,
        "tools": selected.tools,
    }


def test_all_current_plan_job_ids_are_unique_and_bound() -> None:
    for path in (ROOT / "benchmark/plans/buckets").glob("*.yaml"):
        plan = Plan.load(path)
        selected_image_set = image_set()
        ids = campaign.planned_job_ids(plan, "2026-08-16-candidate", selected_image_set)
        assert len(ids) == len(set(ids)) == sum(case.reps for case in plan.cases)
        assert all(len(job_id) <= 63 for job_id in ids)
        changed = campaign.job_id_for(
            plan.cases[0],
            1,
            campaign_id="2026-08-16-candidate",
            bucket=plan.bucket + "-other",
            region=plan.region,
            image_uri=selected_image_set.image_uri,
        )
        assert changed != ids[0]


def test_every_tool_uses_the_same_runtime_image() -> None:
    selected = image_set()
    assert {selected.image_for(tool)["image_uri"] for tool in selected.tools} == {
        selected.image_uri
    }


def test_retry_ids_retain_collision_safe_identity_at_large_ordinals() -> None:
    first = "benchmark-readable-" + "a" * 39
    second = "benchmark-readable-" + "a" * 38 + "b"
    for submission in (2, 10, 10_000):
        first_retry = campaign.submission_job_id(first, submission)
        second_retry = campaign.submission_job_id(second, submission)
        assert first_retry != second_retry
        assert len(first_retry) <= 63
        assert first_retry.endswith(
            hashlib.sha256(f"{first}:{submission}".encode()).hexdigest()[:12]
        )


def test_image_set_requires_pinned_complete_provenance(tmp_path: Path) -> None:
    path = tmp_path / "images.json"
    path.write_text(json.dumps(image_set_document()))
    loaded = campaign.load_image_set(path, {"aws-cli"})
    assert loaded.tools["aws-cli"]["tool_version"] == "1.0"
    mutable = image_set_document()
    mutable["image_uri"] = "registry/toolbox:latest"
    path.write_text(json.dumps(mutable))
    with pytest.raises(campaign.CampaignError, match="pinned"):
        campaign.load_image_set(path, {"aws-cli"})


def test_batch_render_passes_identity_resources_and_auth_policy() -> None:
    case = Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml").cases[0]
    options = campaign.BatchOptions(
        "anonymous@example.test",
        "authenticated@example.test",
        "projects/p/global/networks/n",
        "projects/p/regions/r/subnetworks/s",
        "us-east1-b",
        "STANDARD",
        AUTH_SECRET,
    )
    job = campaign.render_batch_job(
        case,
        "gs://results/leaf/",
        image(case.tool),
        "e" * 64,
        campaign_id="2026-08-16-candidate",
        job_id="c-one",
        rep=1,
        submission=1,
        bucket="bucket",
        region="us-east-1",
        options=options,
    )
    commands = job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"]["commands"]
    for flag in (
        "--bucket",
        "--region",
        "--campaign-id",
        "--case-fingerprint",
        "--image-set-sha256",
        "--toolbox-manifest-sha256",
        "--tool-recipe-sha256",
        "--vcpus",
        "--memory-gb",
        "--subject-workdir",
    ):
        assert flag in commands
    allocation = job["allocationPolicy"]
    assert allocation["serviceAccount"]["email"] == "authenticated@example.test"
    environment = job["taskGroups"][0]["taskSpec"]["environment"]
    assert environment["secretVariables"] == {CREDENTIAL_ENV_VAR: AUTH_SECRET}
    assert not any(command.startswith("projects/") for command in commands)
    assert allocation["instances"][0]["policy"]["provisioningModel"] == "STANDARD"
    assert allocation["network"]["networkInterfaces"][0]["network"].endswith("/n")
    assert allocation["location"]["allowedLocations"] == ["zones/us-east1-b"]


def test_authenticated_render_fails_closed_without_sa_and_secret() -> None:
    plan = Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml")
    case = next(case for case in plan.cases if case.auth == "authenticated")
    options = campaign.BatchOptions("anon@example.test", None, None, None, None, "SPOT")
    with pytest.raises(campaign.CampaignError, match="authenticated worker"):
        campaign.render_batch_job(
            case,
            "gs://results/leaf/",
            image(case.tool),
            "e" * 64,
            campaign_id="2026-08-16-candidate",
            job_id="c-one",
            rep=1,
            submission=1,
            bucket=plan.bucket,
            region=plan.region,
            options=options,
        )


def test_anonymous_case_carries_no_credential_even_when_one_is_configured() -> None:
    """The stratum decides: a configured secret reaches authenticated cases only."""
    case = replace(
        Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml").cases[0], auth="anonymous"
    )
    options = campaign.BatchOptions(
        "anon@example.test",
        "auth@example.test",
        None,
        None,
        None,
        "SPOT",
        AUTH_SECRET,
    )
    job = campaign.render_batch_job(
        case,
        "gs://results/leaf/",
        image(case.tool),
        "e" * 64,
        campaign_id="2026-08-16-candidate",
        job_id="c-one",
        rep=1,
        submission=1,
        bucket="bucket",
        region="us-east-1",
        options=options,
    )
    task_spec = job["taskGroups"][0]["taskSpec"]
    assert "environment" not in task_spec
    assert job["allocationPolicy"]["serviceAccount"]["email"] == "anon@example.test"
    assert AUTH_SECRET not in json.dumps(job)


def test_intent_is_durable_before_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml")
    case = plan.cases[0]
    con = campaign.open_db(str(tmp_path / "campaign.db"))
    args = SimpleNamespace(
        results_bucket="results",
        campaign_id="2026-08-16-candidate",
        anonymous_worker_sa="anon@example.test",
        authenticated_worker_sa="auth@example.test",
        network=None,
        subnetwork=None,
        zone=None,
        provisioning="SPOT",
        secret_resource=AUTH_SECRET,
        project="p",
        location="l",
    )

    def observe_intent(*_args: object, **_kwargs: object) -> str:
        row = con.execute("SELECT state, job_json FROM submissions").fetchone()
        assert row["state"] == "SUBMITTING"
        assert json.loads(row["job_json"])["taskGroups"]
        return "SUBMITTED"

    monkeypatch.setattr(campaign, "ensure_job", observe_intent)
    campaign._submit_one(
        con,
        cast(argparse.Namespace, args),
        plan,
        case,
        image_set(),
        1,
        1,
        "c-test",
    )
    assert con.execute("SELECT state FROM submissions").fetchone()[0] == "SUBMITTED"


class ExistingClient:
    def __init__(self, job: batch_v1.Job) -> None:
        self.job = job

    def create_job(self, **_kwargs: object) -> batch_v1.Job:
        raise AlreadyExists("exists")  # type: ignore[no-untyped-call]

    def get_job(self, **_kwargs: object) -> batch_v1.Job:
        return self.job


def test_already_exists_adopts_only_exact_job() -> None:
    case = replace(
        Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml").cases[0], auth="anonymous"
    )
    options = campaign.BatchOptions("anon@example.test", None, None, None, None, "SPOT")
    document = campaign.render_batch_job(
        case,
        "gs://results/leaf/",
        image(case.tool),
        "e" * 64,
        campaign_id="2026-08-16-candidate",
        job_id="c-one",
        rep=1,
        submission=1,
        bucket="bucket",
        region="region",
        options=options,
    )
    job = campaign._job_from_dict(document)
    job.name = "projects/p/locations/l/jobs/c-one"
    assert (
        campaign.ensure_job(
            "p",
            "l",
            "c-one",
            document,
            client=cast(batch_v1.BatchServiceClient, ExistingClient(job)),
        )
        == "ADOPTED"
    )
    job.labels["benchmark-intent"] = "collision"
    with pytest.raises(campaign.CampaignError, match="collides"):
        campaign.ensure_job(
            "p",
            "l",
            "c-one",
            document,
            client=cast(batch_v1.BatchServiceClient, ExistingClient(job)),
        )


def test_provider_resolved_locations_do_not_read_as_a_different_job() -> None:
    """Batch expands an unrestricted request into the region and its zones."""
    plan = Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml")
    case = replace(plan.cases[0], auth="anonymous")
    unrestricted = campaign.BatchOptions("anon@example.test", None, None, None, None, "SPOT")
    document = campaign.render_batch_job(
        case,
        "gs://results/leaf/",
        image(case.tool),
        "e" * 64,
        campaign_id="2026-08-16-candidate",
        job_id="c-one",
        rep=1,
        submission=1,
        bucket="bucket",
        region="region",
        options=unrestricted,
    )
    name = "projects/p/locations/us-east1/jobs/c-one"
    resolved = campaign._job_from_dict(document)
    resolved.name = name
    resolved.allocation_policy.location.allowed_locations.extend(
        ["regions/us-east1", "zones/us-east1-b", "zones/us-east1-c"]
    )
    assert campaign._adoption_exact(resolved, name, document, "us-east1")

    zoned = replace(unrestricted, zone="us-east1-b")
    pinned_document = campaign.render_batch_job(
        case,
        "gs://results/leaf/",
        image(case.tool),
        "e" * 64,
        campaign_id="2026-08-16-candidate",
        job_id="c-one",
        rep=1,
        submission=1,
        bucket="bucket",
        region="region",
        options=zoned,
    )
    elsewhere = campaign._job_from_dict(pinned_document)
    elsewhere.name = name
    del elsewhere.allocation_policy.location.allowed_locations[:]
    elsewhere.allocation_policy.location.allowed_locations.append("zones/us-east1-c")
    assert not campaign._adoption_exact(elsewhere, name, pinned_document, "us-east1")


def test_permanent_create_rejection_is_definitively_not_created() -> None:
    class Client:
        def create_job(self, **_kwargs: object) -> batch_v1.Job:
            raise BadRequest("invalid")  # type: ignore[no-untyped-call]

        def get_job(self, **_kwargs: object) -> batch_v1.Job:
            pytest.fail("a permanent create rejection must not be probed as ambiguous")

    assert (
        campaign.ensure_job(
            "p", "l", "c-one", {}, client=cast(batch_v1.BatchServiceClient, Client())
        )
        == "NOT_CREATED"
    )


def test_retry_rewrites_only_retry_identity() -> None:
    case = replace(
        Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml").cases[0], auth="anonymous"
    )
    options = campaign.BatchOptions("anon@example.test", None, None, None, None, "SPOT")
    first = campaign.render_batch_job(
        case,
        "gs://results/submission-1/",
        image(case.tool),
        "e" * 64,
        campaign_id="2026-08-16-candidate",
        job_id="c-one",
        rep=1,
        submission=1,
        bucket="bucket",
        region="region",
        options=options,
    )
    rewritten = campaign.retry_job_document(
        first,
        job_id="c-two",
        destination="gs://results/submission-2/",
        submission=2,
    )
    expected = campaign.render_batch_job(
        case,
        "gs://results/submission-2/",
        image(case.tool),
        "e" * 64,
        campaign_id="2026-08-16-candidate",
        job_id="c-two",
        rep=1,
        submission=2,
        bucket="bucket",
        region="region",
        options=options,
    )
    assert rewritten == expected


def test_plan_binding_and_retry_evidence_are_fail_closed(tmp_path: Path) -> None:
    plan = Plan.load(ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml")
    con = campaign.open_db(str(tmp_path / "campaign.db"))
    for case in plan.cases:
        for rep in range(1, case.reps + 1):
            campaign.record_intent(
                con,
                base_job_id=f"{case.tool}-{case.case_id}-{rep}",
                submission=1,
                job_id=f"{case.tool}-{case.case_id}-{rep}",
                campaign_id="2026-08-16-candidate",
                project="p",
                location="l",
                case=case,
                rep=rep,
                bucket=plan.bucket,
                region=plan.region,
                image_uri=image(case.tool)["image_uri"],
                image_set_sha256="e" * 64,
                destination=str(tmp_path / case.tool / case.case_id / str(rep)),
                job_dict={},
            )
    rows = campaign.latest_submissions(con)
    assert campaign.plan_binding_errors(plan, rows) == []
    con.execute("UPDATE submissions SET fingerprint='changed' WHERE job_id=?", (rows[0]["job_id"],))
    changed = campaign.latest_submissions(con)
    assert any(
        "changed plan case" in error for error in campaign.plan_binding_errors(plan, changed)
    )
    accepted_job = changed[0]["job_id"]
    campaign.update_submission_state(con, accepted_job, "FAILED")
    con.close()
    assert (
        campaign.cmd_accept_failure(
            cast(
                argparse.Namespace,
                SimpleNamespace(state=str(tmp_path / "campaign.db"), job_id=accepted_job),
            )
        )
        == 0
    )
    check = campaign.open_db(str(tmp_path / "campaign.db"), readonly=True)
    assert (
        check.execute("SELECT state FROM submissions WHERE job_id=?", (accepted_job,)).fetchone()[0]
        == "ACCEPTED_FAILED"
    )
    check.close()

    destination = tmp_path / "retry"
    leaf = destination / "attempt"
    leaf.mkdir(parents=True)
    assert campaign.retry_evidence_state(str(destination)) == "INCOMPLETE"
    (leaf / "result.json").write_text("{}")
    assert campaign.retry_evidence_state(str(destination)) == "COMPLETE"


@pytest.mark.parametrize(
    ("codes", "expected"),
    [([0, 0], 0), ([0, 2], 2), ([0, 1], 1), ([0, 6], 1), ([], 1)],
)
def test_verify_aggregate_semantics(codes: list[int], expected: int) -> None:
    assert campaign.aggregate_verify_exit(codes) == expected


def _tracked_submission(con: sqlite3.Connection, job_id: str, state: str) -> None:
    now = "2026-08-17T00:00:00+00:00"
    con.execute(
        "INSERT INTO submissions (base_job_id, submission, job_id, campaign_id, project, "
        "location, tool, mode, case_id, fingerprint, rep, bucket, region, image_uri, "
        "image_set_sha256, destination, job_json, state, submitted_at, updated_at) "
        "VALUES (?,1,?,'2026-08-17-c','p','l','aws-cli','m','c','f',1,'b','r','i','s','d','{}',"
        "?,?,?)",
        (job_id, job_id, state, now, now),
    )
    con.commit()


def _listed_job(job_id: str, state: str) -> batch_v1.Job:
    job = batch_v1.Job()
    job.name = f"projects/p/locations/l/jobs/{job_id}"
    job.status.state = getattr(batch_v1.JobStatus.State, state)
    return job


def test_poll_reads_every_submission_in_one_listing(tmp_path: Path) -> None:
    con = campaign.open_db(str(tmp_path / "campaign.db"))
    _tracked_submission(con, "job-one", "SUBMITTED")
    _tracked_submission(con, "job-two", "SUBMITTED")
    _tracked_submission(con, "job-settled", "SUCCEEDED")
    calls: list[str] = []

    class Client:
        def list_jobs(self, **kwargs: object) -> list[batch_v1.Job]:
            calls.append("list")
            request = cast(dict[str, str], kwargs["request"])
            assert request["filter"] == campaign.BENCHMARK_JOB_FILTER
            assert request["parent"] == "projects/p/locations/l"
            return [_listed_job("job-one", "RUNNING"), _listed_job("job-two", "FAILED")]

        def get_job(self, **_kwargs: object) -> batch_v1.Job:
            pytest.fail("a listed submission must not be described individually")

    rows = campaign.latest_submissions(con)
    terminal = campaign.poll_once(
        "p", "l", con, rows, client=cast(batch_v1.BatchServiceClient, Client())
    )
    assert calls == ["list"]  # one request for the whole pass, not one per job
    assert not terminal
    states = {row["job_id"]: row["state"] for row in campaign.latest_submissions(con)}
    assert states == {"job-one": "RUNNING", "job-two": "FAILED", "job-settled": "SUCCEEDED"}


def test_poll_describes_only_what_the_listing_left_out(tmp_path: Path) -> None:
    con = campaign.open_db(str(tmp_path / "campaign.db"))
    _tracked_submission(con, "job-listed", "SUBMITTED")
    _tracked_submission(con, "job-absent", "SUBMITTED")
    described: list[str] = []

    class Client:
        def list_jobs(self, **_kwargs: object) -> list[batch_v1.Job]:
            return [_listed_job("job-listed", "SUCCEEDED")]

        def get_job(self, **kwargs: object) -> batch_v1.Job:
            name = cast(str, kwargs["name"])
            described.append(name)
            raise NotFound("no such job")  # type: ignore[no-untyped-call]

    rows = campaign.latest_submissions(con)
    terminal = campaign.poll_once(
        "p", "l", con, rows, client=cast(batch_v1.BatchServiceClient, Client())
    )
    assert described == ["projects/p/locations/l/jobs/job-absent"]
    assert not terminal
    states = {row["job_id"]: row["state"] for row in campaign.latest_submissions(con)}
    assert states == {"job-listed": "SUCCEEDED", "job-absent": "SUBMITTED"}


def test_poll_falls_back_to_describes_when_the_listing_fails(tmp_path: Path) -> None:
    con = campaign.open_db(str(tmp_path / "campaign.db"))
    _tracked_submission(con, "job-one", "SUBMITTED")

    class Client:
        def list_jobs(self, **_kwargs: object) -> list[batch_v1.Job]:
            raise Forbidden("listing denied")  # type: ignore[no-untyped-call]

        def get_job(self, **_kwargs: object) -> batch_v1.Job:
            return _listed_job("job-one", "SUCCEEDED")

    rows = campaign.latest_submissions(con)
    assert campaign.poll_once(
        "p", "l", con, rows, client=cast(batch_v1.BatchServiceClient, Client())
    )
    assert campaign.latest_submissions(con)[0]["state"] == "SUCCEEDED"


def test_cancel_waits_for_delete_settlement(monkeypatch: pytest.MonkeyPatch) -> None:
    settled: list[float] = []

    class Operation:
        def result(self, timeout: float) -> None:
            settled.append(timeout)

    class Client:
        def delete_job(self, **_kwargs: object) -> Operation:
            return Operation()

    monkeypatch.setattr(batch_v1, "BatchServiceClient", Client)
    campaign.cancel_job("p", "l", "job", client=Client())  # type: ignore[arg-type]
    assert settled == [60]


def test_dry_run_renders_every_case_without_sqlite_or_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml"
    plan = Plan.load(plan_path)
    image_path = tmp_path / "images.json"
    image_path.write_text(json.dumps(image_set_document()))
    monkeypatch.setattr(
        campaign,
        "ensure_job",
        lambda *_args, **_kwargs: pytest.fail("dry-run contacted Batch"),
    )
    state = tmp_path / "campaign.db"
    result = campaign.main(
        [
            "--state",
            str(state),
            "submit",
            "--project",
            "p",
            "--location",
            "l",
            "--plan",
            str(plan_path),
            "--campaign-id",
            "2026-08-16-candidate",
            "--results-bucket",
            "results",
            "--image-set",
            str(image_path),
            "--secret-resource",
            AUTH_SECRET,
            "--anonymous-worker-sa",
            "anon@example.test",
            "--authenticated-worker-sa",
            "auth@example.test",
            "--dry-run",
        ]
    )
    lines = capsys.readouterr().out.splitlines()
    assert result == 0
    assert len(lines) == len(plan.cases)
    assert all(json.loads(line.split(" ", 1)[1])["taskGroups"] for line in lines)
    assert not state.exists()


def test_submit_rejects_wrong_provider_parent_before_terminal_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = ROOT / "benchmark/plans/buckets/noaa-ghcn-pds.yaml"
    plan = Plan.load(plan_path)
    selected = image_set()
    image_path = tmp_path / "images.json"
    image_path.write_text(json.dumps(image_set_document()))
    state_path = tmp_path / "campaign.db"
    case = plan.cases[0]
    base = campaign.job_id_for(
        case,
        1,
        campaign_id="2026-08-16-candidate",
        bucket=plan.bucket,
        region=plan.region,
        image_uri=selected.image_uri,
    )
    con = campaign.open_db(str(state_path))
    campaign.record_intent(
        con,
        base_job_id=base,
        submission=1,
        job_id=base,
        campaign_id="2026-08-16-candidate",
        project="old-project",
        location="old-location",
        case=case,
        rep=1,
        bucket=plan.bucket,
        region=plan.region,
        image_uri=selected.image_uri,
        image_set_sha256=selected.sha256,
        destination="gs://results/existing/",
        job_dict={},
    )
    campaign.update_submission_state(con, base, "SUCCEEDED")
    con.close()
    monkeypatch.setattr(
        campaign, "ensure_job", lambda *_args, **_kwargs: pytest.fail("must fail before Batch")
    )
    assert (
        campaign.main(
            [
                "--state",
                str(state_path),
                "submit",
                "--project",
                "new-project",
                "--location",
                "new-location",
                "--plan",
                str(plan_path),
                "--campaign-id",
                "2026-08-16-candidate",
                "--results-bucket",
                "results",
                "--image-set",
                str(image_path),
                "--anonymous-worker-sa",
                "anon@example.test",
            ]
        )
        == 1
    )
    assert "different provider parent" in capsys.readouterr().err
