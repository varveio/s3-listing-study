"""Fault schedules and sidecars: how a plan states them, and what they refuse.

The worker's half — two child executions and a sidecar process — is exercised
end to end in ``test_measure.py``.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

import benchmark.plan as bench
from benchmark import campaign, faults, identity
from benchmark.contract import canonical_json
from benchmark.runtime import command_adapter as capsule

LISTING = {"listing": "listing.txt"}
LIST = capsule.Mode(
    product="text",
    fields=("key",),
    artifacts=LISTING,
    product_artifact="listing",
    product_channel="file",
    resume="resume",
    resume_evidence=("progress.txt",),
)
RESUME = capsule.Mode(
    product="text",
    fields=("key",),
    artifacts=LISTING,
    product_artifact="listing",
    product_channel="file",
)
PLAIN = capsule.Mode(product="text", fields=("key",), artifacts=LISTING, product_artifact="listing")
SIDECAR = {
    "name": "faultproxy",
    "executable_uri": "gs://bucket/tools/faultproxy",
    "executable_sha256": "c" * 64,
    "listen": "127.0.0.1:19091",
    "listen_flag": "-listen",
    "target_flag": "-target",
    "timeline_flag": "-timeline",
    "args": ["-seed", "7", "-window", "throttle:0s-130s:0.2"],
}
REPLAY = """\
replay:
  capacity_status: uncalibrated
  server_image_uri: example/replay-server@sha256:{image}
  fixture_sha256: {fixture}
  serving_mode: sorted
  latency_model: none
""".format(image="b" * 64, fixture="a" * 64)


def adapter(modes: dict[str, capsule.Mode]) -> capsule.LoadedCommandAdapter:
    def build(request: capsule.CommandRequest) -> tuple[str, ...]:
        return ("/bin/true", request.mode, request.endpoint_url)

    return capsule.LoadedCommandAdapter(
        build=build,
        fixed_command_prefix=("/bin/true",),
        config_keys=frozenset(),
        supports_unsigned=True,
        supports_signed=True,
        tool="swath",
        functional_env={},
        modes=modes,
    )


SWATH = adapter({"list": LIST, "resume": RESUME, "plain": PLAIN})


def load(tmp_path: Path, row: str, *, extra: str = "", defaults: str = "") -> bench.Plan:
    replay = "replay" in extra
    path = tmp_path / "b.yaml"
    path.write_text(
        f"spec_version: {3 if replay else 2}\n"
        "bucket: b\nregion: us-east-1\n"
        + extra
        + "defaults:\n  reps: 1\n  timeout_s: 600\n  vcpus: 4\n  memory_gb: 16\n"
        + (
            "  subject_vcpus: 1\n  replay_vcpus: 1\n  replay_memory_gb: 2\n"
            "  replay_parquet_connections: 8\n  replay_max_concurrent_requests: 8\n"
            "  replay_prefetch: false\n  replay_heap_percent: 75\n  container_memory_gb: 4\n"
            if replay
            else ""
        )
        + defaults
        + "tools:\n  swath:\n    cases:\n"
        + textwrap.indent(row, "      "),
        encoding="utf-8",
    )
    return bench.Plan.load(
        path,
        instances={(4, 16): "n4-standard-4"},
        heap=bench.HeapConfig(percent=75),
        adapters={"swath": SWATH},
        default_exclusions=(),
    )


def identity_of(case: bench.Case) -> tuple[str, str]:
    return campaign.case_identity(
        case,
        auth_role=None,
        target_bucket="b",
        target_region="us-east-1",
        location="us-central1",
        tool_slice="1" * 64,
        platform="2" * 64,
    )


def test_a_kill_point_is_plan_content_that_reaches_identity_and_the_worker(
    tmp_path: Path,
) -> None:
    """The schedule a row states is hashed, and the worker is told it from the hash input.

    A row with no schedule hashes exactly as it did before schedules existed,
    and a layer can state the expected wall a row's percentage is read against.
    """
    plain = load(tmp_path, "- {mode: list}\n").cases[0]
    killed = load(
        tmp_path,
        "- {mode: list, fault_kill_percent: 25}\n",
        defaults="  fault_expected_wall_s: 120\n",
    ).cases[0]
    assert plain.fault_schedule is None
    assert killed.fault_schedule == faults.FaultSchedule(kill_percent=25, expected_wall_s=120)
    assert killed.fault_schedule.kill_after_s == 30.0
    assert "fault_kill_percent-25" in killed.label

    plain_id, plain_inputs = identity_of(plain)
    killed_id, killed_inputs = identity_of(killed)
    assert "fault_schedule" not in json.loads(plain_inputs)
    assert plain_inputs == identity.case_inputs_document(
        json.loads(plain_inputs)["environment"],
        dict(plain.config),
        "1" * 64,
        "2" * 64,
    )
    assert killed_id != plain_id
    assert json.loads(killed_inputs)["fault_schedule"] == {
        "kind": "kill-resume",
        "signal": "SIGKILL",
        "kill_percent": 25,
        "expected_wall_s": 120,
    }
    assert faults.worker_flags(killed_inputs) == (
        ("--fault-schedule", canonical_json(killed.fault_schedule.as_dict())),
    )
    assert faults.worker_flags(plain_inputs) == ()


@pytest.mark.parametrize(
    ("row", "match"),
    (
        ("- {mode: list, fault_kill_at_s: 10, fault_kill_percent: 50}\n", "not both"),
        ("- {mode: list, fault_kill_percent: 50}\n", "stated together"),
        ("- {mode: list, fault_kill_percent: 100, fault_expected_wall_s: 60}\n", "1..99"),
        ("- {mode: list, fault_kill_at_s: 600}\n", "not inside its 600s timeout"),
        ("- {mode: plain, fault_kill_at_s: 10}\n", "declares no resume"),
        ("- {mode: list, purpose: preparation, fault_kill_at_s: 10}\n", "preparation"),
    ),
)
def test_a_kill_point_that_cannot_land_or_cannot_resume_is_refused(
    tmp_path: Path, row: str, match: str
) -> None:
    with pytest.raises(bench.PlanError, match=match):
        load(tmp_path, row)


def test_a_sidecar_is_plan_content_bound_to_every_case_and_its_identity(tmp_path: Path) -> None:
    block = "sidecar:\n" + textwrap.indent(json.dumps(SIDECAR), "  ") + "\n"
    plan = load(tmp_path, "- {mode: plain, purpose: diagnostic}\n", extra=REPLAY + block)
    case = plan.cases[0]
    assert plan.sidecar == case.sidecar == faults.make_sidecar(SIDECAR)
    assert case.sidecar is not None
    assert case.sidecar.endpoint_url == "http://127.0.0.1:19091"
    assert case.sidecar.argv("/x", "/t") == (
        "/x",
        "-seed",
        "7",
        "-window",
        "throttle:0s-130s:0.2",
        "-listen",
        "127.0.0.1:19091",
        "-target",
        faults.SIDECAR_TARGET_URL,
        "-timeline",
        "/t",
    )
    _case_id, inputs = identity_of(case)
    assert json.loads(inputs)["sidecar"] == SIDECAR
    assert faults.worker_flags(inputs) == (("--sidecar", canonical_json(SIDECAR)),)


@pytest.mark.parametrize(
    ("change", "match"),
    (
        ({"listen": "0.0.0.0:19091"}, "127.0.0.1"),
        ({"listen": "127.0.0.1:19090"}, "replay server's own"),
        ({"executable_uri": "gs://bucket/tools/*"}, "exact gs://"),
        ({"args": ["-listen=127.0.0.1:1"]}, "restate a flag"),
        ({"surprise": 1}, "unknown key"),
    ),
)
def test_a_sidecar_the_harness_cannot_stand_up_is_refused(
    tmp_path: Path, change: dict[str, object], match: str
) -> None:
    block = "sidecar:\n" + textwrap.indent(json.dumps({**SIDECAR, **change}), "  ") + "\n"
    with pytest.raises(bench.PlanError, match=match):
        load(tmp_path, "- {mode: plain, purpose: diagnostic}\n", extra=REPLAY + block)


def test_a_sidecar_without_a_replay_server_is_refused(tmp_path: Path) -> None:
    block = "sidecar:\n" + textwrap.indent(json.dumps(SIDECAR), "  ") + "\n"
    with pytest.raises(bench.PlanError, match="no \\[replay\\] block"):
        load(tmp_path, "- {mode: plain}\n", extra=block)


def test_a_worker_document_that_is_not_canonical_is_refused() -> None:
    schedule = faults.FaultSchedule(kill_at_s=30)
    assert faults.parse_schedule(canonical_json(schedule.as_dict())) == schedule
    with pytest.raises(faults.FaultError, match="canonical"):
        faults.parse_schedule(json.dumps(schedule.as_dict(), indent=1))
    with pytest.raises(faults.FaultError):
        faults.parse_sidecar(canonical_json({**SIDECAR, "args": None}))


@pytest.mark.parametrize(
    ("modes", "match"),
    (
        ({"list": replace(LIST, resume="absent")}, "unknown resume"),
        ({"list": LIST, "resume": PLAIN}, "same channel"),
        ({"list": LIST, "resume": replace(RESUME, resume="list")}, "own"),
    ),
)
def test_a_resume_that_does_not_continue_the_same_product_fails_to_load(
    modes: dict[str, capsule.Mode], match: str
) -> None:
    with pytest.raises(capsule.CommandAdapterError, match=match):
        capsule._check_resume(modes, {}, Path("command.py"))


def test_a_resume_cannot_join_two_stdout_streams() -> None:
    with pytest.raises(capsule.CommandAdapterError, match="stdout"):
        capsule.Mode(
            product="text",
            fields=("key",),
            artifacts=LISTING,
            product_artifact="listing",
            resume="resume",
        )


def test_a_result_whose_treatment_disagrees_with_the_hashed_case_is_refused() -> None:
    """The marker must carry the schedule and sidecar the row hashed, or it binds nothing.

    A row that stated neither binds exactly as before, so every historical
    result keeps binding.
    """
    from benchmark.verify import expected_result_binding

    schedule = faults.FaultSchedule(kill_at_s=30).as_dict()
    row = {
        "group_id": "g",
        "job_name": "j",
        "case_id": "swath.abc",
        "attempt_id": "swath.abc.s1",
        "tool": "swath",
        "mode": "list",
        "target_bucket": "b",
        "target_region": "r",
        "target_prefix": "",
        "auth_role": None,
        "image_uri": "i",
        "image_set_sha256": "s",
        "config": "{}",
        "replay": None,
        "machine_type": "m",
        "vcpus": 1,
        "memory_gb": 1,
        "container_memory_gb": None,
    }
    plain = expected_result_binding({**row, "case_inputs": canonical_json({"config": {}})})  # type: ignore[arg-type]
    assert "fault_schedule" not in plain and "sidecar" not in plain
    inputs = canonical_json({"config": {}, "fault_schedule": schedule, "sidecar": SIDECAR})
    bound = expected_result_binding({**row, "case_inputs": inputs})  # type: ignore[arg-type]
    assert bound["fault_schedule"] == schedule and bound["sidecar"] == SIDECAR
    result = {**bound, "fault_schedule": {**schedule, "kill_at_s": 31}}
    assert [name for name, value in bound.items() if result.get(name) != value] == [
        "fault_schedule"
    ]
