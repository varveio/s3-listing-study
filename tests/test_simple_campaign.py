from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import AlreadyExists, BadRequest
from google.cloud import batch_v1

sys.path.insert(0, str(Path(__file__).parents[1] / "simple"))
import campaign  # type: ignore[import-not-found]

from s3_listing_study.manager.bench.plan import Plan

ROOT = Path(__file__).parents[1]
DIGEST = "a" * 64
AUTH_SECRETS = {
    "authenticated": {
        "AWS_ACCESS_KEY_ID": "projects/p/secrets/access-key/versions/1",
        "AWS_SECRET_ACCESS_KEY": "projects/p/secrets/secret-key/versions/1",
    }
}


def image(tool: str) -> dict[str, str]:
    return {
        "image_uri": f"registry/{tool}@sha256:{DIGEST}",
        "tool_parent_image": f"registry/{tool}-parent@sha256:{'f' * 64}",
        "tool_version": "1.0",
        "tool_build_sha256": "b" * 64,
        "adapter_bundle_sha256": "c" * 64,
        "harness_revision": "d" * 40,
        "subject_workdir": "/",
    }


def test_all_current_plan_job_ids_are_unique_and_bound() -> None:
    for path in (ROOT / "bench/buckets").glob("*.yaml"):
        plan = Plan.load(path)
        images = {case.tool: image(case.tool) for case in plan.cases}
        image_set = campaign.ImageSet(images, "e" * 64)
        ids = campaign.planned_job_ids(plan, "2026-08-16-candidate", image_set)
        assert len(ids) == len(set(ids)) == sum(case.reps for case in plan.cases)
        assert all(len(job_id) <= 63 for job_id in ids)
        changed = campaign.job_id_for(
            plan.cases[0],
            1,
            campaign_id="2026-08-16-candidate",
            bucket=plan.bucket + "-other",
            region=plan.region,
            image_uri=images[plan.cases[0].tool]["image_uri"],
        )
        assert changed != ids[0]


def test_retry_ids_retain_collision_safe_identity_at_large_ordinals() -> None:
    first = "c-readable-" + "a" * 47
    second = "c-readable-" + "a" * 46 + "b"
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
    path.write_text(json.dumps({"schema_version": 1, "images": {"aws-cli": image("aws-cli")}}))
    loaded = campaign.load_image_set(path, {"aws-cli"})
    assert loaded.images["aws-cli"]["tool_version"] == "1.0"
    mutable = image("aws-cli")
    mutable["image_uri"] = "registry/aws-cli:latest"
    path.write_text(json.dumps({"schema_version": 1, "images": {"aws-cli": mutable}}))
    with pytest.raises(campaign.CampaignError, match="pinned"):
        campaign.load_image_set(path, {"aws-cli"})


def test_batch_render_passes_identity_resources_and_auth_policy() -> None:
    case = Plan.load(ROOT / "bench/buckets/noaa-ghcn-pds.yaml").cases[0]
    options = campaign.BatchOptions(
        "anonymous@example.test",
        "authenticated@example.test",
        "projects/p/global/networks/n",
        "projects/p/regions/r/subnetworks/s",
        "us-east1-b",
        "STANDARD",
    )
    job = campaign.render_batch_job(
        case,
        "gs://results/leaf/",
        image(case.tool),
        "e" * 64,
        AUTH_SECRETS,
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
        "--vcpus",
        "--memory-gb",
        "--subject-workdir",
    ):
        assert flag in commands
    allocation = job["allocationPolicy"]
    assert allocation["serviceAccount"]["email"] == "authenticated@example.test"
    assert "--pass-env" in commands
    assert allocation["instances"][0]["policy"]["provisioningModel"] == "STANDARD"
    assert allocation["network"]["networkInterfaces"][0]["network"].endswith("/n")
    assert allocation["location"]["allowedLocations"] == ["zones/us-east1-b"]


def test_authenticated_render_fails_closed_without_sa_and_secret() -> None:
    plan = Plan.load(ROOT / "bench/buckets/noaa-ghcn-pds.yaml")
    case = next(case for case in plan.cases if case.auth == "authenticated")
    options = campaign.BatchOptions("anon@example.test", None, None, None, None, "SPOT")
    with pytest.raises(campaign.CampaignError, match="authenticated worker"):
        campaign.render_batch_job(
            case,
            "gs://results/leaf/",
            image(case.tool),
            "e" * 64,
            {},
            campaign_id="2026-08-16-candidate",
            job_id="c-one",
            rep=1,
            submission=1,
            bucket=plan.bucket,
            region=plan.region,
            options=options,
        )


def test_intent_is_durable_before_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = Plan.load(ROOT / "bench/buckets/noaa-ghcn-pds.yaml")
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
        args,
        plan,
        case,
        campaign.ImageSet({case.tool: image(case.tool)}, "e" * 64),
        AUTH_SECRETS,
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
    case = replace(Plan.load(ROOT / "bench/buckets/noaa-ghcn-pds.yaml").cases[0], auth="anonymous")
    options = campaign.BatchOptions("anon@example.test", None, None, None, None, "SPOT")
    document = campaign.render_batch_job(
        case,
        "gs://results/leaf/",
        image(case.tool),
        "e" * 64,
        {},
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
    assert campaign.ensure_job("p", "l", "c-one", document, client=ExistingClient(job)) == "ADOPTED"
    job.labels["s3-study-intent"] = "collision"
    with pytest.raises(campaign.CampaignError, match="collides"):
        campaign.ensure_job("p", "l", "c-one", document, client=ExistingClient(job))


def test_permanent_create_rejection_is_definitively_not_created() -> None:
    class Client:
        def create_job(self, **_kwargs: object) -> batch_v1.Job:
            raise BadRequest("invalid")  # type: ignore[no-untyped-call]

        def get_job(self, **_kwargs: object) -> batch_v1.Job:
            pytest.fail("a permanent create rejection must not be probed as ambiguous")

    assert campaign.ensure_job("p", "l", "c-one", {}, client=Client()) == "NOT_CREATED"


def test_retry_rewrites_only_retry_identity() -> None:
    case = replace(Plan.load(ROOT / "bench/buckets/noaa-ghcn-pds.yaml").cases[0], auth="anonymous")
    options = campaign.BatchOptions("anon@example.test", None, None, None, None, "SPOT")
    first = campaign.render_batch_job(
        case,
        "gs://results/submission-1/",
        image(case.tool),
        "e" * 64,
        {},
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
        {},
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
    plan = Plan.load(ROOT / "bench/buckets/noaa-ghcn-pds.yaml")
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
            SimpleNamespace(state=str(tmp_path / "campaign.db"), job_id=accepted_job)
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


def test_cancel_waits_for_delete_settlement(monkeypatch: pytest.MonkeyPatch) -> None:
    settled: list[float] = []

    class Operation:
        def result(self, timeout: float) -> None:
            settled.append(timeout)

    class Client:
        def delete_job(self, **_kwargs: object) -> Operation:
            return Operation()

    monkeypatch.setattr(campaign.batch_v1, "BatchServiceClient", Client)
    campaign.cancel_job("p", "l", "job", client=Client())
    assert settled == [60]


def test_dry_run_renders_every_case_without_sqlite_or_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = ROOT / "bench/buckets/noaa-ghcn-pds.yaml"
    plan = Plan.load(plan_path)
    image_path = tmp_path / "images.json"
    image_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "images": {case.tool: image(case.tool) for case in plan.cases},
            }
        )
    )
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text(
        "authenticated:\n"
        "  AWS_ACCESS_KEY_ID: projects/p/secrets/access-key/versions/1\n"
        "  AWS_SECRET_ACCESS_KEY: projects/p/secrets/secret-key/versions/1\n"
    )
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
            "--secrets",
            str(secrets),
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
