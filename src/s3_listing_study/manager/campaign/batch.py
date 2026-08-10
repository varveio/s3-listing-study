"""Pure rendering of one frozen campaign attempt as a GCP Batch v1 job."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any

from s3_listing_study.manager.campaign import DIGEST_RE, Attempt, CampaignError

CREDENTIAL_ENV_VAR = "S3_STUDY_AWS_CREDENTIAL"
DEFAULT_OUTPUT_PATH = "/tmp/s3-listing-study-attempt"
DEFAULT_TERM_GRACE_S = 5
DEFAULT_POST_ATTEMPT_ALLOWANCE_S = 1800
PROVISIONING_MODELS = ("STANDARD", "SPOT")
CASE_ENV_KEYS = frozenset(("JAVA_TOOL_OPTIONS", "NODE_OPTIONS"))
N4_BOOT_DISK = {"type": "hyperdisk-balanced", "image": "batch-cos"}

_SECRET_RE = re.compile(r"\Aprojects/[^/]+/secrets/[^/]+/versions/[^/]+\Z")


@dataclass(frozen=True, slots=True)
class BatchConfig:
    """Cloud choices that are not part of a benchmark case's identity."""

    results_bucket: str
    anonymous_worker_service_account: str
    authenticated_worker_service_account: str | None = None
    aws_credential_secret: str | None = None
    network: str | None = None
    subnetwork: str | None = None
    provisioning: str = "SPOT"
    zone: str | None = None
    output_path: str = DEFAULT_OUTPUT_PATH
    term_grace_s: int = DEFAULT_TERM_GRACE_S
    post_attempt_allowance_s: int = DEFAULT_POST_ATTEMPT_ALLOWANCE_S


def _validate_config(config: BatchConfig) -> None:
    if not config.results_bucket or config.results_bucket.startswith("gs://"):
        raise CampaignError("results bucket must be a bucket name without gs://")
    if any(character.isspace() for character in config.results_bucket):
        raise CampaignError("results bucket must not contain whitespace")
    if not config.anonymous_worker_service_account or any(
        character.isspace() for character in config.anonymous_worker_service_account
    ):
        raise CampaignError("anonymous worker service account must be a non-empty token")
    if (
        config.authenticated_worker_service_account is not None
        and config.authenticated_worker_service_account == config.anonymous_worker_service_account
    ):
        raise CampaignError("anonymous and authenticated worker service accounts must differ")
    if (config.network is None) != (config.subnetwork is None):
        raise CampaignError("network and subnetwork must be supplied together")
    for label, value in (("network", config.network), ("subnetwork", config.subnetwork)):
        if value is not None and (not value or any(character.isspace() for character in value)):
            raise CampaignError(f"{label} must be a non-empty token")
    if config.provisioning not in PROVISIONING_MODELS:
        raise CampaignError(
            f"provisioning must be one of {'|'.join(PROVISIONING_MODELS)}: {config.provisioning!r}"
        )
    if config.zone is not None and (
        not config.zone or any(character.isspace() for character in config.zone)
    ):
        raise CampaignError("zone must be a non-empty token")
    if not config.output_path.startswith("/") or "\x00" in config.output_path:
        raise CampaignError("local output path must be absolute and contain no NUL byte")
    if config.term_grace_s < 0:
        raise CampaignError("TERM grace must be nonnegative")
    if config.post_attempt_allowance_s < 1:
        raise CampaignError("post-attempt allowance must be positive")


def _image_uri(attempt: Attempt) -> str:
    derived = attempt.image.get("derived_image")
    image_uri = attempt.image.get("image_uri")
    if not isinstance(derived, str) or DIGEST_RE.fullmatch(derived) is None:
        raise CampaignError(f"{attempt.case.tool}: derived_image is not a sha256 digest")
    if (
        not isinstance(image_uri, str)
        or not image_uri.removesuffix(f"@{derived}")
        or not image_uri.endswith(f"@{derived}")
    ):
        raise CampaignError(f"{attempt.case.tool}: image_uri must be pinned with @{derived}")
    return image_uri


def _image_token(attempt: Attempt, field: str) -> str:
    value = attempt.image.get(field)
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise CampaignError(f"{attempt.case.tool}: image {field} must be a non-empty token")
    return value


def _commands(attempt: Attempt, config: BatchConfig) -> list[str]:
    resources = attempt.case.resources
    container_memory = resources.container_memory_gb
    commands = [
        "--request-schema",
        "1",
        "--output",
        config.output_path,
        "--timeout",
        str(attempt.case.timeout_s),
        "--term-grace",
        str(config.term_grace_s),
        "--tool",
        attempt.case.tool,
        "--tool-version",
        _image_token(attempt, "subject_version"),
        "--derived-image",
        str(attempt.image["derived_image"]),
        "--harness-revision",
        _image_token(attempt, "harness_revision"),
        "--operation",
        "list",
        "--auth",
        attempt.case.auth,
        "--mode",
        attempt.case.mode,
        "--bucket",
        attempt.bucket,
        "--region",
        attempt.region,
        "--prefix",
        "",
        "--scope",
        "full",
        "--campaign-id",
        attempt.campaign,
        "--job-id",
        attempt.job_id,
        "--case-id",
        attempt.case.case_id,
        "--case-fingerprint",
        attempt.case.fingerprint,
        "--attempt-fingerprint",
        attempt.fingerprint,
        "--run-ordinal",
        str(attempt.run_ordinal),
        "--submission-number",
        str(attempt.submission),
        "--machine-type",
        resources.machine_type,
        "--vcpus",
        str(resources.vcpus),
        "--memory-gb",
        str(resources.memory_gb),
        "--container-memory-gb",
        "none" if container_memory is None else str(container_memory),
        "--destination",
        f"gs://{config.results_bucket}/{attempt.prefix}",
    ]
    seen: set[str] = set()
    for name, value in attempt.case.env:
        if name not in CASE_ENV_KEYS:
            allowed = "|".join(sorted(CASE_ENV_KEYS))
            raise CampaignError(f"case environment key must be one of {allowed}: {name!r}")
        if name in seen:
            raise CampaignError(f"case environment repeats {name}")
        if not value or "\x00" in value:
            raise CampaignError(f"case environment {name} value must be non-empty and NUL-free")
        seen.add(name)
        commands.extend(("--case-env", f"{name}={value}"))
    return commands


def render_job(attempt: Attempt, config: BatchConfig) -> dict[str, Any]:
    """Return the Batch v1 job body for ``attempt`` without I/O or mutation."""
    _validate_config(config)
    image_uri = _image_uri(attempt)

    if attempt.case.auth == "anonymous":
        service_account = config.anonymous_worker_service_account
        secret_variables: dict[str, str] = {}
    elif attempt.case.auth == "authenticated":
        secret = config.aws_credential_secret
        authenticated_service_account = config.authenticated_worker_service_account
        if not authenticated_service_account or any(
            character.isspace() for character in authenticated_service_account
        ):
            raise CampaignError(
                "authenticated case requires an authenticated worker service account"
            )
        service_account = authenticated_service_account
        if not secret or _SECRET_RE.fullmatch(secret) is None:
            raise CampaignError("authenticated case requires a Secret Manager version resource")
        secret_variables = {CREDENTIAL_ENV_VAR: secret}
    else:  # A hand-built Attempt should fail here as clearly as a parsed plan does.
        raise CampaignError(f"unknown authentication stratum: {attempt.case.auth!r}")

    container: dict[str, Any] = {
        "imageUri": image_uri,
        # Batch appends these to the fixed ENTRYPOINT embedded in the derived image.
        "commands": _commands(attempt, config),
    }
    if attempt.case.resources.docker_options:
        container["options"] = shlex.join(attempt.case.resources.docker_options)

    task_spec: dict[str, Any] = {
        "runnables": [{"container": container}],
        "computeResource": {
            # Batch v1 represents these int64 fields as decimal JSON strings.
            "cpuMilli": str(attempt.case.resources.cpu_milli),
            "memoryMib": str(attempt.case.resources.memory_mib),
        },
        "maxRetryCount": 0,
        "maxRunDuration": (
            f"{attempt.case.timeout_s + config.term_grace_s + config.post_attempt_allowance_s}s"
        ),
    }
    if secret_variables:
        task_spec["environment"] = {"secretVariables": secret_variables}

    instance_policy: dict[str, Any] = {
        "machineType": attempt.case.resources.machine_type,
        "provisioningModel": config.provisioning,
    }
    # N4 supports Hyperdisk only; Batch otherwise defaults a boot disk to
    # pd-balanced, which cannot provision an N4 VM. The short image name is a
    # Batch-supported Container-Optimized OS image.
    if attempt.case.resources.machine_type.startswith("n4-"):
        instance_policy["bootDisk"] = dict(N4_BOOT_DISK)

    allocation: dict[str, Any] = {
        "instances": [{"policy": instance_policy}],
        "serviceAccount": {"email": service_account},
    }
    if config.zone is not None:
        zone = config.zone.removeprefix("zones/")
        allocation["location"] = {"allowedLocations": [f"zones/{zone}"]}
    if config.network is not None and config.subnetwork is not None:
        allocation["network"] = {
            "networkInterfaces": [{"network": config.network, "subnetwork": config.subnetwork}]
        }

    return {
        "taskGroups": [{"taskCount": "1", "parallelism": "1", "taskSpec": task_spec}],
        "allocationPolicy": allocation,
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }
