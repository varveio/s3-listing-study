"""Local executor refusals and schedule determinism that protect run evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark import local_campaign
from benchmark import plan as bench
from benchmark.ledger import Attempt, CampaignError
from benchmark.runtime.command_adapter import HEAP_PERCENT


def case(tool: str, *, reps: int = 3) -> bench.Case:
    return bench.Case(
        tool=tool,
        label="mode=listing",
        mode="listing",
        purpose="canary",
        statistic="timing",
        auth_role=None,
        resources=bench.Resources(
            vcpus=2,
            memory_gb=4,
            machine_type="local-fixture-2vcpu-4gb",
            container_memory_gb=4,
        ),
        reps=reps,
        timeout_s=60,
        heap_percent=HEAP_PERCENT,
        axes=(("mode", "listing"),),
        config=(("mode", "listing"),),
    )


def image() -> dict[str, str]:
    return {
        "image_uri": "local-docker@sha256:" + "a" * 64,
        "toolbox_manifest_sha256": "b" * 64,
        "toolbox_recipe_sha256": "c" * 64,
        "recipe_sha256": "d" * 64,
        "build_inputs_sha256": "e" * 64,
        "tool_version": "1",
        "tool_build_sha256": "f" * 64,
        "adapter_bundle_sha256": "1" * 64,
        "harness_revision": "2" * 40,
        "subject_workdir": "/",
    }


def attempt(tmp_path: Path, *, signed: bool = False) -> Attempt:
    return Attempt(
        case_id="aws-cli.123456789abc",
        attempt=1,
        case_inputs="{}",
        group_id="local-test",
        tool="aws-cli",
        auth_role="public-read" if signed else None,
        executor="local-docker",
        location="us-east1",
        machine_type="local-fixture-2vcpu-4gb",
        vcpus=2,
        memory_gb=4,
        container_memory_gb=4,
        heap_percent=HEAP_PERCENT,
        timeout_s=60,
        target_bucket="bucket",
        target_region="us-east-1",
        target_prefix="",
        config='{"mode":"listing"}',
        input_artifact_sha256=None,
        produced_by=None,
        tool_slice_sha256="3" * 64,
        platform_sha256="4" * 64,
        image_uri="local-docker@sha256:" + "a" * 64,
        image_set_sha256="5" * 64,
        executor_env="{}",
        service_account="local-environment" if signed else "anonymous",
        secret_resource=None,
        job_name="study-aws-cli-123456789abc-s1",
        result_prefix=str(tmp_path / "results/aws-cli.123456789abc.s1"),
        purpose="canary",
        statistic="timing",
        origin="planned",
    )


def test_randomized_schedule_is_seeded_and_complete_by_block() -> None:
    cases = (case("one"), case("two"), case("three"))
    first = local_campaign.randomized_blocks(cases, 982451653)
    second = local_campaign.randomized_blocks(cases, 982451653)
    assert [(item.block, item.case.tool) for item in first] == [
        (item.block, item.case.tool) for item in second
    ]
    assert [item.index for item in first] == list(range(1, 10))
    for block in range(1, 4):
        assert {item.case.tool for item in first if item.block == block} == {
            "one",
            "two",
            "three",
        }


def test_randomized_complete_blocks_refuse_unequal_repetition_counts() -> None:
    with pytest.raises(CampaignError, match="same reps"):
        local_campaign.randomized_blocks((case("one", reps=2), case("two", reps=3)), 1)


def test_cpuset_uses_only_whole_physical_cores() -> None:
    host = local_campaign.Host(
        allowed_cpus=(0, 1, 4, 5),
        physical_cores=((0, 4), (1, 5)),
        memory_gb=8,
        machine_family="local-fixture",
        document={},
    )
    assert host.cpuset(2) == "0,4"
    assert host.cpuset(4) == "0,4,1,5"
    with pytest.raises(CampaignError, match="whole physical cores"):
        host.cpuset(3)


def test_local_request_names_the_credential_without_recording_it(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    request = local_campaign._docker_request(
        attempt(root, signed=True),
        image(),
        image_id="sha256:" + "a" * 64,
        results_root=root,
        scratch=root / ".work/local-test/aws-cli.123456789abc.s1",
        cpuset="0,4",
        term_grace=5.0,
        schedule_index=1,
        block=1,
    )
    encoded = json.dumps(request)
    assert "--env=S3_STUDY_AWS_CREDENTIAL" in encoded
    assert "AWS_ACCESS_KEY_ID=" not in encoded
    assert request["schedule_index"] == 1
    assert request["block"] == 1


def test_ownership_handoff_refuses_a_path_outside_the_results_root(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(CampaignError, match="outside results root"):
        local_campaign._chown(
            "sha256:" + "a" * 64,
            results,
            (10001, 10001),
            (outside,),
            recursive=True,
        )
