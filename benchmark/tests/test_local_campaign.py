"""Evidence-protecting checks for the bounded local Docker campaign path."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from benchmark import campaign, docker_executor, ledger
from benchmark.plan import Plan

ROOT = Path(__file__).parents[2]
PLAN_PATH = ROOT / "benchmark/plans/examples/noaa-ghcn-pds.yaml"
SUITE = "s3-listing-study"


def test_host_memory_jitter_does_not_change_machine_or_case_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = {"bytes": 16 * 1024**3}
    case = next(case for case in Plan.load(PLAN_PATH).cases if case.purpose != "preparation")

    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: {0, 1})
    monkeypatch.setattr(
        os,
        "sysconf",
        lambda name: memory["bytes"] // 4096 if name == "SC_PHYS_PAGES" else 4096,
    )

    def read_text(path: Path, *_args: object, **_kwargs: object) -> str:
        rendered = str(path)
        if rendered.endswith("physical_package_id") or rendered.endswith("core_id"):
            return "0\n"
        if rendered == "/proc/cpuinfo":
            return "model name : Test CPU\n"
        if rendered == "/proc/sys/kernel/random/boot_id":
            return "test-boot\n"
        raise AssertionError(f"unexpected read: {rendered}")

    monkeypatch.setattr(Path, "read_text", read_text)

    def command(argv: object) -> subprocess.CompletedProcess[str]:
        tokens = cast(tuple[str, ...], argv)
        if tokens[0] == "findmnt":
            stdout = "/dev/test ext4\n"
        elif tokens[:2] == ("docker", "version"):
            stdout = "28.0.0\n"
        else:
            assert tokens[:2] == ("docker", "info")
            stdout = "overlay2\n"
        return subprocess.CompletedProcess(tokens, 0, stdout, "")

    monkeypatch.setattr(docker_executor, "_command", command)
    baseline = docker_executor.inspect_host(tmp_path)
    memory["bytes"] += 16 * 1024
    jittered = docker_executor.inspect_host(tmp_path)
    memory["bytes"] = 17 * 1024**3
    larger = docker_executor.inspect_host(tmp_path)

    assert baseline.facts["memory_bytes"] != jittered.facts["memory_bytes"]
    assert baseline.family == jittered.family
    assert baseline.family != larger.family

    def case_id(family: str) -> str:
        local = replace(case, resources=replace(case.resources, machine_type=family))
        return campaign.case_identity(
            local,
            auth_role=local.auth_role,
            target_bucket="example-bucket",
            target_region="us-east-1",
            location="local-host",
            tool_slice="a" * 64,
            platform="b" * 64,
        )[0]

    assert case_id(baseline.family) == case_id(jittered.family)
    assert case_id(baseline.family) != case_id(larger.family)


def _journal_attempt(
    con: sqlite3.Connection,
    *,
    case_id: str,
    group: str,
    executor: str,
    result_prefix: str | None = None,
) -> ledger.Attempt:
    attempt = ledger.Attempt(
        case_id=case_id,
        attempt=1,
        case_inputs=json.dumps({"case": case_id}),
        group_id=group,
        tool="aws-cli",
        auth_role=None,
        executor=executor,
        location="local-host",
        machine_type="test-machine",
        vcpus=1,
        memory_gb=1,
        container_memory_gb=None,
        heap_percent=80,
        timeout_s=60,
        target_bucket="example-bucket",
        target_region="us-east-1",
        target_prefix="",
        config='{"concurrency":1,"mode":"recursive"}',
        input_artifact_sha256=None,
        produced_by=None,
        tool_slice_sha256="a" * 64,
        platform_sha256="b" * 64,
        image_uri="docker@sha256:test",
        image_set_sha256="c" * 64,
        executor_env="{}",
        service_account="anonymous",
        secret_resource=None,
        job_name=f"job-{case_id}",
        result_prefix=result_prefix or f"/results/{case_id}",
        purpose="measurement",
        statistic="timing",
        origin="planned",
    )
    ledger.journal_intent(
        con,
        case_id=case_id,
        case_inputs=attempt.case_inputs,
        build=lambda _ordinal: (attempt, "{}"),
    )
    return attempt


def test_local_close_refuses_batch_and_preserves_terminal_docker_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "campaign.db"
    con = ledger.open_ledger(str(state), suite=SUITE)
    batch = _journal_attempt(con, case_id="batch-case", group="batch-group", executor="gcp-batch")
    running = _journal_attempt(con, case_id="running-case", group="docker-group", executor="docker")
    unstarted = _journal_attempt(
        con, case_id="unstarted-case", group="docker-group", executor="docker"
    )
    terminal = _journal_attempt(
        con, case_id="terminal-case", group="docker-group", executor="docker"
    )
    ledger.set_state(con, running.attempt_id, "RUNNING")
    ledger.set_state(con, terminal.attempt_id, "SUCCEEDED", "original detail")
    con.close()

    with pytest.raises(ledger.CampaignError, match="Docker groups only"):
        docker_executor.cmd_local_close(
            cast(
                argparse.Namespace,
                SimpleNamespace(state=str(state), group="batch-group", reason="host crashed"),
            )
        )

    con = ledger.open_ledger(str(state), readonly=True)
    assert (
        con.execute(
            "SELECT state FROM attempts WHERE attempt_id=?", (batch.attempt_id,)
        ).fetchone()["state"]
        == "SUBMITTING"
    )
    con.close()

    running_containers = {running.job_name}

    def command(argv: object) -> subprocess.CompletedProcess[str]:
        tokens = cast(tuple[str, ...], argv)
        assert tokens[:2] == ("docker", "ps")
        stdout = "".join(f"{name}\n" for name in sorted(running_containers))
        return subprocess.CompletedProcess(tokens, 0, stdout, "")

    monkeypatch.setattr(docker_executor, "_command", command)
    with pytest.raises(ledger.CampaignError, match=running.job_name):
        docker_executor.cmd_local_close(
            cast(
                argparse.Namespace,
                SimpleNamespace(state=str(state), group="docker-group", reason="host crashed"),
            )
        )
    running_containers.clear()

    docker_executor.cmd_local_close(
        cast(
            argparse.Namespace,
            SimpleNamespace(state=str(state), group="docker-group", reason="host crashed"),
        )
    )
    con = ledger.open_ledger(str(state), readonly=True)
    rows = {row["attempt_id"]: row for row in ledger.attempt_rows(con, group_id="docker-group")}
    assert (rows[running.attempt_id]["state"], rows[unstarted.attempt_id]["state"]) == (
        "FAILED",
        "NOT_CREATED",
    )
    assert "host crashed" in rows[running.attempt_id]["state_detail"]
    assert "host crashed" in rows[unstarted.attempt_id]["state_detail"]
    assert (rows[terminal.attempt_id]["state"], rows[terminal.attempt_id]["state_detail"]) == (
        "SUCCEEDED",
        "original detail",
    )
    con.close()


def test_local_close_refuses_to_fail_a_running_row_with_complete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "campaign.db"
    result = tmp_path / "results" / "attempt"
    result.mkdir(parents=True)
    (result / "result.json").write_text("{}")
    con = ledger.open_ledger(str(state), suite=SUITE)
    attempt = _journal_attempt(
        con,
        case_id="complete-case",
        group="docker-group",
        executor="docker",
        result_prefix=str(result),
    )
    ledger.set_state(con, attempt.attempt_id, "RUNNING")
    con.close()
    monkeypatch.setattr(
        docker_executor,
        "_command",
        lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
    )

    with pytest.raises(ledger.CampaignError, match="evidence is complete"):
        docker_executor.cmd_local_close(
            cast(
                argparse.Namespace,
                SimpleNamespace(state=str(state), group="docker-group", reason="host crashed"),
            )
        )

    con = ledger.open_ledger(str(state), readonly=True)
    row = ledger.attempt_rows(con, group_id="docker-group")[0]
    assert row["state"] == "RUNNING"
    con.close()

    docker_executor.cmd_local_close(
        cast(
            argparse.Namespace,
            SimpleNamespace(
                state=str(state), group="docker-group", reason="host crashed", settle_complete=True
            ),
        )
    )
    con = ledger.open_ledger(str(state), readonly=True)
    row = ledger.attempt_rows(con, group_id="docker-group")[0]
    assert row["state"] == "SUCCEEDED"
    assert "Docker exit unknown" in row["state_detail"]
    con.close()
