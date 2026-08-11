"""Campaign CLI preparation, freezing, and Temporal dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import textwrap
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.envconfig import ClientConfig as TemporalClientConfig

from s3_listing_study.common.build_selection import load_registered_selection
from s3_listing_study.manager import cli as manager_cli
from s3_listing_study.manager.bench import plan as bench
from s3_listing_study.manager.campaign import CampaignError
from s3_listing_study.manager.campaign import cli as campaign_cli
from s3_listing_study.manager.campaign import control as campaign_control
from s3_listing_study.manager.campaign import report as campaign_report
from s3_listing_study.temporal.models import CaseControllerProgress
from tests.test_campaign_batch import DIGEST, attempt

ROOT = Path(__file__).resolve().parents[1]
SAFE_TEMPORAL_CONFIG: dict[str, Any] = {
    "target_host": "temporal.example.invalid:7233",
    "namespace": "s3-study",
    "api_key": "temporal-test-api-key",
    "tls": True,
}


@pytest.fixture(autouse=True)
def safe_temporal_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        TemporalClientConfig,
        "load_client_connect_config",
        lambda: dict(SAFE_TEMPORAL_CONFIG),
    )


def write_inputs(
    tmp_path: Path, *, auth: str = "anonymous", timeout_s: int = 3600
) -> tuple[Path, Path]:
    plan = tmp_path / "example-bucket.yaml"
    plan.write_text(
        textwrap.dedent(
            f"""\
            spec_version: 2
            bucket: example-bucket
            region: us-east-1
            defaults:
              reps: 1
              timeout_s: {timeout_s}
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
    image_set = write_image_set(tmp_path / "images.json", {"aws-cli"})
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
        selection = load_registered_selection(ROOT, tool)
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
            "tool_image_digest": "sha256:" + "c" * 64,
            "tool_image_uri": f"registry.example/tool/{tool}@sha256:" + "c" * 64,
            "selection_sha256": selection.selection_sha256,
        }
    path.write_text(json.dumps({"schema_version": 3, "images": images}), encoding="utf-8")
    return path


def arguments(plan: Path, image_set: Path) -> list[str]:
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
        "anonymous@study.iam.gserviceaccount.com",
    ]


def completed(
    returncode: int = 0, *, stdout: bytes = b"", stderr: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_manager_dispatches_submit_and_has_no_deleted_watch_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str] | None] = []

    def submit(argv: Any) -> int:
        seen.append(None if argv is None else list(argv))
        return 7

    monkeypatch.setattr(campaign_cli, "submit_campaign_main", submit)
    assert manager_cli.main(["submit-campaign", "--campaign", "2026-08-10-first"]) == 7
    assert seen == [["--campaign", "2026-08-10-first"]]
    help_text = manager_cli.build_parser().format_help()
    assert "submit-campaign" in help_text
    assert "watch-campaign" not in help_text
    with pytest.raises(SystemExit):
        manager_cli.main(["watch-campaign"])


def test_manager_dispatches_retry_and_finalize_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, list[str]]] = []

    def retry(argv: Any) -> int:
        seen.append(("retry", list(argv or ())))
        return 7

    def finalize(argv: Any) -> int:
        seen.append(("finalize", list(argv or ())))
        return 8

    monkeypatch.setattr(
        campaign_control,
        "retry_case_main",
        retry,
    )
    monkeypatch.setattr(
        campaign_control,
        "finalize_campaign_main",
        finalize,
    )
    assert manager_cli.main(["retry-case", "--campaign", "campaign"]) == 7
    assert manager_cli.main(["finalize-campaign", "--campaign", "campaign"]) == 8
    assert seen == [
        ("retry", ["--campaign", "campaign"]),
        ("finalize", ["--campaign", "campaign"]),
    ]


def test_retry_update_uses_original_case_and_exact_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, Any, dict[str, Any]]] = []

    class Handle:
        async def execute_update(self, update: Any, arg: Any = None, **kwargs: Any) -> Any:
            calls.append((update, arg, kwargs))
            return CaseControllerProgress(
                "job-s1", None, "running", None, None, None, False, 2, "job-s2"
            )

    async def owned(*_args: Any) -> tuple[Any, Any]:
        frozen = (
            SimpleNamespace(cases=(SimpleNamespace(job_id="job-s1"),)),
            SimpleNamespace(cases=(SimpleNamespace(job_id="job-s1"),)),
        )
        return Handle(), frozen

    monkeypatch.setattr(campaign_control, "_owned_handle", owned)
    args = campaign_control.retry_parser().parse_args(
        [
            "--campaign",
            "campaign",
            "--results-bucket",
            "results",
            "--job-id",
            "job-s1",
            "--submission",
            "2",
        ]
    )
    result = asyncio.run(campaign_control._retry(args))
    assert result["current_job_id"] == "job-s2"
    assert calls[0][1].job_id == "job-s1"
    assert calls[0][1].submission == 2
    assert calls[0][2]["id"] == "retry-job-s1-s2"


def test_finalize_uses_one_owner_bound_update(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    class Handle:
        async def execute_update(self, update: Any, **kwargs: Any) -> Any:
            calls.append((update, kwargs))
            return [
                CaseControllerProgress("job-s1", None, "terminal", "FAILED", None, "resource", True)
            ]

    async def owned(*_args: Any) -> tuple[Any, Any]:
        return Handle(), (object(), object())

    monkeypatch.setattr(campaign_control, "_owned_handle", owned)
    args = campaign_control.finalize_parser().parse_args(
        ["--campaign", "campaign", "--results-bucket", "results"]
    )
    result = asyncio.run(campaign_control._finalize(args))
    assert result[0]["phase"] == "terminal"
    assert "id" not in calls[0][1]


def test_submit_help_exposes_temporal_inputs_and_no_ledger() -> None:
    help_text = campaign_cli.build_parser().format_help()
    assert "--bucket" in help_text
    assert "--path" in help_text
    assert "--anonymous-worker-sa" in help_text
    assert "--authenticated-worker-sa" in help_text
    assert "--secret-resource" in help_text
    assert "--ledger" not in help_text


def test_spot_is_the_default_provisioning_model(tmp_path: Path) -> None:
    parsed = campaign_cli.build_parser().parse_args(
        arguments(tmp_path / "plan.yaml", tmp_path / "images.json")
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
        *arguments(buckets / "unused.yaml", image_set)[2:],
        "--dry-run",
    ]
    assert campaign_cli.submit_campaign_main(argv) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert roster_checks == ["first", "second"]
    assert [plan["bucket"] for plan in rendered["campaign.json"]["plans"]] == [
        "first",
        "second",
    ]
    assert len(rendered["jobs"]) == 2
    assert len({job["job_id"] for job in rendered["jobs"]}) == 2


def test_image_set_must_cover_the_union_of_all_plan_tools(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = write_plan(tmp_path / "first.yaml", bucket="first")
    second = write_plan(tmp_path / "second.yaml", bucket="second", tool="s5cmd")
    image_set = write_image_set(tmp_path / "images.json", {"aws-cli"})
    argv = [
        "--path",
        str(first),
        "--path",
        str(second),
        *arguments(first, image_set)[2:],
        "--dry-run",
    ]
    assert campaign_cli.submit_campaign_main(argv) == 1
    assert "image set does not exactly cover the plans (missing s5cmd)" in capsys.readouterr().err


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
        *arguments(first, image_set)[2:],
        "--dry-run",
    ]
    assert campaign_cli.submit_campaign_main(argv) == 1
    assert "more than one plan for bucket 'same'" in capsys.readouterr().err


@pytest.mark.parametrize("schema_version", [2, 99])
def test_image_set_refuses_noncurrent_schema(tmp_path: Path, schema_version: int) -> None:
    _plan, image_set = write_inputs(tmp_path)
    document = json.loads(image_set.read_text(encoding="utf-8"))
    document["schema_version"] = schema_version
    image_set.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(campaign_cli.SubmissionError, match="schema_version must be 3"):
        campaign_cli._read_image_set(image_set)


def test_dry_run_is_deterministic_and_has_no_cloud_or_temporal_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, image_set = write_inputs(tmp_path)
    argv = [*arguments(plan, image_set), "--dry-run"]
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("dry-run called a subprocess"),
    )

    async def no_start(**_kwargs: Any) -> str:
        pytest.fail("dry-run contacted Temporal")

    monkeypatch.setattr(campaign_cli, "_start_workflow", no_start)
    assert campaign_cli.submit_campaign_main(argv) == 0
    first = capsys.readouterr().out
    assert campaign_cli.submit_campaign_main(argv) == 0
    second = capsys.readouterr().out
    assert first == second
    rendered = json.loads(first)
    job = rendered["jobs"][0]["job"]
    assert job["taskGroups"][0]["taskSpec"]["maxRetryCount"] == 0
    assert job["allocationPolicy"]["serviceAccount"]["email"].startswith("anonymous@")
    assert len(job["labels"]["s3-study-attempt"]) == 52


def test_authenticated_configuration_reaches_the_pure_batch_renderer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, image_set = write_inputs(tmp_path, auth="authenticated")
    argv = [
        *arguments(plan, image_set),
        "--authenticated-worker-sa",
        "authenticated@study.iam.gserviceaccount.com",
        "--secret-resource",
        "projects/study/secrets/aws/versions/7",
        "--network",
        "projects/study/global/networks/benchmark",
        "--subnetwork",
        "projects/study/regions/us-east1/subnetworks/benchmark",
        "--provisioning",
        "STANDARD",
        "--zone",
        "us-east1-b",
        "--post-attempt-allowance-s",
        "77",
        "--dry-run",
    ]
    assert campaign_cli.submit_campaign_main(argv) == 0
    job = json.loads(capsys.readouterr().out)["jobs"][0]["job"]
    allocation = job["allocationPolicy"]
    task = job["taskGroups"][0]["taskSpec"]
    assert allocation["serviceAccount"]["email"].startswith("authenticated@")
    assert allocation["instances"][0]["policy"]["provisioningModel"] == "STANDARD"
    assert allocation["location"] == {"allowedLocations": ["zones/us-east1-b"]}
    assert allocation["network"]["networkInterfaces"][0]["network"].endswith("/benchmark")
    assert task["environment"]["secretVariables"]["S3_STUDY_AWS_CREDENTIAL"].endswith("/versions/7")
    assert task["maxRunDuration"] == "3682s"


def test_prepare_rejects_empty_and_duplicate_job_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, image_set = write_inputs(tmp_path)
    args = campaign_cli.build_parser().parse_args(arguments(plan, image_set))
    scope = campaign_cli.TemporalScope("temporal.example.invalid:7233", "s3-study")
    monkeypatch.setattr(campaign_cli, "attempts_for", lambda *_args, **_kwargs: ())
    with pytest.raises(campaign_cli.SubmissionError, match="no scheduled runs"):
        campaign_cli._prepare(args, scope)
    selected = attempt()
    monkeypatch.setattr(
        campaign_cli, "attempts_for", lambda *_args, **_kwargs: (selected, selected)
    )
    with pytest.raises(campaign_cli.SubmissionError, match="duplicate Batch job IDs"):
        campaign_cli._prepare(args, scope)


def test_committed_long_case_controller_timeout_covers_batch_duration(tmp_path: Path) -> None:
    plan, image_set = write_inputs(tmp_path, timeout_s=28_800)
    args = campaign_cli.build_parser().parse_args(arguments(plan, image_set))
    request = campaign_cli._prepare(
        args, campaign_cli.TemporalScope("temporal.example.invalid:7233", "s3-study")
    )[0]
    selected = request.cases[0]
    batch_duration = selected.job["taskGroups"][0]["taskSpec"]["maxRunDuration"]
    assert batch_duration == "30605s"
    assert selected.controller_timeout_s == 34_205


def test_freeze_accepts_only_byte_identical_existing_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(campaign_cli, "_run", lambda *_args, **_kwargs: completed())
    campaign_cli._freeze("gs://bucket/new", b"new\n")
    answers = iter([completed(1, stderr=b"412 Precondition Failed"), completed(stdout=b"same\n")])
    monkeypatch.setattr(campaign_cli, "_run", lambda *_args, **_kwargs: next(answers))
    campaign_cli._freeze("gs://bucket/object", b"same\n")
    answers = iter([completed(1, stderr=b"already exists"), completed(stdout=b"different\n")])
    with pytest.raises(campaign_cli.SubmissionError, match="different content"):
        campaign_cli._freeze("gs://bucket/object", b"same\n")


def test_owner_create_collision_uses_bounded_exact_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = campaign_cli.TemporalOwner(
        campaign="2026-08-10-first",
        campaign_digest="d" * 64,
        scope=campaign_cli.TemporalScope("temporal.example.invalid:7233", "s3-study"),
        workflow_id="2026-08-10-first",
        run_id="run-a",
    )
    content = campaign_cli._canonical_json(selected.document())
    calls: list[tuple[Any, ...]] = []
    answers = iter([completed(1, stderr=b"412 Precondition Failed"), completed(stdout=content)])

    def run(argv: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(tuple(argv))
        return next(answers)

    monkeypatch.setattr(campaign_cli, "_run", run)
    campaign_cli._freeze_owner("gs://bucket/temporal-owner.json", selected)
    assert calls[1][-1] == f"--range=0-{campaign_cli.TEMPORAL_OWNER_MAX_BYTES}"

    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: completed(
            stdout=b"x" * (campaign_cli.TEMPORAL_OWNER_MAX_BYTES + 1)
        ),
    )
    with pytest.raises(campaign_cli.SubmissionError, match="exceeds"):
        campaign_cli._read_optional_owner("gs://bucket/temporal-owner.json")


def test_optional_owner_recognizes_real_gcloud_missing_object_error_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_missing = b"ERROR: (gcloud.storage.cat) The following URLs matched no objects or files:\n"
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: completed(1, stderr=real_missing),
    )
    assert campaign_cli._read_optional_owner("gs://bucket/temporal-owner.json") is None

    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: completed(1, stderr=b"permission denied"),
    )
    with pytest.raises(campaign_cli.SubmissionError, match="permission denied"):
        campaign_cli._read_optional_owner("gs://bucket/temporal-owner.json")


def test_submit_freezes_canonical_inputs_before_temporal_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, image_set = write_inputs(tmp_path)
    calls: list[tuple[str, bytes | None]] = []
    started: dict[str, Any] = {}

    def run(argv: Any, *, payload: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        if tuple(argv[:3]) == ("gcloud", "storage", "cat"):
            assert str(argv[3]).endswith("/inputs/temporal-owner.json")
            return completed(1, stderr=b"not found")
        calls.append((str(argv[4]), payload))
        return completed()

    async def start(**kwargs: Any) -> str:
        started.update(kwargs)
        return str(kwargs["campaign"])

    monkeypatch.setattr(campaign_cli, "_run", run)
    monkeypatch.setattr(campaign_cli, "_start_workflow", start)
    assert campaign_cli.submit_campaign_main(arguments(plan, image_set)) == 0
    assert [uri for uri, _payload in calls] == [
        "gs://study-results/campaigns/2026-08-10-first/inputs/plans/example-bucket.yaml",
        "gs://study-results/campaigns/2026-08-10-first/campaign.json",
        "gs://study-results/campaigns/2026-08-10-first/inputs/temporal.json",
    ]
    assert calls[0][1] == plan.read_bytes()
    manifest_bytes = calls[1][1]
    temporal_bytes = calls[2][1]
    assert manifest_bytes is not None
    assert temporal_bytes is not None
    temporal_document = json.loads(temporal_bytes)
    assert (
        temporal_document["campaign_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    )
    assert started["campaign_digest"] == hashlib.sha256(temporal_bytes).hexdigest()
    assert temporal_document["temporal_scope"] == {
        "target_host": SAFE_TEMPORAL_CONFIG["target_host"],
        "namespace": SAFE_TEMPORAL_CONFIG["namespace"],
    }
    assert SAFE_TEMPORAL_CONFIG["api_key"].encode() not in temporal_bytes
    assert started["client_config"]["api_key"] == SAFE_TEMPORAL_CONFIG["api_key"]
    request = started["request"]
    commands = request.cases[0].job["taskGroups"][0]["taskSpec"]["runnables"][0]["container"][
        "commands"
    ]
    destination = commands[commands.index("--destination") + 1]
    assert destination.startswith("gs://study-results/campaigns/2026-08-10-first/")
    assert "/temporal/" not in destination
    output = capsys.readouterr().out
    assert SAFE_TEMPORAL_CONFIG["api_key"] not in output
    assert json.loads(output) == {
        "campaign": "2026-08-10-first",
        "workflow_id": "2026-08-10-first",
    }


def test_different_temporal_scope_changes_frozen_input_bytes(tmp_path: Path) -> None:
    plan, image_set = write_inputs(tmp_path)
    args = campaign_cli.build_parser().parse_args(arguments(plan, image_set))
    first = campaign_cli._prepare(
        args, campaign_cli.TemporalScope("first.example.invalid:7233", "study-a")
    )[2]
    second = campaign_cli._prepare(
        args, campaign_cli.TemporalScope("second.example.invalid:7233", "study-b")
    )[2]
    assert first != second


def test_submit_wait_hands_the_owned_campaign_to_stateless_reporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, image_set = write_inputs(tmp_path)

    def run(argv: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if tuple(argv[:3]) == ("gcloud", "storage", "cat"):
            return completed(1, stderr=b"not found")
        return completed()

    async def start(**kwargs: Any) -> str:
        return str(kwargs["campaign"])

    reported: list[str] = []

    def report(argv: Any) -> int:
        reported.extend(argv)
        return 7

    monkeypatch.setattr(campaign_cli, "_run", run)
    monkeypatch.setattr(campaign_cli, "_start_workflow", start)
    monkeypatch.setattr(campaign_report, "report_campaign_main", report)
    assert (
        campaign_cli.submit_campaign_main(
            [
                *arguments(plan, image_set),
                "--wait",
                "--poll-interval-s",
                "0.25",
                "--publish-report",
            ]
        )
        == 7
    )
    assert reported == [
        "--campaign",
        "2026-08-10-first",
        "--results-bucket",
        "study-results",
        "--wait",
        "--poll-interval-s",
        "0.25",
        "--publish",
    ]


def test_oversized_valid_workflow_input_stops_before_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, image_set = write_inputs(tmp_path)
    args = campaign_cli.build_parser().parse_args(arguments(plan, image_set))
    prepared = campaign_cli._prepare(
        args, campaign_cli.TemporalScope("temporal.example.invalid:7233", "s3-study")
    )
    request, manifest_bytes, temporal_bytes, plan_inputs, dry_run = prepared
    selected = request.cases[0]
    cases = tuple(replace(selected, job_id=f"campaign-case-{index:05d}") for index in range(3500))
    oversized = replace(request, cases=cases)
    assert asyncio.run(campaign_cli._workflow_input_size(oversized)) > (
        campaign_cli.TEMPORAL_WORKFLOW_INPUT_MAX_BYTES
    )
    monkeypatch.setattr(
        campaign_cli,
        "_prepare",
        lambda *_args, **_kwargs: (
            oversized,
            manifest_bytes,
            temporal_bytes,
            plan_inputs,
            dry_run,
        ),
    )
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("oversized input reached cloud"),
    )

    async def no_start(**_kwargs: Any) -> str:
        pytest.fail("oversized input reached Temporal")

    monkeypatch.setattr(campaign_cli, "_start_workflow", no_start)
    assert campaign_cli.submit_campaign_main(arguments(plan, image_set)) == 1
    assert "split the campaign" in capsys.readouterr().err


def test_render_failure_precedes_every_cloud_and_temporal_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, image_set = write_inputs(tmp_path)
    monkeypatch.setattr(
        campaign_cli,
        "render_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CampaignError("render failed")),
    )
    monkeypatch.setattr(
        campaign_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("render failure reached a cloud command"),
    )

    async def no_start(**_kwargs: Any) -> str:
        pytest.fail("render failure reached Temporal")

    monkeypatch.setattr(campaign_cli, "_start_workflow", no_start)
    assert campaign_cli.submit_campaign_main(arguments(plan, image_set)) == 1
    assert "render failed" in capsys.readouterr().err


def test_worker_entry_point_remains_but_standalone_starter_is_removed() -> None:
    project = ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8")
    assert "s3-listing-study-temporal-worker" in project
    assert "s3-listing-study-temporal-start" not in project
