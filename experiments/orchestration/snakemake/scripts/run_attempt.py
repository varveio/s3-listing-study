"""Run the image-owned worker, then write its validated result pointer."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class ResultPointerError(ValueError):
    """The local worker result cannot safely identify this planned attempt."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ResultPointerError(f"duplicate result JSON key: {key}")
        document[key] = value
    return document


def _canonical_attempt_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ResultPointerError("result attempt_id must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        raise ResultPointerError("result attempt_id must be a canonical UUID") from None
    if str(parsed) != value or parsed.version != 4:
        raise ResultPointerError("result attempt_id must be a canonical UUIDv4")
    return value


def validated_result_pointer(
    result_path: str | Path,
    *,
    destination: str,
    campaign_id: str,
    job_id: str,
    case_id: str,
    case_fingerprint: str,
    attempt_fingerprint: str,
    run_ordinal: int,
    submission_number: int,
) -> dict[str, str]:
    """Read one worker result and return its exact, planned evidence pointer."""
    path = Path(result_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ResultPointerError(f"cannot read worker result {path}: {exc}") from None
    try:
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResultPointerError(f"worker result is not valid JSON: {exc}") from None
    if not isinstance(document, Mapping):
        raise ResultPointerError("worker result must be a JSON object")
    if document.get("schema_version") not in (2, 3):
        raise ResultPointerError("worker result schema is unsupported")
    campaign = document.get("campaign")
    if not isinstance(campaign, Mapping):
        raise ResultPointerError("worker result campaign must be an object")

    expected: dict[str, object] = {
        "campaign_id": campaign_id,
        "job_id": job_id,
        "case_id": case_id,
        "case_fingerprint": case_fingerprint,
        "attempt_fingerprint": attempt_fingerprint,
        "run_ordinal": run_ordinal,
        "submission_number": submission_number,
    }
    for field, value in expected.items():
        recorded = campaign.get(field)
        if type(recorded) is not type(value) or recorded != value:
            raise ResultPointerError(
                f"worker result campaign {field} does not match planned attempt"
            )

    attempt_id = _canonical_attempt_id(document.get("attempt_id"))
    destination_root = destination.rstrip("/")
    if not destination_root.startswith("gs://") or any(
        character.isspace() for character in destination_root
    ):
        raise ResultPointerError("projected destination must be a canonical gs:// URI")
    artifact_uri = f"{destination_root}/{attempt_id}"
    result_uri = f"{artifact_uri}/result.json"
    if document.get("artifact_uri") != artifact_uri:
        raise ResultPointerError("worker result artifact_uri does not match projected destination")
    if document.get("result_uri") != result_uri:
        raise ResultPointerError("worker result result_uri does not match projected destination")
    return {
        "attempt_id": attempt_id,
        "artifact_uri": artifact_uri,
        "result_uri": result_uri,
        "result_sha256": hashlib.sha256(raw).hexdigest(),
    }


def write_result_marker(
    marker_path: str | Path,
    result_path: str | Path,
    *,
    destination: str,
    campaign_id: str,
    job_id: str,
    case_id: str,
    case_fingerprint: str,
    attempt_fingerprint: str,
    run_ordinal: int,
    submission_number: int,
    campaign_sha256: str,
    execution_sha256: str,
) -> None:
    """Validate the result before creating the deterministic marker."""
    pointer = validated_result_pointer(
        result_path,
        destination=destination,
        campaign_id=campaign_id,
        job_id=job_id,
        case_id=case_id,
        case_fingerprint=case_fingerprint,
        attempt_fingerprint=attempt_fingerprint,
        run_ordinal=run_ordinal,
        submission_number=submission_number,
    )
    marker = Path(marker_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "schema_version": 1,
                    "kind": "s3-listing-study-result-pointer",
                    "campaign_sha256": campaign_sha256,
                    "execution_sha256": execution_sha256,
                    "job_id": job_id,
                    **pointer,
                },
                stream,
                sort_keys=True,
            )
            stream.write("\n")
    except FileExistsError:
        raise ResultPointerError(f"refusing to replace existing result pointer {marker}") from None


def main(context: Any) -> None:
    command = [
        "/usr/bin/python3",
        "-I",
        "/opt/s3-listing-study/attempt.pyz",
        *context.params.worker_argv,
    ]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)

    write_result_marker(
        context.output.marker,
        Path(context.params.output_path) / "result.json",
        destination=context.params.destination,
        campaign_id=context.params.campaign_id,
        job_id=context.params.job_id,
        case_id=context.params.case_id,
        case_fingerprint=context.params.case_fingerprint,
        attempt_fingerprint=context.params.attempt_fingerprint,
        run_ordinal=context.params.run_ordinal,
        submission_number=context.params.submission_number,
        campaign_sha256=context.params.campaign_sha256,
        execution_sha256=context.params.execution_sha256,
    )


# Snakemake injects this global when it executes a ``script:`` directive.
if "snakemake" in globals():  # pragma: no cover - exercised by Snakemake itself
    main(globals()["snakemake"])
