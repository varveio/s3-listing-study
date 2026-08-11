"""Minimal Google Batch adaptation for immutable study execution images."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from typing import Any

from google.cloud import batch_v1
from snakemake_executor_plugin_googlebatch.executor import GoogleBatchExecutor
from snakemake_interface_common.exceptions import WorkflowError
from snakemake_interface_executor_plugins.executors.base import SubmittedJobInfo
from snakemake_interface_executor_plugins.utils import format_cli_arg, join_cli_args

PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:[0-9a-f]{64}\Z")
WORKDIR = "/tmp/workdir"
RUNTIME_PATH = f"{WORKDIR}/runtime"
UNSET = "__unset__"


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
            boot_disk = {"type": boot_type, "image": boot_image, "size_gb": boot_size}
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

    def run_job(self, job: Any) -> None:
        logfile = job.logfile_suggestion(".snakemake/googlebatch_logs")
        os.makedirs(os.path.dirname(logfile), exist_ok=True)
        actual_job_id = self.generate_jobid(job)
        request = build_create_job_request(
            self._projection(job),
            project=self.get_param(job, "project"),
            location=self.get_param(job, "region"),
            runtime_image=self.get_param(job, "runtime_image"),
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
