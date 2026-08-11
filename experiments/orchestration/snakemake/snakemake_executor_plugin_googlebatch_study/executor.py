"""Minimal Google Batch adaptation for immutable study execution images."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from google.api_core.exceptions import DeadlineExceeded
from google.cloud import batch_v1
from snakemake_executor_plugin_googlebatch.executor import GoogleBatchExecutor
from snakemake_interface_common.exceptions import WorkflowError
from snakemake_interface_executor_plugins.executors.base import SubmittedJobInfo
from snakemake_interface_executor_plugins.utils import format_cli_arg, join_cli_args

PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:[0-9a-f]{64}\Z")
WORKDIR = "/tmp/workdir"
RUNTIME_PATH = f"{WORKDIR}/runtime"
UNSET = "__unset__"
ADAPTER_VERSION = "0.1.0"
ADAPTER_SOURCE_FILES = ("__init__.py", "executor.py")


def adapter_source_sha256() -> str:
    """Hash the named adapter sources with unambiguous path/length framing."""
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for name in ADAPTER_SOURCE_FILES:
        content = (root / name).read_bytes()
        encoded_name = name.encode()
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def installed_executor_identity() -> dict[str, str]:
    try:
        adapter_distribution_version = version("s3-listing-snakemake-trial")
        snakemake_version = version("snakemake")
        upstream_version = version("snakemake-executor-plugin-googlebatch")
    except PackageNotFoundError as exc:
        raise ValueError(f"required executor distribution is not installed: {exc.name}") from None
    if adapter_distribution_version != ADAPTER_VERSION:
        raise ValueError(
            "installed adapter distribution version disagrees with adapter source: "
            f"{adapter_distribution_version} != {ADAPTER_VERSION}"
        )
    return {
        "name": "snakemake-executor-plugin-googlebatch-study",
        "adapter_version": ADAPTER_VERSION,
        "upstream_plugin_version": upstream_version,
        "snakemake_version": snakemake_version,
        "adapter_source_sha256": adapter_source_sha256(),
    }


def validate_installed_executor_identity(expected: Mapping[str, Any]) -> None:
    actual = installed_executor_identity()
    comparable = {key: expected.get(key) for key in actual}
    if comparable != actual:
        mismatches = [key for key in actual if comparable[key] != actual[key]]
        raise ValueError(
            "frozen executor identity does not match installed code/environment: "
            + ", ".join(mismatches)
        )


def _optional(value: Any) -> Any:
    return None if value in (None, UNSET) else value


def _require_pinned_image(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or PINNED_IMAGE_RE.fullmatch(value) is None:
        raise WorkflowError(f"{label} must be an image URI pinned with @sha256:<64 hex>")
    return value


def _container_options(cgroup_options: str | None) -> str:
    parts = ["--network=host", f"--workdir={WORKDIR}"]
    if cgroup_options:
        parts.extend(shlex.split(cgroup_options))
    return shlex.join(parts)


def build_create_job_request(
    projection: Mapping[str, Any],
    *,
    project: str,
    location: str,
    runtime_image: str,
    nested_command: str,
    actual_job_id: str,
) -> batch_v1.CreateJobRequest:
    """Build the provider request without I/O, retaining planned/actual IDs."""
    subject_image = _require_pinned_image(projection["image_uri"], label="subject image")
    runtime_image = _require_pinned_image(runtime_image, label="runtime helper image")
    if not nested_command or "pip install" in nested_command:
        raise WorkflowError("nested Snakemake command must be non-empty and install nothing")

    stage = batch_v1.Runnable()
    stage.container = batch_v1.Runnable.Container(
        image_uri=runtime_image,
        entrypoint="/usr/local/bin/stage-runtime",
        volumes=[f"{WORKDIR}:{WORKDIR}"],
        options="--user=0:0",
    )

    run = batch_v1.Runnable()
    run.container = batch_v1.Runnable.Container(
        image_uri=subject_image,
        entrypoint="/bin/sh",
        commands=["-ceu", nested_command],
        volumes=[f"{WORKDIR}:{WORKDIR}"],
        options=_container_options(_optional(projection.get("container_options"))),
    )
    run.environment = batch_v1.Environment(
        variables={
            "HOME": f"{WORKDIR}/home",
            "PYTHONPATH": RUNTIME_PATH,
            "PYTHONUNBUFFERED": "1",
        }
    )
    secret = _optional(projection.get("secret_resource"))
    if secret is not None:
        run.environment.secret_variables = {"S3_STUDY_AWS_CREDENTIAL": secret}

    task = batch_v1.TaskSpec(
        runnables=[stage, run],
        compute_resource=batch_v1.ComputeResource(
            cpu_milli=int(projection["cpu_milli"]),
            memory_mib=int(projection["memory_mib"]),
        ),
        max_retry_count=int(projection["retry_count"]),
        max_run_duration=str(projection["max_run_duration"]),
    )
    group = batch_v1.TaskGroup(
        task_count=1,
        parallelism=1,
        task_count_per_node=1,
        task_spec=task,
    )

    disk_spec = _optional(projection.get("boot_disk"))
    disk = None
    if disk_spec is not None:
        disk = batch_v1.AllocationPolicy.Disk(
            type_=disk_spec["type"],
            image=disk_spec["image"],
        )
        if disk_spec.get("size_gb") is not None:
            disk.size_gb = int(disk_spec["size_gb"])
    policy = batch_v1.AllocationPolicy.InstancePolicy(
        machine_type=str(projection["machine_type"]),
        provisioning_model=getattr(
            batch_v1.AllocationPolicy.ProvisioningModel,
            str(projection["provisioning"]),
        ),
    )
    if disk is not None:
        policy.boot_disk = disk
    allocation = batch_v1.AllocationPolicy(
        instances=[batch_v1.AllocationPolicy.InstancePolicyOrTemplate(policy=policy)],
        service_account=batch_v1.ServiceAccount(email=str(projection["service_account"])),
    )
    zone = _optional(projection.get("zone"))
    if zone is not None:
        allocation.location = batch_v1.AllocationPolicy.LocationPolicy(
            allowed_locations=[f"zones/{str(zone).removeprefix('zones/')}"]
        )
    network = _optional(projection.get("network"))
    subnetwork = _optional(projection.get("subnetwork"))
    if network is not None or subnetwork is not None:
        if network is None or subnetwork is None:
            raise WorkflowError("network and subnetwork must be provided together")
        allocation.network = batch_v1.AllocationPolicy.NetworkPolicy(
            network_interfaces=[
                batch_v1.AllocationPolicy.NetworkInterface(
                    network=str(network), subnetwork=str(subnetwork)
                )
            ]
        )

    planned_job_id = str(projection["job_id"])
    batch_job = batch_v1.Job(
        task_groups=[group],
        allocation_policy=allocation,
        labels={"planned-job-id": planned_job_id},
        logs_policy=batch_v1.LogsPolicy(destination=batch_v1.LogsPolicy.Destination.CLOUD_LOGGING),
    )
    return batch_v1.CreateJobRequest(
        parent=f"projects/{project}/locations/{location}",
        job_id=actual_job_id,
        job=batch_job,
    )


class StudyGoogleBatchExecutor(GoogleBatchExecutor):
    """Upstream executor with study allocation and runtime staging fixes."""

    def get_param(self, job: Any, param: str) -> Any:
        """Honor explicit falsey resources such as max_retry_count=0."""
        missing = object()
        value = job.resources.get(f"googlebatch_{param}", missing)
        if value is not missing:
            return value
        return getattr(self.executor_settings, param, None)

    def get_python_executable(self) -> str:
        return "/usr/bin/python3"

    def format_job_exec(self, job: Any) -> str:
        """Use staged Python for source deployment and the nested job."""
        prefix = self.get_job_exec_prefix(job)
        if prefix:
            prefix += " &&"
        suffix = self.get_job_exec_suffix(job)
        if suffix:
            suffix = f"&& {suffix}"
        factory = self.workflow.spawned_job_args_factory
        precommand = factory.precommand(
            executor_common_settings=self.common_settings,
            python_executable=self.get_python_executable(),
        )
        if precommand:
            precommand += " &&"
        args = join_cli_args(
            [
                prefix,
                self.get_envvar_declarations(),
                precommand,
                self.get_python_executable(),
                "-m snakemake",
                format_cli_arg("--snakefile", self.get_snakefile()),
                self.get_job_args(job),
                factory.general_args(executor_common_settings=self.common_settings),
                self.additional_general_args(),
                format_cli_arg("--mode", self.get_exec_mode().item_to_choice()),
                format_cli_arg(
                    "--local-groupid",
                    self.workflow.group_settings.local_groupid,
                    skip=self.job_specific_local_groupid,
                ),
                suffix,
            ]
        )
        if "pip install" in args:
            raise WorkflowError("runtime package installation is forbidden")
        return args

    def _projection(self, job: Any) -> dict[str, Any]:
        boot_type = _optional(self.get_param(job, "boot_disk_type"))
        boot_image = _optional(self.get_param(job, "boot_disk_image"))
        boot_size = _optional(self.get_param(job, "boot_disk_gb"))
        boot_disk = None
        if boot_type is not None or boot_image is not None or boot_size is not None:
            if boot_type is None or boot_image is None:
                raise WorkflowError("boot disk type and image must be provided together")
            boot_disk = {"type": boot_type, "image": boot_image}
            if boot_size is not None:
                boot_disk["size_gb"] = boot_size
        return {
            "job_id": self.get_param(job, "planned_job_id"),
            "image_uri": self.get_param(job, "container"),
            "machine_type": self.get_param(job, "machine_type"),
            "cpu_milli": self.get_param(job, "cpu_milli"),
            "memory_mib": self.get_param(job, "memory"),
            "container_options": _optional(self.get_param(job, "container_options")),
            "boot_disk": boot_disk,
            "retry_count": self.get_param(job, "retry_count"),
            "max_run_duration": self.get_param(job, "max_run_duration"),
            "provisioning": self.get_param(job, "provisioning"),
            "zone": _optional(self.get_param(job, "zone")),
            "network": _optional(self.get_param(job, "network")),
            "subnetwork": _optional(self.get_param(job, "subnetwork")),
            "service_account": self.get_param(job, "service_account"),
            "secret_resource": _optional(self.get_param(job, "secret_resource")),
        }

    def _storage_gcs_project(self) -> Any:
        providers = self.workflow.storage_provider_settings or {}
        tagged = providers.get("gcs")
        if tagged is None:
            return None
        settings = tagged.get_settings()
        return None if settings is None else settings.project

    def _validate_frozen_submission(self, job: Any) -> dict[str, Any]:
        try:
            frozen = json.loads(job.params["frozen_provider_projection"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"missing or invalid frozen provider projection: {exc}") from None
        try:
            validate_installed_executor_identity(frozen["executor_identity"])
        except (KeyError, ValueError) as exc:
            raise WorkflowError(str(exc)) from None
        storage = self.workflow.storage_settings
        actual = {
            "batch": self._projection(job),
            "threads": job.threads,
            "project": self.get_param(job, "project"),
            "location": self.get_param(job, "region"),
            "runtime_image": self.get_param(job, "runtime_image"),
            "default_storage_provider": storage.default_storage_provider,
            "default_storage_prefix": storage.default_storage_prefix,
            "storage_gcs_project": self._storage_gcs_project(),
            "executor_identity": installed_executor_identity(),
        }
        if actual != frozen:
            mismatches = sorted(key for key in frozen if actual.get(key) != frozen[key])
            raise WorkflowError(
                "effective Snakemake launch differs from frozen provider projection: "
                + ", ".join(mismatches)
            )
        return actual

    def run_job(self, job: Any) -> None:
        frozen = self._validate_frozen_submission(job)
        logfile = job.logfile_suggestion(".snakemake/googlebatch_logs")
        os.makedirs(os.path.dirname(logfile), exist_ok=True)
        actual_job_id = self.generate_jobid(job)
        request = build_create_job_request(
            frozen["batch"],
            project=frozen["project"],
            location=frozen["location"],
            runtime_image=frozen["runtime_image"],
            nested_command=self.format_job_exec(job),
            actual_job_id=actual_job_id,
        )
        created_job = self.batch.create_job(request=request)
        aux = {
            "batch_job": created_job,
            "last_seen": None,
            "logfile": logfile,
            "planned_job_id": self.get_param(job, "planned_job_id"),
            "provider_job_id": actual_job_id,
        }
        self.report_job_submission(SubmittedJobInfo(job, external_jobid=created_job.name, aux=aux))

    async def check_active_jobs(self, active_jobs: list[SubmittedJobInfo]):
        """Keep transient polling timeouts active instead of misclassifying them."""
        for submitted in active_jobs:
            job_id = submitted.external_jobid
            request = batch_v1.GetJobRequest(name=job_id)
            aux_logs = [submitted.aux["logfile"]]
            last_seen = submitted.aux["last_seen"]
            try:
                response = self.batch.get_job(request=request)
            except DeadlineExceeded:
                self.logger.warning(
                    f"Google Batch status poll for '{job_id}' exceeded its deadline; "
                    "the job remains active and will be checked again"
                )
                yield submitted
                continue

            self.logger.info(f"Job {job_id} has state {response.status.state.name}")
            for event in response.status.status_events:
                if not last_seen or event.event_time.nanosecond > last_seen:
                    self.logger.info(f"{event.type_}: {event.description}")
                    last_seen = event.event_time.nanosecond
            submitted.aux["last_seen"] = last_seen
            state = response.status.state.name
            if state in ("FAILED", "SUCCEEDED"):
                self.save_finished_job_logs(submitted)
            if state == "FAILED":
                self.report_job_error(
                    submitted,
                    msg=f"Google Batch job '{job_id}' failed. ",
                    aux_logs=aux_logs,
                )
            elif state == "SUCCEEDED":
                self.report_job_success(submitted)
            else:
                yield submitted
