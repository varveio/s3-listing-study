"""Validated campaign loading and the local Snakemake/Batch parity projection."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from s3_listing_study.manager.bench.plan import (
    CASE_ID_RE,
    TOOL_RE,
    Resources,
)
from s3_listing_study.manager.bench.plan import (
    fingerprint as case_fingerprint_for,
)
from s3_listing_study.manager.campaign import (
    DIGEST_RE,
    JOB_ID_MAX,
    JOB_ID_RE,
    attempt_fingerprint,
    attempt_prefix,
    job_id,
    validate_campaign_id,
)
from s3_listing_study.manager.campaign.request import (
    CASE_ENV_KEYS,
    evidence_prefix,
    worker_argv,
)

PROFILE_FIELDS = {
    "schema_version",
    "project",
    "location",
    "results_bucket",
    "provisioning",
    "zone",
    "network",
    "subnetwork",
    "anonymous_worker_service_account",
    "authenticated_worker_service_account",
    "aws_credential_secret",
    "output_path",
    "term_grace_s",
    "post_attempt_allowance_s",
    "retry_count",
    "n4_boot_disk",
    "evidence_prefix",
    "orchestration_prefix",
    "executor",
}
CAMPAIGN_FIELDS = {
    "schema_version",
    "campaign",
    "results_bucket",
    "attempt_fingerprint_version",
    "provisioning",
    "zone",
    "plans",
    "images",
    "attempts",
}
PLAN_FIELDS = {"bucket", "region", "path", "sha256"}
IMAGE_FIELDS = {
    "derived_image",
    "image_uri",
    "shared_base_digest",
    "shared_base_uri",
    "shared_base_source_sha256",
    "tool_build_sha256",
    "tool_artifact",
    "tool_version",
    "adapter_bundle_sha256",
    "harness_revision",
    "tool_image_digest",
    "tool_image_uri",
    "selection_sha256",
}
ATTEMPT_FIELDS = {
    "job_id",
    "submission",
    "run_ordinal",
    "bucket",
    "region",
    "tool",
    "case_id",
    "mode",
    "auth",
    "case_fingerprint",
    "derived_image",
    "fingerprint",
    "attempt_fingerprint",
    "resources",
    "env",
    "reps",
    "timeout_s",
    "prefix",
}
RESOURCE_FIELDS = {"vcpus", "memory_gb", "machine_type", "container_memory_gb"}
EXECUTOR_FIELDS = {
    "name",
    "adapter_version",
    "upstream_plugin_version",
    "snakemake_version",
    "adapter_source_sha256",
}
EXECUTOR_RUNTIME_FIELDS = EXECUTOR_FIELDS | {"runtime_image"}

HEX_RE = re.compile(r"\A[0-9a-f]{64}\Z")
BUCKET_RE = re.compile(r"\A[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
REGION_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")
PROJECT_RE = re.compile(r"\A[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
MACHINE_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")
VERSION_RE = re.compile(r"\A[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?\Z")
PINNED_IMAGE_RE = re.compile(r"\A[^\s@]+@sha256:[0-9a-f]{64}\Z")
SERVICE_ACCOUNT_RE = re.compile(
    r"\A[a-z0-9][a-z0-9-]{0,62}@[a-z][a-z0-9-]{3,28}[a-z0-9]\.iam\.gserviceaccount\.com\Z"
)
SECRET_RE = re.compile(
    r"\Aprojects/[a-z][a-z0-9-]{4,28}[a-z0-9]/secrets/[A-Za-z0-9_-]+/"
    r"versions/(?:[0-9]+|latest)\Z"
)
DEPLOYABLE_RUN_ROOT = Path(".snakemake") / "runs"
MARKER_ROOT = "markers"
N4_BOOT_DISK = {"type": "hyperdisk-balanced", "image": "batch-cos"}


class WorkflowInputError(ValueError):
    """A frozen campaign or execution profile is unusable."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_fields(value: Any, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowInputError(f"{label} must be an object")
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        detail = []
        if missing:
            detail.append(f"missing {', '.join(missing)}")
        if unknown:
            detail.append(f"unknown {', '.join(unknown)}")
        raise WorkflowInputError(f"invalid {label} fields ({'; '.join(detail)})")
    return value


def _token(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(c.isspace() for c in value)
        or "\x00" in value
    ):
        raise WorkflowInputError(f"{label} must be a non-empty token")
    return value


def _integer(value: Any, *, label: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise WorkflowInputError(f"{label} must be an integer of at least {minimum}")
    if maximum is not None and value > maximum:
        raise WorkflowInputError(f"{label} must be at most {maximum}")
    return value


def _hex(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or HEX_RE.fullmatch(value) is None:
        raise WorkflowInputError(f"{label} must be 64 lowercase hex digits")
    return value


def _digest_reference(
    value: Mapping[str, Any], digest_field: str, uri_field: str, *, label: str
) -> None:
    digest = value[digest_field]
    uri = value[uri_field]
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise WorkflowInputError(f"{label} digest is invalid")
    if not isinstance(uri, str) or not uri.endswith(f"@{digest}") or any(c.isspace() for c in uri):
        raise WorkflowInputError(f"{label} URI is not pinned to its digest")


def _bucket(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or BUCKET_RE.fullmatch(value) is None or ".." in value:
        raise WorkflowInputError(f"{label} is not a path-safe bucket name")
    return value


def _load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        document = json.loads(
            source.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowInputError(f"cannot read {source} as JSON: {exc}") from None
    if not isinstance(document, dict):
        raise WorkflowInputError(f"{source} is not a JSON object")
    return document


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_sha256(path: str | Path, expected: Any, *, label: str) -> str:
    expected = _hex(expected, label=f"expected {label} SHA-256")
    observed = sha256_file(path)
    if observed != expected:
        raise WorkflowInputError(
            f"{label} SHA-256 does not match operator-supplied digest: {observed} != {expected}"
        )
    return observed


def deployable_source_paths(
    campaign_path: str | Path,
    execution_profile_path: str | Path,
    *,
    working_directory: str | Path,
) -> tuple[str, str]:
    """Return source-archive-safe paths under one ignored workflow run directory."""
    base = Path(working_directory).resolve()
    run_root = (base / DEPLOYABLE_RUN_ROOT).resolve()
    resolved: list[Path] = []
    for source, expected_name, label in (
        (campaign_path, "campaign.json", "campaign"),
        (execution_profile_path, "execution-profile.json", "execution profile"),
    ):
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = base / candidate
        candidate = candidate.resolve()
        try:
            relative = candidate.relative_to(run_root)
        except ValueError:
            raise WorkflowInputError(
                f"{label} must be below {DEPLOYABLE_RUN_ROOT.as_posix()}/ in the working directory"
            ) from None
        if len(relative.parts) != 2 or relative.name != expected_name:
            raise WorkflowInputError(
                f"{label} must be named {expected_name} in one "
                f"{DEPLOYABLE_RUN_ROOT.as_posix()}/<run>/ directory"
            )
        resolved.append(candidate)
    if resolved[0].parent != resolved[1].parent:
        raise WorkflowInputError(
            "campaign and execution profile must share one frozen run directory"
        )
    return (
        resolved[0].relative_to(base).as_posix(),
        resolved[1].relative_to(base).as_posix(),
    )


def _validate_image(tool: str, raw: Any) -> dict[str, Any]:
    image = _exact_fields(raw, IMAGE_FIELDS, label=f"campaign image {tool}")
    _digest_reference(image, "derived_image", "image_uri", label=f"{tool} execution image")
    _digest_reference(image, "shared_base_digest", "shared_base_uri", label=f"{tool} shared image")
    _digest_reference(image, "tool_image_digest", "tool_image_uri", label=f"{tool} tool image")
    for field in (
        "shared_base_source_sha256",
        "tool_build_sha256",
        "adapter_bundle_sha256",
        "selection_sha256",
    ):
        _hex(image[field], label=f"{tool} {field}")
    artifact = _exact_fields(
        image["tool_artifact"], {"kind", "locator", "sha256"}, label=f"{tool} tool_artifact"
    )
    _token(artifact["kind"], label=f"{tool} artifact kind")
    _token(artifact["locator"], label=f"{tool} artifact locator")
    _hex(artifact["sha256"], label=f"{tool} artifact SHA-256")
    _token(image["tool_version"], label=f"{tool} tool_version")
    if (
        not isinstance(image["harness_revision"], str)
        or re.fullmatch(r"[0-9a-f]{40}", image["harness_revision"]) is None
    ):
        raise WorkflowInputError(f"{tool} harness_revision must be a full lowercase commit ID")
    return image


def load_campaign(path: str | Path) -> dict[str, Any]:
    document = _exact_fields(_load_json(path), CAMPAIGN_FIELDS, label="campaign")
    if document["schema_version"] != 3 or isinstance(document["schema_version"], bool):
        raise WorkflowInputError("campaign schema_version must be 3")
    campaign = document["campaign"]
    try:
        validate_campaign_id(campaign)
    except (TypeError, ValueError) as exc:
        raise WorkflowInputError(f"invalid campaign id: {exc}") from None
    except Exception as exc:
        raise WorkflowInputError(f"invalid campaign id: {exc}") from None
    _bucket(document["results_bucket"], label="campaign results_bucket")
    if document["attempt_fingerprint_version"] != 3:
        raise WorkflowInputError("campaign attempt_fingerprint_version must be 3")
    if document["provisioning"] not in ("STANDARD", "SPOT"):
        raise WorkflowInputError("campaign provisioning must be STANDARD or SPOT")
    zone = document["zone"]
    if zone is not None and REGION_RE.fullmatch(_token(zone, label="campaign zone")) is None:
        raise WorkflowInputError("campaign zone is invalid")

    raw_images = document["images"]
    if not isinstance(raw_images, dict) or not raw_images:
        raise WorkflowInputError("campaign images must be a non-empty object")
    for tool, image in raw_images.items():
        if not isinstance(tool, str) or TOOL_RE.fullmatch(tool) is None:
            raise WorkflowInputError(f"campaign image key is not a path-safe tool name: {tool!r}")
        _validate_image(tool, image)

    plans = document["plans"]
    if not isinstance(plans, list) or not plans:
        raise WorkflowInputError("campaign plans must be a non-empty list")
    plan_coordinates: dict[str, str] = {}
    for index, raw in enumerate(plans):
        plan = _exact_fields(raw, PLAN_FIELDS, label=f"campaign plan {index}")
        bucket = _bucket(plan["bucket"], label=f"campaign plan {index} bucket")
        region = _token(plan["region"], label=f"campaign plan {index} region")
        if REGION_RE.fullmatch(region) is None:
            raise WorkflowInputError(f"campaign plan {index} region is invalid")
        if bucket in plan_coordinates:
            raise WorkflowInputError(f"campaign repeats plan bucket {bucket!r}")
        plan_coordinates[bucket] = region
        expected_path = f"campaigns/{campaign}/inputs/plans/{bucket}.yaml"
        if plan["path"] != expected_path:
            raise WorkflowInputError(f"campaign plan {bucket} path is not canonical")
        _hex(plan["sha256"], label=f"campaign plan {bucket} SHA-256")

    attempts = document["attempts"]
    if not isinstance(attempts, list) or not attempts:
        raise WorkflowInputError("campaign attempts must be a non-empty list")
    seen: set[tuple[str, str, str, int]] = set()
    used_tools: set[str] = set()
    used_buckets: set[str] = set()
    for index, raw in enumerate(attempts):
        row = _exact_fields(raw, ATTEMPT_FIELDS, label=f"campaign attempt {index}")
        bucket = _bucket(row["bucket"], label=f"attempt {index} bucket")
        tool = row["tool"]
        case_id = row["case_id"]
        if not isinstance(tool, str) or TOOL_RE.fullmatch(tool) is None:
            raise WorkflowInputError(f"attempt {index} tool is not path-safe")
        if not isinstance(case_id, str) or CASE_ID_RE.fullmatch(case_id) is None:
            raise WorkflowInputError(f"attempt {index} case_id is not path-safe")
        if tool not in raw_images:
            raise WorkflowInputError(f"attempt references unknown image {tool!r}")
        if bucket not in plan_coordinates or row["region"] != plan_coordinates[bucket]:
            raise WorkflowInputError(f"attempt {index} bucket/region does not match a frozen plan")
        run_ordinal = _integer(
            row["run_ordinal"], label=f"attempt {index} run_ordinal", minimum=1, maximum=99
        )
        submission = _integer(
            row["submission"], label=f"attempt {index} submission", minimum=1, maximum=99
        )
        coordinate = (bucket, tool, case_id, run_ordinal)
        if coordinate in seen:
            raise WorkflowInputError(f"campaign repeats attempt coordinate {coordinate!r}")
        seen.add(coordinate)
        used_tools.add(tool)
        used_buckets.add(bucket)
        _token(row["mode"], label=f"attempt {index} mode")
        if row["auth"] not in ("anonymous", "authenticated"):
            raise WorkflowInputError(f"attempt {index} auth is invalid")
        case_fingerprint = _hex(row["case_fingerprint"], label=f"attempt {index} case fingerprint")
        fingerprint = _hex(row["attempt_fingerprint"], label=f"attempt {index} fingerprint")
        if row["fingerprint"] != fingerprint:
            raise WorkflowInputError(f"attempt {index} fingerprint aliases disagree")
        image = raw_images[tool]
        if row["derived_image"] != image["derived_image"]:
            raise WorkflowInputError(f"attempt {index} derived image disagrees with registration")
        resources = _exact_fields(
            row["resources"], RESOURCE_FIELDS, label=f"attempt {index} resources"
        )
        vcpus = _integer(resources["vcpus"], label=f"attempt {index} vcpus", minimum=1)
        memory_gb = _integer(resources["memory_gb"], label=f"attempt {index} memory_gb", minimum=1)
        machine_type = _token(resources["machine_type"], label=f"attempt {index} machine_type")
        if MACHINE_RE.fullmatch(machine_type) is None:
            raise WorkflowInputError(f"attempt {index} machine_type is invalid")
        ceiling = resources["container_memory_gb"]
        if ceiling is not None:
            _integer(ceiling, label=f"attempt {index} container_memory_gb", minimum=1)
            if ceiling > memory_gb:
                raise WorkflowInputError(f"attempt {index} container memory exceeds machine memory")
        reps = _integer(row["reps"], label=f"attempt {index} reps", minimum=1, maximum=99)
        if run_ordinal > reps:
            raise WorkflowInputError(f"attempt {index} run_ordinal exceeds reps")
        _integer(row["timeout_s"], label=f"attempt {index} timeout_s", minimum=1)
        environment = row["env"]
        if not isinstance(environment, list):
            raise WorkflowInputError(f"attempt {index} env must be a list")
        seen_env: set[str] = set()
        for pair in environment:
            if not isinstance(pair, list) or len(pair) != 2:
                raise WorkflowInputError(f"attempt {index} environment entries must be pairs")
            name, value = pair
            if name not in CASE_ENV_KEYS or name in seen_env:
                raise WorkflowInputError(f"attempt {index} environment key is invalid or repeated")
            _token(value, label=f"attempt {index} environment value")
            seen_env.add(name)
        expected_case_fingerprint = case_fingerprint_for(
            bucket=bucket,
            region=row["region"],
            tool=tool,
            mode=row["mode"],
            auth=row["auth"],
            resources=Resources(
                vcpus=vcpus,
                memory_gb=memory_gb,
                machine_type=machine_type,
                container_memory_gb=ceiling,
            ),
            timeout_s=row["timeout_s"],
            env=tuple((name, value) for name, value in environment),
        )
        if case_fingerprint != expected_case_fingerprint:
            raise WorkflowInputError(f"attempt {index} case fingerprint does not match its inputs")
        if attempt_fingerprint(case_fingerprint=case_fingerprint, components=image) != fingerprint:
            raise WorkflowInputError(f"attempt {index} fingerprint does not match its inputs")
        expected_prefix = attempt_prefix(
            campaign=campaign,
            bucket=bucket,
            tool=tool,
            case_id=case_id,
            run_ordinal=run_ordinal,
        )
        if row["prefix"] != expected_prefix:
            raise WorkflowInputError(f"attempt {index} prefix is not canonical")
        expected_job_id = job_id(
            campaign=campaign,
            tool=tool,
            case_id=case_id,
            fingerprint=fingerprint,
            run_ordinal=run_ordinal,
            submission=submission,
        )
        if (
            not isinstance(row["job_id"], str)
            or len(row["job_id"]) > JOB_ID_MAX
            or JOB_ID_RE.fullmatch(row["job_id"]) is None
            or row["job_id"] != expected_job_id
        ):
            raise WorkflowInputError(f"attempt {index} job_id is not canonical")
        if vcpus * 1000 <= 0:  # Makes the scheduler projection invariant explicit.
            raise WorkflowInputError(f"attempt {index} CPU request is invalid")
    if used_tools != set(raw_images) or used_buckets != set(plan_coordinates):
        raise WorkflowInputError("campaign plans/images are not exactly covered by attempts")
    return document


def load_execution_profile(path: str | Path) -> dict[str, Any]:
    document = _exact_fields(_load_json(path), PROFILE_FIELDS, label="execution profile")
    if document["schema_version"] != 2 or isinstance(document["schema_version"], bool):
        raise WorkflowInputError("execution profile schema_version must be 2")
    project = _token(document["project"], label="execution project")
    location = _token(document["location"], label="execution location")
    if PROJECT_RE.fullmatch(project) is None:
        raise WorkflowInputError("execution project is invalid")
    if REGION_RE.fullmatch(location) is None:
        raise WorkflowInputError("execution location is invalid")
    _bucket(document["results_bucket"], label="execution results_bucket")
    if document["provisioning"] not in ("STANDARD", "SPOT"):
        raise WorkflowInputError("execution provisioning must be STANDARD or SPOT")
    zone = document["zone"]
    if zone is not None and REGION_RE.fullmatch(_token(zone, label="execution zone")) is None:
        raise WorkflowInputError("execution zone is invalid")
    if zone is not None and not zone.startswith(f"{location}-"):
        raise WorkflowInputError("execution zone is outside its location")
    if (document["network"] is None) != (document["subnetwork"] is None):
        raise WorkflowInputError("execution network and subnetwork must be supplied together")
    for field in ("network", "subnetwork"):
        if document[field] is not None:
            _token(document[field], label=f"execution {field}")
    if document["network"] is not None:
        if (
            re.fullmatch(
                rf"projects/{re.escape(project)}/global/networks/[a-z][a-z0-9-]{{0,62}}",
                document["network"],
            )
            is None
        ):
            raise WorkflowInputError("execution network resource is invalid")
        if (
            re.fullmatch(
                rf"projects/{re.escape(project)}/regions/{re.escape(location)}/subnetworks/"
                r"[a-z][a-z0-9-]{0,62}",
                document["subnetwork"],
            )
            is None
        ):
            raise WorkflowInputError("execution subnetwork resource is invalid")
    anonymous = document["anonymous_worker_service_account"]
    authenticated = document["authenticated_worker_service_account"]
    if not isinstance(anonymous, str) or SERVICE_ACCOUNT_RE.fullmatch(anonymous) is None:
        raise WorkflowInputError("execution anonymous worker identity is invalid")
    if authenticated is not None and (
        not isinstance(authenticated, str) or SERVICE_ACCOUNT_RE.fullmatch(authenticated) is None
    ):
        raise WorkflowInputError("execution authenticated worker identity is invalid")
    if authenticated == anonymous:
        raise WorkflowInputError("execution worker identities must differ")
    secret = document["aws_credential_secret"]
    if secret is not None and (not isinstance(secret, str) or SECRET_RE.fullmatch(secret) is None):
        raise WorkflowInputError("execution AWS credential secret is invalid")
    if (authenticated is None) != (secret is None):
        raise WorkflowInputError(
            "execution authenticated worker identity and AWS credential secret must be paired"
        )
    output_path = document["output_path"]
    if (
        not isinstance(output_path, str)
        or not output_path.startswith("/")
        or "\x00" in output_path
        or str(PurePosixPath(output_path)) != output_path
        or ".." in PurePosixPath(output_path).parts
    ):
        raise WorkflowInputError("execution output_path must be a canonical absolute path")
    _integer(document["term_grace_s"], label="execution term_grace_s")
    _integer(
        document["post_attempt_allowance_s"], label="execution post_attempt_allowance_s", minimum=1
    )
    if document["retry_count"] != 0 or isinstance(document["retry_count"], bool):
        raise WorkflowInputError("execution retry_count must be zero")
    if document["n4_boot_disk"] != N4_BOOT_DISK:
        raise WorkflowInputError("execution n4_boot_disk must be the fixed N4 Batch disk")
    if document["orchestration_prefix"] != "snakemake/orchestration/":
        raise WorkflowInputError("execution orchestration_prefix must be snakemake/orchestration/")
    if document["evidence_prefix"] != "snakemake/evidence/":
        raise WorkflowInputError("execution evidence_prefix must be snakemake/evidence/")
    raw_executor = document["executor"]
    if not isinstance(raw_executor, dict):
        raise WorkflowInputError("execution executor must be an object")
    executor_name = raw_executor.get("name")
    if executor_name == "snakemake-executor-plugin-googlebatch-study":
        executor = _exact_fields(raw_executor, EXECUTOR_RUNTIME_FIELDS, label="execution executor")
        runtime_image = executor["runtime_image"]
        if not isinstance(runtime_image, str) or PINNED_IMAGE_RE.fullmatch(runtime_image) is None:
            raise WorkflowInputError("execution runtime image must be pinned by digest")
    elif executor_name == "snakemake-executor-plugin-googlebatch":
        # Compatibility for the local direct-renderer parity fixture. The
        # actual Snakefile launch path rejects this projection-only profile.
        executor = _exact_fields(raw_executor, EXECUTOR_FIELDS, label="execution executor")
    else:
        raise WorkflowInputError("execution executor name is invalid")
    for field in ("adapter_version", "upstream_plugin_version", "snakemake_version"):
        if not isinstance(executor[field], str) or VERSION_RE.fullmatch(executor[field]) is None:
            raise WorkflowInputError(f"execution executor {field} is invalid")
    _hex(executor["adapter_source_sha256"], label="execution executor adapter source SHA-256")
    return document


def executor_runtime_image(profile: Mapping[str, Any]) -> str:
    """Return the frozen helper image, rejecting projection-only profiles."""
    executor = profile["executor"]
    if executor["name"] != "snakemake-executor-plugin-googlebatch-study":
        raise WorkflowInputError("execution profile does not select the runnable Batch adapter")
    return str(executor["runtime_image"])


def canonical_profile_bytes(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def freeze_execution_profile(source: str | Path, destination: str | Path) -> tuple[bool, str]:
    document = load_execution_profile(source)
    if document["executor"]["name"] == "snakemake-executor-plugin-googlebatch-study":
        # This dependency-heavy import is intentionally limited to the outer
        # freeze command. Nested Snakemake only performs schema validation.
        from snakemake_executor_plugin_googlebatch_study.executor import (
            validate_installed_executor_identity,
        )

        try:
            validate_installed_executor_identity(document["executor"])
        except ValueError as exc:
            raise WorkflowInputError(str(exc)) from None
    content = canonical_profile_bytes(document)
    target = Path(destination)
    try:
        with target.open("xb") as output:
            output.write(content)
    except FileExistsError:
        if target.read_bytes() != content:
            raise WorkflowInputError(f"{target} already exists with different content") from None
        created = False
    except OSError as exc:
        raise WorkflowInputError(f"could not create {target}: {exc}") from None
    else:
        target.chmod(0o444)
        created = True
    return created, hashlib.sha256(content).hexdigest()


def marker_path(
    *,
    campaign: str,
    campaign_sha256: str,
    execution_sha256: str,
    attempt: Mapping[str, Any],
) -> str:
    try:
        validate_campaign_id(campaign)
    except Exception as exc:
        raise WorkflowInputError(f"invalid marker campaign: {exc}") from None
    _hex(campaign_sha256, label="marker campaign SHA-256")
    _hex(execution_sha256, label="marker execution SHA-256")
    bucket = _bucket(attempt.get("bucket"), label="marker bucket")
    tool = attempt.get("tool")
    case_id = attempt.get("case_id")
    if not isinstance(tool, str) or TOOL_RE.fullmatch(tool) is None:
        raise WorkflowInputError("marker tool is not path-safe")
    if not isinstance(case_id, str) or CASE_ID_RE.fullmatch(case_id) is None:
        raise WorkflowInputError("marker case_id is not path-safe")
    ordinal = _integer(
        attempt.get("run_ordinal"), label="marker run_ordinal", minimum=1, maximum=99
    )
    return str(
        Path(MARKER_ROOT)
        / campaign
        / campaign_sha256
        / execution_sha256
        / bucket
        / tool
        / case_id
        / f"run-{ordinal}.json"
    )


def attempt_for_wildcards(
    attempts: Mapping[tuple[str, str, str, str], Mapping[str, Any]], wildcards: Any
) -> Mapping[str, Any]:
    coordinate = (
        str(wildcards.bucket),
        str(wildcards.tool),
        str(wildcards.case_id),
        str(wildcards.run_ordinal),
    )
    try:
        return attempts[coordinate]
    except KeyError:
        raise WorkflowInputError(f"unknown attempt coordinate {coordinate!r}") from None


def _profile_matches_campaign(campaign: Mapping[str, Any], profile: Mapping[str, Any]) -> None:
    for field in ("results_bucket", "provisioning", "zone"):
        if campaign[field] != profile[field]:
            raise WorkflowInputError(
                f"execution profile {field} does not match frozen campaign: "
                f"{profile[field]!r} != {campaign[field]!r}"
            )


def project_attempt(
    campaign: Mapping[str, Any], attempt: Mapping[str, Any], profile: Mapping[str, Any]
) -> dict[str, Any]:
    """Project one validated resolved row into every scheduling/request field."""
    _profile_matches_campaign(campaign, profile)
    image = campaign["images"][attempt["tool"]]
    resources = attempt["resources"]
    auth = attempt["auth"]
    if auth == "anonymous":
        service_account = profile["anonymous_worker_service_account"]
        secret_resource = None
    elif auth == "authenticated":
        service_account = profile["authenticated_worker_service_account"]
        secret_resource = profile["aws_credential_secret"]
        if not service_account or not secret_resource:
            raise WorkflowInputError("authenticated attempt requires identity and secret")
    else:
        raise WorkflowInputError(f"unknown authentication stratum {auth!r}")
    ceiling = resources["container_memory_gb"]
    options = () if ceiling is None else (f"--memory={ceiling}g", f"--memory-swap={ceiling}g")
    machine_type = resources["machine_type"]
    boot_disk = profile["n4_boot_disk"] if machine_type.startswith("n4-") else None
    max_run_duration_s = (
        attempt["timeout_s"] + profile["term_grace_s"] + profile["post_attempt_allowance_s"]
    )
    destination = evidence_prefix(
        campaign=campaign["campaign"],
        attempt_prefix=attempt["prefix"],
        object_root=profile["evidence_prefix"],
    )
    return {
        "job_id": attempt["job_id"],
        "image_uri": image["image_uri"],
        "output_path": profile["output_path"],
        "destination": f"gs://{profile['results_bucket']}/{destination}",
        "worker_argv": worker_argv(
            campaign=campaign["campaign"],
            attempt=attempt,
            image=image,
            results_bucket=profile["results_bucket"],
            output_path=profile["output_path"],
            term_grace_s=profile["term_grace_s"],
            destination_prefix=destination,
        ),
        "vcpus": resources["vcpus"],
        "machine_type": machine_type,
        "cpu_milli": resources["vcpus"] * 1000,
        "memory_mib": resources["memory_gb"] * 1024,
        "container_options": shlex.join(options) if options else None,
        "boot_disk": boot_disk,
        "retry_count": profile["retry_count"],
        "max_run_duration": f"{max_run_duration_s}s",
        "provisioning": profile["provisioning"],
        "zone": profile["zone"],
        "network": profile["network"],
        "subnetwork": profile["subnetwork"],
        "service_account": service_account,
        "secret_resource": secret_resource,
        "task_count": "1",
        "parallelism": "1",
        "logs_destination": "CLOUD_LOGGING",
    }


def frozen_provider_projection(
    campaign: Mapping[str, Any], attempt: Mapping[str, Any], profile: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the submission contract through a non-resource rule parameter."""
    projected = project_attempt(campaign, attempt, profile)
    batch_fields = {
        key: projected[key]
        for key in (
            "job_id",
            "image_uri",
            "machine_type",
            "cpu_milli",
            "memory_mib",
            "container_options",
            "boot_disk",
            "retry_count",
            "max_run_duration",
            "provisioning",
            "zone",
            "network",
            "subnetwork",
            "service_account",
            "secret_resource",
        )
    }
    executor = profile["executor"]
    return {
        "batch": batch_fields,
        "threads": projected["vcpus"],
        "project": profile["project"],
        "location": profile["location"],
        "runtime_image": executor_runtime_image(profile),
        "default_storage_provider": "gcs",
        "default_storage_prefix": (
            f"gcs://{profile['results_bucket']}/{profile['orchestration_prefix']}"
        ),
        "storage_gcs_project": profile["project"],
        "executor_identity": {
            key: executor[key]
            for key in (
                "name",
                "adapter_version",
                "upstream_plugin_version",
                "snakemake_version",
                "adapter_source_sha256",
            )
        },
    }


def canonical_provider_projection(
    campaign: Mapping[str, Any], attempt: Mapping[str, Any], profile: Mapping[str, Any]
) -> str:
    return json.dumps(
        frozen_provider_projection(campaign, attempt, profile),
        sort_keys=True,
        separators=(",", ":"),
    )
