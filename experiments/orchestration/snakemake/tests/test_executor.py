"""Provider-request tests for the study's Snakemake Google Batch adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import DeadlineExceeded
from google.cloud import batch_v1
from google.protobuf.timestamp_pb2 import Timestamp

ROOT = Path(__file__).resolve().parents[4]
EXPERIMENT = ROOT / "experiments" / "orchestration" / "snakemake"
sys.path.insert(0, str(EXPERIMENT))

from scripts.workflow import (  # noqa: E402
    WorkflowInputError,
    canonical_provider_projection,
    executor_runtime_image,
    freeze_execution_profile,
    load_campaign,
    load_execution_profile,
    marker_path,
    project_attempt,
)
from snakemake_executor_plugin_googlebatch_study import (  # noqa: E402
    RUNTIME_PATH,
    adapter_source_sha256,
    build_create_job_request,
    installed_executor_identity,
)
from snakemake_executor_plugin_googlebatch_study import (  # noqa: E402
    Executor as StudyGoogleBatchExecutor,
)

from s3_listing_study.manager.bench import plan as bench  # noqa: E402
from s3_listing_study.manager.campaign import compile_campaign  # noqa: E402

RUNTIME_IMAGE = "us-east1-docker.pkg.dev/study/runtime/snakemake@sha256:" + "f" * 64
SECRET = "projects/study1/secrets/aws/versions/7"


def _images(plan: bench.Plan) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for index, tool in enumerate(plan.tools()):
        registration = json.loads(
            (ROOT / "tools" / tool / "build" / "image.json").read_text(encoding="utf-8")
        )
        digit = format(index, "x")
        derived = "sha256:" + digit * 64
        tool_digest = "sha256:" + format(index + 1, "x") * 64
        result[tool] = {
            "derived_image": derived,
            "image_uri": f"us-east1-docker.pkg.dev/study/images/{tool}@{derived}",
            "shared_base_digest": "sha256:" + "a" * 64,
            "shared_base_uri": "registry.example/base@sha256:" + "a" * 64,
            "shared_base_source_sha256": registration["shared_base_source_sha256"],
            "tool_build_sha256": registration["tool_build_sha256"],
            "tool_artifact": registration["tool_artifact"],
            "tool_version": registration["tool_version"],
            "adapter_bundle_sha256": registration["adapter_bundle_sha256"],
            "harness_revision": "b" * 40,
            "tool_image_digest": tool_digest,
            "tool_image_uri": f"registry.example/{tool}@{tool_digest}",
            "selection_sha256": format(index + 1, "x") * 64,
        }
    return result


def _profile() -> dict[str, object]:
    return {
        "schema_version": 2,
        "project": "study1",
        "location": "us-east1",
        "results_bucket": "study-results",
        "provisioning": "SPOT",
        "zone": "us-east1-b",
        "network": "projects/study1/global/networks/study",
        "subnetwork": "projects/study1/regions/us-east1/subnetworks/study",
        "orchestration_prefix": "snakemake/orchestration/",
        "evidence_prefix": "snakemake/evidence/",
        "anonymous_worker_service_account": "worker@study.iam.gserviceaccount.com",
        "authenticated_worker_service_account": "auth-worker@study.iam.gserviceaccount.com",
        "aws_credential_secret": SECRET,
        "output_path": "/tmp/s3-listing-study-attempt",
        "term_grace_s": 5,
        "post_attempt_allowance_s": 1800,
        "retry_count": 0,
        "n4_boot_disk": {"type": "hyperdisk-balanced", "image": "batch-cos"},
        "executor": {
            "name": "snakemake-executor-plugin-googlebatch-study",
            "adapter_version": "0.1.0",
            "upstream_plugin_version": "0.5.1",
            "snakemake_version": "9.25.1",
            "adapter_source_sha256": adapter_source_sha256(),
            "runtime_image": RUNTIME_IMAGE,
        },
    }


def _compiled(tmp_path: Path):
    plan = bench.Plan.load(bench.default_path("noaa-rtma-pds"))
    compiled = compile_campaign(
        campaign="2026-08-11-snake",
        plans=(plan,),
        images=_images(plan),
        results_bucket="study-results",
        provisioning="SPOT",
        zone="us-east1-b",
    )
    campaign_path = tmp_path / "campaign.json"
    profile_path = tmp_path / "profile.json"
    campaign_path.write_bytes(compiled.content)
    profile_path.write_text(json.dumps(_profile()), encoding="utf-8")
    return compiled, load_campaign(campaign_path), load_execution_profile(profile_path)


def test_all_17_real_attempts_build_exact_batch_requests(tmp_path: Path) -> None:
    compiled, campaign, profile = _compiled(tmp_path)

    assert len(compiled.attempts) == len(campaign["attempts"]) == 17
    for canonical_attempt, row in zip(compiled.attempts, campaign["attempts"], strict=True):
        projected = project_attempt(campaign, row, profile)
        request = build_create_job_request(
            projected,
            project=profile["project"],
            location=profile["location"],
            runtime_image=RUNTIME_IMAGE,
            nested_command="/usr/bin/python3 -m snakemake --version",
            actual_job_id="snakemake-operational-a1b2c3",
        )
        job = request.job
        task = job.task_groups[0].task_spec
        subject = task.runnables[1]
        policy = job.allocation_policy.instances[0].policy
        interface = job.allocation_policy.network.network_interfaces[0]

        assert projected["job_id"] == canonical_attempt.job_id == row["job_id"]
        assert subject.container.image_uri == projected["image_uri"]
        assert policy.machine_type == projected["machine_type"]
        assert task.compute_resource.cpu_milli == projected["cpu_milli"]
        assert task.compute_resource.memory_mib == projected["memory_mib"]
        assert task.max_retry_count == projected["retry_count"] == 0
        assert f"{int(task.max_run_duration.total_seconds())}s" == projected["max_run_duration"]
        assert policy.provisioning_model.name == projected["provisioning"]
        assert list(job.allocation_policy.location.allowed_locations) == [
            f"zones/{projected['zone']}"
        ]
        assert interface.network == projected["network"]
        assert interface.subnetwork == projected["subnetwork"]
        assert job.allocation_policy.service_account.email == projected["service_account"]
        if projected["boot_disk"] is None:
            assert not policy.boot_disk
        else:
            assert policy.boot_disk.type_ == projected["boot_disk"]["type"]
            assert policy.boot_disk.image == projected["boot_disk"]["image"]
            assert policy.boot_disk.size_gb == projected["boot_disk"].get("size_gb", 0)
        expected_options = projected["container_options"]
        if expected_options:
            for option in ("--memory=", "--memory-swap="):
                assert option in subject.container.options
        else:
            assert "--memory=" not in subject.container.options
            assert "--memory-swap=" not in subject.container.options
        expected_secret = projected["secret_resource"]
        assert subject.environment.secret_variables.get("S3_STUDY_AWS_CREDENTIAL") == (
            expected_secret or None
        )
        assert job.labels["planned-job-id"] == projected["job_id"]
        assert request.job_id != projected["job_id"]
        assert request.parent == "projects/study1/locations/us-east1"


def test_runtime_staging_and_nested_command_do_not_install_or_mask_exit() -> None:
    dockerfile = (EXPERIMENT / "runtime" / "Dockerfile").read_text(encoding="utf-8")
    readme = (EXPERIMENT / "README.md").read_text(encoding="utf-8")
    stage = (EXPERIMENT / "runtime" / "stage-runtime").read_text(encoding="utf-8")
    runtime_project = (EXPERIMENT / "runtime" / "pyproject.toml").read_text(encoding="utf-8")
    assert "@sha256:" in dockerfile
    assert "uv sync" in dockerfile and "--frozen" in dockerfile
    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert "COPY stage-runtime /usr/local/bin/stage-runtime" in dockerfile
    assert "COPY experiments/" not in dockerfile
    assert (
        "docker build \\\n"
        "  --provenance=false \\\n"
        "  --sbom=false \\\n"
        "  -f experiments/orchestration/snakemake/runtime/Dockerfile \\\n"
        "  -t us-east1-docker.pkg.dev/varve-oss/s3-listing-study/"
        "snakemake-runtime:2026-08-11 \\\n"
        "  experiments/orchestration/snakemake/runtime"
    ) in readme
    assert "pip install" not in dockerfile
    assert "pip install" not in stage
    assert "s3-listing-study" not in runtime_project
    assert "snakemake-executor-plugin-googlebatch" not in runtime_project

    projection = {
        "job_id": "planned-job",
        "image_uri": "registry.example/subject@sha256:" + "a" * 64,
        "machine_type": "n4-standard-2",
        "cpu_milli": 2000,
        "memory_mib": 4096,
        "container_options": "--memory=2g --memory-swap=2g",
        "boot_disk": {"type": "hyperdisk-balanced", "image": "batch-cos", "size_gb": 40},
        "retry_count": 0,
        "max_run_duration": "99s",
        "provisioning": "STANDARD",
        "zone": None,
        "network": None,
        "subnetwork": None,
        "service_account": "worker@study.iam.gserviceaccount.com",
        "secret_resource": None,
    }
    request = build_create_job_request(
        projection,
        project="study1",
        location="us-east1",
        runtime_image=RUNTIME_IMAGE,
        nested_command="exit 23",
        actual_job_id="actual-job-a1b2c3",
    )
    task = request.job.task_groups[0].task_spec
    assert task.max_retry_count == 0
    assert task.runnables[0].container.entrypoint == "/usr/local/bin/stage-runtime"
    assert task.runnables[1].environment.variables["PYTHONPATH"] == RUNTIME_PATH
    assert task.runnables[1].ignore_exit_status is False
    assert task.runnables[1].container.commands == ["-ceu", "exit 23"]
    assert request.job.allocation_policy.instances[0].policy.boot_disk.size_gb == 40
    assert subprocess.run(["/bin/sh", "-ceu", "exit 23"], check=False).returncode == 23


def test_falsey_job_resource_overrides_executor_default() -> None:
    executor = SimpleNamespace(executor_settings=SimpleNamespace(retry_count=1))
    job = SimpleNamespace(resources={"googlebatch_retry_count": 0})
    assert StudyGoogleBatchExecutor.get_param(executor, job, "retry_count") == 0


def test_runtime_helper_identity_comes_from_frozen_profile() -> None:
    profile = _profile()
    assert executor_runtime_image(profile) == RUNTIME_IMAGE
    assert profile["executor"] == {**installed_executor_identity(), "runtime_image": RUNTIME_IMAGE}


def test_runnable_profile_freeze_binds_installed_executor_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    destination = tmp_path / "execution-profile.json"
    source.write_text(json.dumps(_profile()), encoding="utf-8")
    assert freeze_execution_profile(source, destination)[0] is True

    tampered = _profile()
    tampered["executor"]["adapter_source_sha256"] = "0" * 64
    source.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(WorkflowInputError, match="adapter_source_sha256"):
        freeze_execution_profile(source, tmp_path / "tampered.json")


def _executor_validation_fixture(tmp_path: Path):
    _, campaign, profile = _compiled(tmp_path)
    row = campaign["attempts"][0]
    projected = project_attempt(campaign, row, profile)
    resources = {
        "googlebatch_planned_job_id": projected["job_id"],
        "googlebatch_container": projected["image_uri"],
        "googlebatch_machine_type": projected["machine_type"],
        "googlebatch_cpu_milli": projected["cpu_milli"],
        "googlebatch_memory": projected["memory_mib"],
        "googlebatch_container_options": projected["container_options"] or "__unset__",
        "googlebatch_boot_disk_type": (
            projected["boot_disk"]["type"] if projected["boot_disk"] else "__unset__"
        ),
        "googlebatch_boot_disk_image": (
            projected["boot_disk"]["image"] if projected["boot_disk"] else "__unset__"
        ),
        "googlebatch_retry_count": projected["retry_count"],
        "googlebatch_max_run_duration": projected["max_run_duration"],
        "googlebatch_provisioning": projected["provisioning"],
        "googlebatch_zone": projected["zone"] or "__unset__",
        "googlebatch_network": projected["network"] or "__unset__",
        "googlebatch_subnetwork": projected["subnetwork"] or "__unset__",
        "googlebatch_service_account": projected["service_account"],
        "googlebatch_secret_resource": projected["secret_resource"] or "__unset__",
        "googlebatch_runtime_image": RUNTIME_IMAGE,
    }
    job = SimpleNamespace(
        resources=resources,
        threads=projected["vcpus"],
        params={
            "frozen_provider_projection": canonical_provider_projection(campaign, row, profile)
        },
    )
    tagged = SimpleNamespace(get_settings=lambda: SimpleNamespace(project=profile["project"]))
    executor = object.__new__(StudyGoogleBatchExecutor)
    executor.executor_settings = SimpleNamespace(
        project=profile["project"], region=profile["location"]
    )
    executor.workflow = SimpleNamespace(
        storage_settings=SimpleNamespace(
            default_storage_provider="gcs",
            default_storage_prefix=(
                f"gcs://{profile['results_bucket']}/{profile['orchestration_prefix']}"
            ),
        ),
        storage_provider_settings={"gcs": tagged},
    )
    return executor, job


def test_effective_launch_matches_frozen_provider_projection(tmp_path: Path) -> None:
    executor, job = _executor_validation_fixture(tmp_path)
    actual = executor._validate_frozen_submission(job)
    assert actual["batch"]["retry_count"] == 0
    assert actual["threads"] == 2


@pytest.mark.parametrize(
    ("mutation", "mismatch"),
    (
        (lambda executor, job: job.resources.__setitem__("googlebatch_retry_count", 1), "batch"),
        (lambda executor, job: setattr(job, "threads", job.threads + 1), "threads"),
        (
            lambda executor, job: job.resources.__setitem__("googlebatch_project", "other1"),
            "project",
        ),
        (
            lambda executor, job: job.resources.__setitem__("googlebatch_region", "us-west1"),
            "location",
        ),
        (
            lambda executor, job: job.resources.__setitem__(
                "googlebatch_runtime_image",
                "us-east1-docker.pkg.dev/study/runtime/other@sha256:" + "e" * 64,
            ),
            "runtime_image",
        ),
        (
            lambda executor, job: setattr(
                executor.workflow.storage_settings,
                "default_storage_prefix",
                "gcs://other-bucket/snakemake/orchestration/",
            ),
            "default_storage_prefix",
        ),
        (
            lambda executor, job: executor.workflow.storage_provider_settings.__setitem__(
                "gcs",
                SimpleNamespace(get_settings=lambda: SimpleNamespace(project="other1")),
            ),
            "storage_gcs_project",
        ),
    ),
)
def test_effective_launch_refuses_generic_overrides(
    tmp_path: Path, mutation, mismatch: str
) -> None:
    executor, job = _executor_validation_fixture(tmp_path)
    mutation(executor, job)
    with pytest.raises(Exception, match=mismatch):
        executor._validate_frozen_submission(job)


def _poll_executor(response_or_error):
    executor = object.__new__(StudyGoogleBatchExecutor)

    def get_job(*, request):
        if isinstance(response_or_error, Exception):
            raise response_or_error
        return response_or_error

    events: list[str] = []
    executor.batch = SimpleNamespace(get_job=get_job)
    executor.logger = SimpleNamespace(
        info=lambda message: events.append(f"info:{message}"),
        warning=lambda message: events.append(f"warning:{message}"),
    )
    executor.save_finished_job_logs = lambda job: events.append("saved")
    executor.report_job_error = lambda job, **kwargs: events.append("failed")
    executor.report_job_success = lambda job: events.append("succeeded")
    return executor, events


def _collect_poll(executor, submitted):
    async def collect():
        return [job async for job in executor.check_active_jobs([submitted])]

    return asyncio.run(collect())


def test_poll_deadline_keeps_job_active_without_terminal_report() -> None:
    executor, events = _poll_executor(DeadlineExceeded("transient"))
    submitted = SimpleNamespace(
        external_jobid="projects/study1/locations/us-east1/jobs/job1",
        aux={"logfile": "job.log", "last_seen": None},
    )
    assert _collect_poll(executor, submitted) == [submitted]
    assert events == [
        "warning:Google Batch status poll for "
        "'projects/study1/locations/us-east1/jobs/job1' exceeded its deadline; "
        "the job remains active and will be checked again"
    ]


@pytest.mark.parametrize(
    ("state", "yielded", "terminal"),
    (("RUNNING", True, None), ("SUCCEEDED", False, "succeeded"), ("FAILED", False, "failed")),
)
def test_poll_preserves_normal_states(state: str, yielded: bool, terminal: str | None) -> None:
    response = SimpleNamespace(
        status=SimpleNamespace(state=SimpleNamespace(name=state), status_events=[])
    )
    executor, events = _poll_executor(response)
    submitted = SimpleNamespace(
        external_jobid="projects/study1/locations/us-east1/jobs/job1",
        aux={"logfile": "job.log", "last_seen": None},
    )
    assert bool(_collect_poll(executor, submitted)) is yielded
    if terminal is None:
        assert "saved" not in events
    else:
        assert events[-2:] == ["saved", terminal]


def test_poll_logs_later_second_event_with_smaller_nanosecond_remainder() -> None:
    first_event = batch_v1.StatusEvent(
        type_="RUNNING",
        description="first",
        event_time=Timestamp(seconds=10, nanos=900),
    )
    second_event = batch_v1.StatusEvent(
        type_="UPDATED",
        description="second",
        event_time=Timestamp(seconds=11, nanos=100),
    )
    first_response = SimpleNamespace(
        status=SimpleNamespace(
            state=SimpleNamespace(name="RUNNING"), status_events=[first_event]
        )
    )
    executor, events = _poll_executor(first_response)
    submitted = SimpleNamespace(
        external_jobid="projects/study1/locations/us-east1/jobs/job1",
        aux={"logfile": "job.log", "last_seen": None},
    )

    assert _collect_poll(executor, submitted) == [submitted]
    submitted.aux["last_seen"] = list(submitted.aux["last_seen"])
    executor.batch.get_job = lambda *, request: SimpleNamespace(
        status=SimpleNamespace(
            state=SimpleNamespace(name="RUNNING"), status_events=[first_event, second_event]
        )
    )
    assert _collect_poll(executor, submitted) == [submitted]

    assert submitted.aux["last_seen"] == (11, 100)
    assert events.count("info:RUNNING: first") == 1
    assert events.count("info:UPDATED: second") == 1


def test_remote_source_layout_is_narrow_and_host_independent() -> None:
    snakefile = (EXPERIMENT / "Snakefile").read_text(encoding="utf-8")
    workflow_support = (EXPERIMENT / "scripts" / "workflow.py").read_text(encoding="utf-8")
    assert 'sys.path.insert(0, "src")' in snakefile
    assert 'sys.path.insert(0, "experiments/orchestration/snakemake/scripts")' in snakefile
    assert "from workflow import" in snakefile
    assert 'script:\n        "scripts/run_attempt.py"' in snakefile
    assert 'worker_argv=lambda wildcards: projection(wildcards)["worker_argv"]' in snakefile
    assert 'MARKER_ROOT = "markers"' in workflow_support
    assert "marker_root" not in snakefile
    assert sorted(
        path.name for path in (EXPERIMENT / "scripts").iterdir() if path.suffix == ".py"
    ) == ["run_attempt.py", "workflow.py"]


def test_plugin_is_discoverable_by_snakemake_registry() -> None:
    from snakemake_interface_executor_plugins.registry import ExecutorPluginRegistry

    registry = ExecutorPluginRegistry()
    assert registry.is_installed("googlebatch-study")
    assert registry.get_plugin("googlebatch-study").common_settings.job_deploy_sources is True
    assert (
        registry.get_plugin(
            "googlebatch-study"
        ).common_settings.auto_deploy_default_storage_provider
        is False
    )


def _checked_in_profile_dry_run(
    tmp_path: Path,
    *extra_args: str,
    targets: tuple[str, ...] | None = None,
    target_count: int = 1,
    dry_run: bool = True,
    nested: bool = False,
    nested_split_paths: bool = False,
) -> subprocess.CompletedProcess[str]:
    compiled, campaign, _ = _compiled(tmp_path)
    run_dir = ROOT / ".snakemake" / "runs" / f"executor-test-{uuid.uuid4().hex}"
    split_run_dir = None
    run_dir.mkdir(parents=True)
    try:
        campaign_path = run_dir / "campaign.json"
        profile_path = run_dir / "execution-profile.json"
        campaign_path.write_bytes(compiled.content)
        profile_path.write_text(json.dumps(_profile()), encoding="utf-8")
        if targets is None:
            targets = tuple(
                marker_path(
                    campaign=campaign["campaign"],
                    campaign_sha256=hashlib.sha256(campaign_path.read_bytes()).hexdigest(),
                    execution_sha256=hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                    attempt=row,
                )
                for row in campaign["attempts"][:target_count]
            )
        if nested:
            row = campaign["attempts"][0]
            nested_profile_path = profile_path
            if nested_split_paths:
                split_run_dir = run_dir.parent / f"{run_dir.name}-split"
                split_run_dir.mkdir()
                nested_profile_path = split_run_dir / profile_path.name
                nested_profile_path.write_bytes(profile_path.read_bytes())
            target_job = "attempt:" + ",".join(
                f"{key}={row[key]}"
                for key in ("bucket", "tool", "case_id", "run_ordinal")
            )
            targets = ()
            dry_run = False
            extra_args = (
                "--mode",
                "subprocess",
                "--target-jobs",
                target_job,
                "--executor",
                "local",
                "--cores",
                "1",
                "--config",
                f"campaign_path={campaign_path.relative_to(ROOT)}",
                f"execution_profile_path={nested_profile_path.relative_to(ROOT)}",
                f"campaign_sha256={hashlib.sha256(campaign_path.read_bytes()).hexdigest()}",
                f"execution_sha256={hashlib.sha256(profile_path.read_bytes()).hexdigest()}",
                *extra_args,
            )
        command = [
            sys.executable,
            "-c",
            (
                "from google.cloud.storage import Bucket; "
                "Bucket.exists=lambda self, *args, **kwargs: False; "
                "from snakemake.cli import main; main()"
            ),
            "--snakefile",
            str(EXPERIMENT / "Snakefile"),
            *targets,
            "--profile",
            str(EXPERIMENT / "profiles" / "googlebatch"),
            "--quiet",
            *extra_args,
        ]
        if dry_run:
            command.append("--dry-run")
        environment = os.environ.copy()
        if nested:
            environment.pop("S3_STUDY_RUN_DIR", None)
        else:
            environment["S3_STUDY_RUN_DIR"] = run_dir.relative_to(ROOT).as_posix()
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        return completed
    finally:
        shutil.rmtree(run_dir)
        if split_run_dir is not None:
            shutil.rmtree(split_run_dir)


def test_checked_in_compute_profile_builds_dag_with_mocked_storage_inventory(
    tmp_path: Path,
) -> None:
    completed = _checked_in_profile_dry_run(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_checked_in_profile_rejects_omitted_target(tmp_path: Path) -> None:
    completed = _checked_in_profile_dry_run(tmp_path, targets=())
    assert completed.returncode != 0
    assert "requires exactly one frozen campaign marker target" in (
        completed.stdout + completed.stderr
    )


@pytest.mark.parametrize("target", ("all", "markers/not-frozen.json"))
def test_checked_in_profile_rejects_rule_or_unknown_target(
    tmp_path: Path, target: str
) -> None:
    completed = _checked_in_profile_dry_run(tmp_path, targets=(target,))
    assert completed.returncode != 0
    if target == "all":
        assert "requires exactly one frozen campaign marker target" in (
            completed.stdout + completed.stderr
        )


def test_checked_in_profile_rejects_multiple_targets(tmp_path: Path) -> None:
    first = _checked_in_profile_dry_run(tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    completed = _checked_in_profile_dry_run(tmp_path, target_count=2)
    assert completed.returncode != 0
    assert "requires exactly one frozen campaign marker target" in (
        completed.stdout + completed.stderr
    )


@pytest.mark.parametrize("executor", ("local", "dryrun"))
def test_real_launch_rejects_nonstudy_executor(tmp_path: Path, executor: str) -> None:
    completed = _checked_in_profile_dry_run(
        tmp_path, "--executor", executor, dry_run=False
    )
    assert completed.returncode != 0
    assert "frozen launch requires the googlebatch-study executor" in (
        completed.stdout + completed.stderr
    )


def test_nested_target_job_reaches_generated_script_without_future_syntax_error(
    tmp_path: Path,
) -> None:
    completed = _checked_in_profile_dry_run(tmp_path, nested=True)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "can't open file '/opt/s3-listing-study/attempt.pyz'" in output
    assert "from __future__ imports must occur at the beginning" not in output
    assert "nested Snakemake target job" not in output
    assert "nested attempt execution requires" not in output
    assert "S3_STUDY_RUN_DIR is required" not in output


def test_nested_target_job_rejects_frozen_paths_from_different_run_directories(
    tmp_path: Path,
) -> None:
    completed = _checked_in_profile_dry_run(
        tmp_path, nested=True, nested_split_paths=True
    )
    assert completed.returncode != 0
    assert "share one frozen run directory" in completed.stdout + completed.stderr


@pytest.mark.parametrize(
    "override",
    (
        ("--set-resources", "attempt:googlebatch_retry_count=1"),
        ("--set-resources", "attempt:googlebatch_project=other1"),
        ("--set-resources", "attempt:googlebatch_region=us-west1"),
        ("--set-resources", "attempt:googlebatch_runtime_image=other"),
        ("--set-threads", "attempt=4"),
    ),
)
def test_checked_in_profile_dry_run_rejects_generic_overrides(
    tmp_path: Path, override: tuple[str, str]
) -> None:
    completed = _checked_in_profile_dry_run(tmp_path, *override)
    assert completed.returncode != 0
    assert "cannot override a frozen Snakemake launch" in completed.stdout + completed.stderr


def test_checked_in_profile_rejects_config_path_override(tmp_path: Path) -> None:
    completed = _checked_in_profile_dry_run(
        tmp_path, "--config", "campaign_path=.snakemake/runs/other/campaign.json"
    )
    assert completed.returncode != 0
    assert "--config cannot override frozen campaign_path" in completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("setting", "expected"),
    (
        ("googlebatch-study-project", "study1"),
        ("default-storage-prefix", "gcs://study-results/snakemake/orchestration/"),
    ),
)
def test_checked_in_compute_profile_derives_settings_from_frozen_profile(
    monkeypatch: pytest.MonkeyPatch, setting: str, expected: str
) -> None:
    from snakemake.profiles import ProfileConfigFileParser

    run_dir = ROOT / ".snakemake" / "runs" / f"profile-test-{uuid.uuid4().hex}"
    relative_run_dir = run_dir.relative_to(ROOT)
    run_dir.mkdir(parents=True)
    try:
        campaign_path = run_dir / "campaign.json"
        execution_profile_path = run_dir / "execution-profile.json"
        campaign_path.write_bytes(b"frozen campaign\n")
        execution_profile_path.write_text(json.dumps(_profile()), encoding="utf-8")
        monkeypatch.chdir(ROOT)
        monkeypatch.setenv("S3_STUDY_RUN_DIR", relative_run_dir.as_posix())

        profile_path = EXPERIMENT / "profiles" / "googlebatch" / "profile.v9+.yaml"
        with profile_path.open(encoding="utf-8") as stream:
            rendered = ProfileConfigFileParser().parse(stream)

        assert rendered[setting] == expected
        assert rendered["config"] == [
            f"campaign_path={relative_run_dir / 'campaign.json'}",
            f"execution_profile_path={relative_run_dir / 'execution-profile.json'}",
            f"campaign_sha256={hashlib.sha256(campaign_path.read_bytes()).hexdigest()}",
            "execution_sha256="
            + hashlib.sha256(execution_profile_path.read_bytes()).hexdigest(),
        ]
    finally:
        shutil.rmtree(run_dir)


@pytest.mark.parametrize(
    "configured_run_dir",
    (
        "__absolute__",
        "../.snakemake/runs/trial",
        ".snakemake/runs/../trial",
        ".snakemake/runs/trial/extra",
        ".snakemake//runs/trial",
        ".snakemake/runs/./trial",
        ".snakemake/runs/trial/",
        ".snakemake/runs/trial\\nested",
    ),
)
def test_checked_in_compute_profile_rejects_noncanonical_run_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_run_dir: str,
) -> None:
    from configargparse import ConfigFileParserException
    from snakemake.profiles import ProfileConfigFileParser

    if configured_run_dir == "__absolute__":
        configured_run_dir = str(tmp_path)
    monkeypatch.setenv("S3_STUDY_RUN_DIR", configured_run_dir)
    profile_path = EXPERIMENT / "profiles" / "googlebatch" / "profile.v9+.yaml"
    with profile_path.open(encoding="utf-8") as stream, pytest.raises(
        ConfigFileParserException,
        match=r"S3_STUDY_RUN_DIR must be exactly \.snakemake/runs/<single-name>",
    ):
        ProfileConfigFileParser().parse(stream)


def test_checked_in_compute_profile_rejects_symlinked_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from configargparse import ConfigFileParserException
    from snakemake.profiles import ProfileConfigFileParser

    run_dir = ROOT / ".snakemake" / "runs" / f"profile-link-{uuid.uuid4().hex}"
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    run_dir.symlink_to(tmp_path, target_is_directory=True)
    try:
        monkeypatch.chdir(ROOT)
        monkeypatch.setenv("S3_STUDY_RUN_DIR", run_dir.relative_to(ROOT).as_posix())
        profile_path = EXPERIMENT / "profiles" / "googlebatch" / "profile.v9+.yaml"
        with profile_path.open(encoding="utf-8") as stream, pytest.raises(
            ConfigFileParserException,
            match="S3_STUDY_RUN_DIR and its parent directories must not be symlinks",
        ):
            ProfileConfigFileParser().parse(stream)
    finally:
        run_dir.unlink()
