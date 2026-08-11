"""The generic Snakemake projection over frozen real campaign rows."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from experiments.orchestration.snakemake.scripts.run_attempt import (
    ResultPointerError,
    write_result_marker,
)
from experiments.orchestration.snakemake.scripts.run_attempt import main as run_attempt_main
from experiments.orchestration.snakemake.scripts.workflow import (
    WorkflowInputError,
    canonical_profile_bytes,
    deployable_source_paths,
    freeze_execution_profile,
    load_campaign,
    load_execution_profile,
    marker_path,
    project_attempt,
    require_sha256,
)

from s3_listing_study.manager.bench import plan as bench
from s3_listing_study.manager.campaign import compile_campaign

ROOT = Path(__file__).resolve().parents[1]
SECRET = "projects/study1/secrets/aws/versions/7"


def images_for(plan: bench.Plan) -> dict[str, dict[str, object]]:
    images: dict[str, dict[str, object]] = {}
    for index, tool in enumerate(plan.tools()):
        registration = json.loads(
            (ROOT / "tools" / tool / "build" / "image.json").read_text(encoding="utf-8")
        )
        digit = format(index, "x")
        digest = "sha256:" + digit * 64
        tool_digest = "sha256:" + format(index + 1, "x") * 64
        images[tool] = {
            "derived_image": digest,
            "image_uri": f"us-east1-docker.pkg.dev/study/images/{tool}@{digest}",
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
    return images


def execution_profile() -> dict[str, object]:
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
            "name": "snakemake-executor-plugin-googlebatch",
            "adapter_version": "0.5.1",
            "upstream_plugin_version": "0.5.1",
            "snakemake_version": "9.25.1",
            "adapter_source_sha256": "c" * 64,
        },
    }


def runnable_execution_profile() -> dict[str, object]:
    profile = execution_profile()
    profile["executor"] = {
        "name": "snakemake-executor-plugin-googlebatch-study",
        "adapter_version": "0.1.0",
        "upstream_plugin_version": "0.5.1",
        "snakemake_version": "9.25.1",
        "adapter_source_sha256": "d" * 64,
        "runtime_image": ("us-east1-docker.pkg.dev/study/runtime/snakemake@sha256:" + "d" * 64),
    }
    return profile


def test_every_real_plan_attempt_projects_the_frozen_campaign_and_profile(tmp_path: Path) -> None:
    plan = bench.Plan.load(bench.default_path("noaa-rtma-pds"))
    compiled = compile_campaign(
        campaign="2026-08-11-snake",
        plans=(plan,),
        images=images_for(plan),
        results_bucket="study-results",
        provisioning="SPOT",
        zone="us-east1-b",
    )
    campaign_path = tmp_path / "campaign.json"
    profile_path = tmp_path / "profile.json"
    campaign_path.write_bytes(compiled.content)
    _write_json(profile_path, execution_profile())
    campaign = load_campaign(campaign_path)
    profile = load_execution_profile(profile_path)
    assert len(compiled.attempts) == len(plan.cases) == 17
    for attempt, row in zip(compiled.attempts, campaign["attempts"], strict=True):
        projected = project_attempt(campaign, row, profile)
        resources = row["resources"]
        assert projected["job_id"] == row["job_id"] == attempt.job_id
        assert projected["image_uri"] == campaign["images"][row["tool"]]["image_uri"]
        assert projected["machine_type"] == resources["machine_type"]
        assert projected["vcpus"] == resources["vcpus"]
        assert projected["cpu_milli"] == resources["vcpus"] * 1000
        assert projected["memory_mib"] == resources["memory_gb"] * 1024
        ceiling = resources["container_memory_gb"]
        assert projected["container_options"] == (
            None if ceiling is None else f"--memory={ceiling}g --memory-swap={ceiling}g"
        )
        assert projected["boot_disk"] == (
            profile["n4_boot_disk"] if resources["machine_type"].startswith("n4-") else None
        )
        assert projected["retry_count"] == 0
        assert projected["max_run_duration"] == f"{row['timeout_s'] + 5 + 1800}s"
        assert projected["provisioning"] == campaign["provisioning"] == profile["provisioning"]
        assert projected["zone"] == campaign["zone"] == profile["zone"]
        assert projected["network"] == profile["network"]
        assert projected["subnetwork"] == profile["subnetwork"]
        assert projected["service_account"] == (
            profile["anonymous_worker_service_account"]
            if row["auth"] == "anonymous"
            else profile["authenticated_worker_service_account"]
        )
        assert projected["secret_resource"] == (
            None if row["auth"] == "anonymous" else profile["aws_credential_secret"]
        )
        job_id_position = projected["worker_argv"].index("--job-id") + 1
        assert projected["worker_argv"][job_id_position] == attempt.job_id


def test_run_script_passes_worker_argv_to_the_image_owned_attempt_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_argv = ["--campaign-id", "2026-08-11-snake", "--job-id", "c-planned-r1-s1"]
    observed: list[list[str]] = []

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        observed.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        "experiments.orchestration.snakemake.scripts.run_attempt.subprocess.run", run
    )
    monkeypatch.setattr(
        "experiments.orchestration.snakemake.scripts.run_attempt.write_result_marker",
        lambda *_args, **_kwargs: None,
    )
    context = SimpleNamespace(
        params=SimpleNamespace(
            worker_argv=worker_argv,
            output_path="/tmp/result",
            destination="gs://study-results/evidence",
            campaign_id="2026-08-11-snake",
            job_id="c-planned-r1-s1",
            case_id="case",
            case_fingerprint="a" * 64,
            attempt_fingerprint="b" * 64,
            run_ordinal=1,
            submission_number=1,
            campaign_sha256="c" * 64,
            execution_sha256="d" * 64,
        ),
        output=SimpleNamespace(marker="marker.json"),
    )

    run_attempt_main(context)

    assert observed == [
        ["/usr/bin/python3", "-I", "/opt/s3-listing-study/attempt.pyz", *worker_argv]
    ]


def _write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _compiled_document() -> dict[str, object]:
    plan = bench.Plan.load(bench.default_path("noaa-rtma-pds"))
    return dict(
        compile_campaign(
            campaign="2026-08-11-snake",
            plans=(plan,),
            images=images_for(plan),
            results_bucket="study-results",
            provisioning="SPOT",
            zone="us-east1-b",
        ).document
    )


def test_loaders_accept_the_complete_compiler_and_profile_shapes(tmp_path: Path) -> None:
    campaign_path = tmp_path / "campaign.json"
    profile_path = tmp_path / "profile.json"
    _write_json(campaign_path, _compiled_document())
    _write_json(profile_path, execution_profile())

    assert len(load_campaign(campaign_path)["attempts"]) == 17
    assert load_execution_profile(profile_path)["evidence_prefix"] == "snakemake/evidence/"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("bucket", "../escape", "path-safe bucket"),
        ("tool", "../escape", "tool is not path-safe"),
        ("case_id", "../../escape", "case_id is not path-safe"),
        ("prefix", "campaigns/2026-08-11-snake/../../escape", "prefix is not canonical"),
        ("job_id", "valid-but-not-derived", "job_id is not canonical"),
        ("attempt_fingerprint", "0" * 64, "fingerprint aliases disagree"),
    ],
)
def test_campaign_loader_refuses_tampered_execution_coordinates(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    document = deepcopy(_compiled_document())
    document["attempts"][0][field] = value
    path = tmp_path / "campaign.json"
    _write_json(path, document)

    with pytest.raises(WorkflowInputError, match=message):
        load_campaign(path)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("mode",), "tampered-mode"),
        (("auth",), "authenticated"),
        (("timeout_s",), 30001),
        (("resources", "vcpus"), 4),
        (("resources", "memory_gb"), 8),
        (("resources", "machine_type"), "n4-highcpu-4"),
        (("resources", "container_memory_gb"), 1),
        (("env",), [["NODE_OPTIONS", "--max-old-space-size=1024"]]),
    ],
)
def test_campaign_loader_recomputes_case_fingerprint_from_semantic_row(
    tmp_path: Path, path: tuple[str, ...], value: object
) -> None:
    document = deepcopy(_compiled_document())
    selected = document["attempts"][0]
    for field in path[:-1]:
        selected = selected[field]
    selected[path[-1]] = value
    campaign_path = tmp_path / "campaign.json"
    _write_json(campaign_path, document)

    with pytest.raises(WorkflowInputError, match="case fingerprint does not match its inputs"):
        load_campaign(campaign_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("output_path", "tmp/output", "canonical absolute path"),
        ("term_grace_s", -1, "at least 0"),
        ("post_attempt_allowance_s", 0, "at least 1"),
        ("anonymous_worker_service_account", "not-an-identity", "identity is invalid"),
        ("aws_credential_secret", "projects/p/secrets/s", "secret is invalid"),
        ("n4_boot_disk", {"type": "pd-balanced", "image": "batch-cos"}, "fixed N4"),
        ("evidence_prefix", "../evidence/", "must be snakemake/evidence"),
    ],
)
def test_execution_profile_refuses_tampered_critical_fields(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    profile = execution_profile()
    profile[field] = value
    path = tmp_path / "profile.json"
    _write_json(path, profile)

    with pytest.raises(WorkflowInputError, match=message):
        load_execution_profile(path)


def test_execution_profile_freeze_is_canonical_and_create_only(tmp_path: Path) -> None:
    source = tmp_path / "profile-template.json"
    destination = tmp_path / "profile.json"
    source.write_text(json.dumps(execution_profile(), indent=7), encoding="utf-8")

    created, digest = freeze_execution_profile(source, destination)
    assert created is True
    assert destination.read_bytes().endswith(b"\n")
    assert require_sha256(destination, digest, label="execution profile") == digest
    assert freeze_execution_profile(source, destination) == (False, digest)

    changed = execution_profile()
    changed["post_attempt_allowance_s"] = 1801
    _write_json(source, changed)
    with pytest.raises(WorkflowInputError, match="different content"):
        freeze_execution_profile(source, destination)


def test_expected_digest_must_match_frozen_bytes(tmp_path: Path) -> None:
    path = tmp_path / "campaign.json"
    path.write_bytes(b"frozen\n")
    with pytest.raises(WorkflowInputError, match="operator-supplied digest"):
        require_sha256(path, "0" * 64, label="campaign")


def test_deployable_sources_refuse_paths_outside_one_ignored_run(tmp_path: Path) -> None:
    run = tmp_path / ".snakemake" / "runs" / "trial"
    run.mkdir(parents=True)
    campaign = run / "campaign.json"
    profile = run / "execution-profile.json"
    campaign.touch()
    profile.touch()

    assert deployable_source_paths(campaign, profile, working_directory=tmp_path) == (
        ".snakemake/runs/trial/campaign.json",
        ".snakemake/runs/trial/execution-profile.json",
    )
    with pytest.raises(WorkflowInputError, match=r"must be below \.snakemake/runs"):
        deployable_source_paths(tmp_path / "campaign.json", profile, working_directory=tmp_path)
    other = tmp_path / ".snakemake" / "runs" / "other" / "execution-profile.json"
    other.parent.mkdir()
    other.touch()
    with pytest.raises(WorkflowInputError, match="share one frozen run directory"):
        deployable_source_paths(campaign, other, working_directory=tmp_path)


def test_remote_deployment_source_archive_is_complete_and_importable(tmp_path: Path) -> None:
    run_name = f"pytest-archive-{uuid4().hex}"
    run = ROOT / ".snakemake" / "runs" / run_name
    archive = tmp_path / "remote-sources.tar.xz"
    extracted = tmp_path / "extracted"
    run.mkdir(parents=True)
    campaign_path = run / "campaign.json"
    profile_path = run / "execution-profile.json"
    campaign_bytes = (
        json.dumps(_compiled_document(), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    execution = runnable_execution_profile()
    profile_bytes = canonical_profile_bytes(execution)
    campaign_path.write_bytes(campaign_bytes)
    profile_path.write_bytes(profile_bytes)
    campaign = load_campaign(campaign_path)
    exact_target = marker_path(
        campaign=campaign["campaign"],
        campaign_sha256=hashlib.sha256(campaign_bytes).hexdigest(),
        execution_sha256=hashlib.sha256(profile_bytes).hexdigest(),
        attempt=campaign["attempts"][0],
    )
    proposed_index = tmp_path / "git-index"
    index_path = subprocess.run(
        ["git", "rev-parse", "--git-path", "index"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    shutil.copy2(index_path, proposed_index)
    archive_env = {**os.environ, "GIT_INDEX_FILE": str(proposed_index)}
    subprocess.run(
        ["git", "add", "-u"], cwd=ROOT, env=archive_env, check=True, capture_output=True
    )
    try:
        completed = subprocess.run(
            [
                str(ROOT / "experiments/orchestration/snakemake/.venv/bin/python"),
                "-c",
                (
                    "import os; from pathlib import Path; "
                    "from google.cloud.storage import Bucket; "
                    "Bucket.exists=lambda self, *args, **kwargs: False; "
                    "from snakemake.cli import main; "
                    "from snakemake.workflow import Workflow; "
                    "capture=lambda self: ("
                    "self.write_source_archive("
                    "Path(os.environ['S3_STUDY_CAPTURE_SOURCE_ARCHIVE'])), "
                    "(_ for _ in ()).throw(SystemExit(0)))[1]; "
                    "Workflow.upload_sources=capture; "
                    "main()"
                ),
                "--snakefile",
                "experiments/orchestration/snakemake/Snakefile",
                exact_target,
                "--profile",
                "experiments/orchestration/snakemake/profiles/googlebatch",
                "--quiet",
            ],
            cwd=ROOT,
            env={
                **archive_env,
                "S3_STUDY_RUN_DIR": run.relative_to(ROOT).as_posix(),
                "S3_STUDY_CAPTURE_SOURCE_ARCHIVE": str(archive),
            },
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        with tarfile.open(archive, "r:xz") as frozen:
            campaign_member = f".snakemake/runs/{run_name}/campaign.json"
            profile_member = f".snakemake/runs/{run_name}/execution-profile.json"
            members = set(frozen.getnames())
            assert {
                campaign_member,
                profile_member,
                "experiments/orchestration/snakemake/Snakefile",
                "experiments/orchestration/snakemake/scripts/workflow.py",
                "experiments/orchestration/snakemake/scripts/run_attempt.py",
                "src/s3_listing_study/manager/campaign/request.py",
            } <= members
            assert frozen.extractfile(campaign_member).read() == campaign_bytes
            assert frozen.extractfile(profile_member).read() == profile_bytes
            assert not any(".venv" in Path(member).parts for member in members)
            assert {
                member for member in members if ".snakemake" in Path(member).parts
            } == {campaign_member, profile_member}
            frozen.extractall(extracted)

        imported = subprocess.run(
            [
                str(ROOT / "experiments/orchestration/snakemake/.venv/bin/python"),
                "-I",
                "-c",
                (
                    "import pathlib, sys; root=pathlib.Path(sys.argv[1]).resolve(); "
                    "scripts=root/'experiments/orchestration/snakemake/scripts'; "
                    "src=root/'src'; sys.path[:0]=[str(scripts), str(src)]; "
                    "import workflow; "
                    "import s3_listing_study.manager.campaign.request as request; "
                    "assert pathlib.Path(workflow.__file__).resolve().is_relative_to(root); "
                    "assert pathlib.Path(request.__file__).resolve().is_relative_to(root)"
                ),
                str(extracted),
            ],
            cwd=extracted,
            check=False,
            capture_output=True,
            text=True,
        )
        assert imported.returncode == 0, imported.stderr
    finally:
        shutil.rmtree(run)


def test_marker_namespace_contains_both_frozen_input_digests() -> None:
    selected = {
        "bucket": "noaa-rtma-pds",
        "tool": "aws-cli",
        "case_id": "s3api-v2-text",
        "run_ordinal": 1,
    }
    rendered = marker_path(
        campaign="2026-08-11-snake",
        campaign_sha256="a" * 64,
        execution_sha256="b" * 64,
        attempt=selected,
    )
    assert rendered == (
        "markers/2026-08-11-snake/"
        + "a" * 64
        + "/"
        + "b" * 64
        + "/noaa-rtma-pds/aws-cli/s3api-v2-text/run-1.json"
    )


@pytest.mark.parametrize("field", ("bucket", "tool", "case_id"))
def test_marker_path_refuses_traversal_in_every_coordinate(field: str) -> None:
    selected = {
        "bucket": "noaa-rtma-pds",
        "tool": "aws-cli",
        "case_id": "s3api-v2-text",
        "run_ordinal": 1,
    }
    selected[field] = "../escape"
    with pytest.raises(WorkflowInputError, match="path-safe"):
        marker_path(
            campaign="2026-08-11-snake",
            campaign_sha256="a" * 64,
            execution_sha256="b" * 64,
            attempt=selected,
        )


def _result_pointer_fixture() -> tuple[dict[str, object], dict[str, object]]:
    attempt_id = "12345678-1234-4234-9234-123456789abc"
    destination = (
        "gs://study-results/snakemake/evidence/2026-08-11-snake/results/"
        "noaa-rtma-pds/aws-cli/s3api-v2-text/run-1"
    )
    expected: dict[str, object] = {
        "destination": destination,
        "campaign_id": "2026-08-11-snake",
        "job_id": "c-abcdef012345-r1-s1",
        "case_id": "s3api-v2-text",
        "case_fingerprint": "a" * 64,
        "attempt_fingerprint": "b" * 64,
        "run_ordinal": 1,
        "submission_number": 1,
        "campaign_sha256": "c" * 64,
        "execution_sha256": "d" * 64,
    }
    document: dict[str, object] = {
        "schema_version": 3,
        "attempt_id": attempt_id,
        "campaign": {
            key: expected[key]
            for key in (
                "campaign_id",
                "job_id",
                "case_id",
                "case_fingerprint",
                "attempt_fingerprint",
                "run_ordinal",
                "submission_number",
            )
        },
        "artifact_uri": f"{destination}/{attempt_id}",
        "result_uri": f"{destination}/{attempt_id}/result.json",
    }
    return document, expected


def test_result_marker_is_exact_validated_worker_pointer(tmp_path: Path) -> None:
    document, expected = _result_pointer_fixture()
    result = tmp_path / "output" / "result.json"
    result.parent.mkdir()
    result.write_text(json.dumps(document), encoding="utf-8")
    marker = tmp_path / "marker.json"

    write_result_marker(marker, result, **expected)

    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "artifact_uri": document["artifact_uri"],
        "attempt_id": document["attempt_id"],
        "campaign_sha256": expected["campaign_sha256"],
        "execution_sha256": expected["execution_sha256"],
        "job_id": expected["job_id"],
        "kind": "s3-listing-study-result-pointer",
        "result_uri": document["result_uri"],
        "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
        "schema_version": 1,
    }


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("job_id", "c-other-r1-s1"),
        ("campaign_id", "2026-08-11-other"),
        ("case_id", "other-case"),
        ("case_fingerprint", "e" * 64),
        ("attempt_fingerprint", "f" * 64),
        ("run_ordinal", 2),
        ("submission_number", 2),
    ),
)
def test_mismatched_worker_result_never_writes_marker(
    tmp_path: Path, field: str, bad_value: object
) -> None:
    document, expected = _result_pointer_fixture()
    campaign = document["campaign"]
    assert isinstance(campaign, dict)
    campaign[field] = bad_value
    result = tmp_path / "result.json"
    result.write_text(json.dumps(document), encoding="utf-8")
    marker = tmp_path / "marker.json"

    with pytest.raises(ResultPointerError, match=field):
        write_result_marker(marker, result, **expected)
    assert not marker.exists()


@pytest.mark.parametrize(
    "failure", ("missing", "malformed", "bad-schema", "bad-uri", "bad-id", "non-v4-id")
)
def test_invalid_worker_result_never_writes_marker(tmp_path: Path, failure: str) -> None:
    document, expected = _result_pointer_fixture()
    result = tmp_path / "result.json"
    if failure == "malformed":
        result.write_text('{"campaign":', encoding="utf-8")
    elif failure == "bad-schema":
        document["schema_version"] = 99
        result.write_text(json.dumps(document), encoding="utf-8")
    elif failure == "bad-uri":
        document["result_uri"] = "gs://other/result.json"
        result.write_text(json.dumps(document), encoding="utf-8")
    elif failure == "bad-id":
        document["attempt_id"] = "not-a-uuid"
        result.write_text(json.dumps(document), encoding="utf-8")
    elif failure == "non-v4-id":
        document["attempt_id"] = "12345678-1234-1234-9234-123456789abc"
        result.write_text(json.dumps(document), encoding="utf-8")
    marker = tmp_path / "marker.json"

    with pytest.raises(ResultPointerError):
        write_result_marker(marker, result, **expected)
    assert not marker.exists()
