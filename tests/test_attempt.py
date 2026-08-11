"""Focused, offline contract tests for the single Python attempt engine."""

from __future__ import annotations

import builtins
import gzip
import json
import os
import signal
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from s3_listing_study.common.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    load_command_adapter,
)
from s3_listing_study.common.secret_scan import Outcome as ScanOutcome
from s3_listing_study.worker import (
    AttemptError,
    AttemptOptions,
    CampaignProvenance,
    DeclaredResources,
    cli,
    run_attempt,
)
from s3_listing_study.worker import engine as attempt_engine
from s3_listing_study.worker.driver import ResolvedInvocation, validate_request
from s3_listing_study.worker.driver import resolve_invocation as resolve_real_invocation

LOGICAL_ARGS = [
    "--tool",
    "aws-cli",
    "--operation",
    "list",
    "--mode",
    "s3api-v2-text",
    "--bucket",
    "bucket-x",
    "--region",
    "region-y",
]
ADAPTER_BUNDLE_SHA256 = "0" * 64
SHARED_BASE_IMAGE_DIGEST = "sha256:" + "1" * 64
SHARED_BASE_IMAGE_URI = "registry.example/study/base@" + SHARED_BASE_IMAGE_DIGEST
DERIVED_IMAGE_DIGEST = "sha256:" + "2" * 64
TOOL_IMAGE_DIGEST = "sha256:" + "3" * 64
TOOL_IMAGE_URI = "registry.example/study/tool@" + TOOL_IMAGE_DIGEST
LOGICAL_ARGS.extend(["--derived-image", DERIVED_IMAGE_DIGEST])
LOGICAL_ARGS.extend(["--shared-base-digest", SHARED_BASE_IMAGE_DIGEST])
LOGICAL_ARGS.extend(["--shared-base-uri", SHARED_BASE_IMAGE_URI])


def test_attempt_cli_is_strictly_logical_and_non_abbreviating() -> None:
    parsed = cli.build_parser().parse_args([*LOGICAL_ARGS, "--prefix=-leading/雪"])
    assert parsed.prefix == "-leading/雪"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([*LOGICAL_ARGS, "--mode", "duplicate"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--oper", "list", *LOGICAL_ARGS])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([*LOGICAL_ARGS, "--", "/bin/echo"])


def test_attempt_cli_emits_and_accepts_only_request_schema_two() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(LOGICAL_ARGS).request_schema == "2"
    assert parser.parse_args([*LOGICAL_ARGS, "--request-schema", "2"]).request_schema == "2"
    with pytest.raises(SystemExit):
        parser.parse_args([*LOGICAL_ARGS, "--request-schema", "1"])


def test_attempt_cli_forwards_only_explicit_managed_runtime_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[AttemptOptions] = []

    monkeypatch.setattr(
        cli,
        "resolve_invocation",
        lambda _request: ResolvedInvocation(
            _python("pass"),
            ADAPTER_BUNDLE_SHA256,
            {"MC_HOST_s3": "https://s3.amazonaws.com"},
        ),
    )
    monkeypatch.setattr(cli, "_adapter_path", lambda _tool: None)

    def fake_run(options: AttemptOptions) -> tuple[dict[str, object], int]:
        observed.append(options)
        return {}, 0

    monkeypatch.setattr(cli, "run_attempt", fake_run)
    assert (
        cli.main(
            [
                "--output",
                str(tmp_path / "attempt"),
                *LOGICAL_ARGS,
                "--case-env",
                "JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=75",
                "--case-env",
                "NODE_OPTIONS=--max-old-space-size=1536",
            ]
        )
        == 0
    )
    assert observed[0].functional_env == {
        "MC_HOST_s3": "https://s3.amazonaws.com",
        "JAVA_TOOL_OPTIONS": "-XX:MaxRAMPercentage=75",
        "NODE_OPTIONS": "--max-old-space-size=1536",
    }


def test_attempt_cli_records_forwarded_managed_runtime_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "resolve_invocation",
        lambda _request: ResolvedInvocation(
            _python("import os; assert os.environ['NODE_OPTIONS'] == '--max-old-space-size=1536'"),
            ADAPTER_BUNDLE_SHA256,
        ),
    )
    monkeypatch.setattr(cli, "_adapter_path", lambda _tool: None)
    output = tmp_path / "attempt"
    assert (
        cli.main(
            [
                "--output",
                str(output),
                *LOGICAL_ARGS,
                "--case-env",
                "NODE_OPTIONS=--max-old-space-size=1536",
            ]
        )
        == 0
    )
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["invocation"]["environment"]["NODE_OPTIONS"] == "--max-old-space-size=1536"


@pytest.mark.parametrize(
    ("values", "capsule", "message"),
    [
        (["OTHER=value"], {}, "key must be one of"),
        (["JAVA_TOOL_OPTIONS=a", "JAVA_TOOL_OPTIONS=b"], {}, "repeats"),
        (["JAVA_TOOL_OPTIONS"], {}, "NAME=VALUE"),
        (["NODE_OPTIONS="], {}, "must not be empty"),
        (["NODE_OPTIONS=a\x00b"], {}, "without NUL"),
        (["NODE_OPTIONS=case"], {"NODE_OPTIONS": "capsule"}, "collides"),
    ],
)
def test_attempt_cli_refuses_invalid_case_environment(
    values: list[str], capsule: dict[str, str], message: str
) -> None:
    with pytest.raises(CommandAdapterError, match=message):
        cli._parse_case_env(values, capsule)


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--timeout", "nan"),
        ("--timeout", "inf"),
        ("--timeout", "-inf"),
        ("--timeout", "0"),
        ("--timeout", "-1"),
        ("--term-grace", "nan"),
        ("--term-grace", "inf"),
        ("--term-grace", "-inf"),
        ("--term-grace", "-0.1"),
    ],
)
def test_attempt_cli_rejects_invalid_numeric_limits_before_output_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: str,
) -> None:
    output = tmp_path / "attempt"

    def must_not_resolve(_request: CommandRequest) -> ResolvedInvocation:
        raise AssertionError("command resolution must follow numeric validation")

    monkeypatch.setattr(cli, "resolve_invocation", must_not_resolve)
    numeric_argument = [f"{option}={value}"] if value.startswith("-") else [option, value]
    assert cli.main(["--output", str(output), *LOGICAL_ARGS, *numeric_argument]) == 2
    assert not output.exists()


@pytest.mark.parametrize("value", ["-1", "+1", "1.0", "NaN", "\uff11"])
def test_attempt_cli_rejects_non_ascii_concurrency_before_output_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    output = tmp_path / "attempt"
    monkeypatch.setattr(
        cli,
        "resolve_invocation",
        lambda _request: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )
    assert cli.main(["--output", str(output), *LOGICAL_ARGS, "--concurrency", value]) == 2
    assert not output.exists()


def test_attempt_cli_passes_typed_concurrency_and_records_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[CommandRequest] = []

    def resolve(request: CommandRequest) -> ResolvedInvocation:
        observed.append(request)
        return ResolvedInvocation(_python("pass"), ADAPTER_BUNDLE_SHA256)

    monkeypatch.setattr(cli, "resolve_invocation", resolve)
    monkeypatch.setattr(cli, "_adapter_path", lambda _tool: None)
    output = tmp_path / "attempt"
    logical_args = list(LOGICAL_ARGS)
    logical_args[logical_args.index("--tool") + 1] = "s4cmd"
    assert (
        cli.main(
            [
                "--output",
                str(output),
                *logical_args,
                "--concurrency",
                "8",
            ]
        )
        == 0
    )
    assert observed[0].concurrency == 8
    assert _result(output)["logical_request"]["concurrency"] == 8  # type: ignore[index]


@pytest.mark.parametrize(("tool", "value"), [("s4cmd", "0"), ("s4cmd", "9"), ("aws-cli", "1")])
def test_attempt_cli_reports_adapter_concurrency_rejection_without_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    value: str,
) -> None:
    root = Path(__file__).resolve().parents[1]

    def resolve(request: CommandRequest) -> ResolvedInvocation:
        adapter = load_command_adapter(root / "tools" / tool / "adapter" / "command.py")
        return ResolvedInvocation(adapter.compile(request), ADAPTER_BUNDLE_SHA256)

    monkeypatch.setattr(cli, "resolve_invocation", resolve)
    logical_args = list(LOGICAL_ARGS)
    logical_args[logical_args.index("--tool") + 1] = tool
    output = tmp_path / "attempt"
    assert cli.main(["--output", str(output), *logical_args, "--concurrency", value]) == 2
    assert not output.exists()


def test_logical_prefix_validation_preserves_unicode_and_bounds_bytes() -> None:
    validate_request(CommandRequest("mode", "bucket", "region", "雪/control\n", tool="aws-cli"))
    with pytest.raises(CommandAdapterError, match="1,024-byte"):
        validate_request(CommandRequest("mode", "bucket", "region", "雪" * 342, tool="aws-cli"))
    with pytest.raises(CommandAdapterError, match="NUL"):
        validate_request(CommandRequest("mode", "bucket", "region", "bad\x00", tool="aws-cli"))


def _python(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


def _run(tmp_path: Path, source: str, **changes: object) -> tuple[dict[str, object], int]:
    source_env = cast(Mapping[str, str] | None, changes.pop("source_env", None))
    values: dict[str, object] = {
        "output": tmp_path / "attempt",
        "argv": _python(source),
        "timeout_s": 2.0,
        "adapter_bundle_sha256": ADAPTER_BUNDLE_SHA256,
        "shared_base_digest": SHARED_BASE_IMAGE_DIGEST,
        "shared_base_uri": SHARED_BASE_IMAGE_URI,
        "derived_image": DERIVED_IMAGE_DIGEST,
        "term_grace_s": 0.1,
        "attempt_id": "test-attempt",
        "tool": "synthetic",
    }
    values.update(changes)
    return run_attempt(AttemptOptions(**values), source_env=source_env)  # type: ignore[arg-type]


def _result(output: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads((output / "result.json").read_bytes()))


def _outcome(result: dict[str, object]) -> dict[str, object]:
    return result["outcome"]  # type: ignore[return-value]


def test_success_records_direct_argv_and_binary_streams(tmp_path: Path) -> None:
    output = tmp_path / "attempt"
    source = "import os; os.write(1, b'out\\x00\\xff'); os.write(2, b'err\\x80')"
    result, runner_exit = _run(tmp_path, source)

    assert runner_exit == 0
    assert _outcome(result) == {
        "status": "completed",
        "exit_code": 0,
        "signal": None,
        "timed_out": False,
        "cleanup": {
            "state": "not_needed",
            "term_sent": False,
            "kill_sent": False,
            "process_group_empty": True,
            "escaped_descendants": [],
        },
    }
    assert gzip.decompress((output / "stdout.raw.gz").read_bytes()) == b"out\x00\xff"
    assert gzip.decompress((output / "stderr.raw.gz").read_bytes()) == b"err\x80"
    assert result["invocation"]["argv"] == list(_python(source))  # type: ignore[index]
    assert result["secret_scan"] == {
        "status": "clean",
        "streams": {"stdout": "clean", "stderr": "clean"},
    }


def test_engine_preserves_validated_tool_image_provenance(tmp_path: Path) -> None:
    result, runner_exit = _run(
        tmp_path,
        "pass",
        tool_image_digest=TOOL_IMAGE_DIGEST,
        tool_image_uri=TOOL_IMAGE_URI,
        selection_sha256="4" * 64,
    )

    assert runner_exit == 0
    assert result["images"]["tool"] == {  # type: ignore[index]
        "digest": TOOL_IMAGE_DIGEST,
        "uri": TOOL_IMAGE_URI,
    }
    assert result["build_inputs"]["tool"]["selection_sha256"] == "4" * 64  # type: ignore[index]


def test_subject_runs_in_registered_working_directory(tmp_path: Path) -> None:
    workdir = tmp_path / "subject-workdir"
    workdir.mkdir()
    result, runner_exit = _run(
        tmp_path,
        "import os; print(os.getcwd())",
        subject_workdir=str(workdir),
    )

    assert runner_exit == 0
    assert gzip.decompress((tmp_path / "attempt/stdout.raw.gz").read_bytes()) == (
        f"{workdir}\n".encode()
    )
    assert result["invocation"]["working_directory"] == str(workdir)  # type: ignore[index]


def test_worker_mints_a_distinct_uuid_for_each_container_execution(tmp_path: Path) -> None:
    results = []
    for ordinal in (1, 2):
        result, runner_exit = _run(
            tmp_path,
            "pass",
            output=tmp_path / f"attempt-{ordinal}",
            attempt_id="",
        )
        assert runner_exit == 0
        uuid.UUID(cast(str, result["attempt_id"]))
        results.append(result["attempt_id"])
    assert len(set(results)) == 2


def test_completed_attempt_counts_native_rows_without_normalizing(tmp_path: Path) -> None:
    payload = (
        b'a.txt\t1\t"aa"\t2026-03-16T14:41:50+00:00\tSTANDARD\n'
        b'b.txt\t2\t"bb"\t2026-03-16T14:41:51+00:00\tSTANDARD\n'
    )
    sink = tmp_path / "sink"
    (sink / "dataset").mkdir(parents=True)
    (sink / "dataset" / "part-0.native").write_bytes(b"original native rows")
    adapter = tmp_path / "normalize.py"
    adapter.write_text(
        f"""
from pathlib import Path
from s3_listing_study.common.duckdb_adapter import staged

def normalize(*args, **kwargs):
    raise AssertionError("worker must not normalize routine attempts")

def count_rows(data, mode, prefix='', native_root=''):
    assert type(data).__name__ == "PathInput"
    assert mode == "native-mode"
    assert prefix == "requested/prefix"
    assert native_root == {str(sink)!r}
    assert Path(native_root, "dataset", "part-0.native").read_bytes() == b"original native rows"
    with staged(data) as path:
        assert Path(path).read_bytes() == {payload!r}
    return 2
"""
    )
    source = f"import os; os.write(1, {payload!r})"
    result, runner_exit = _run(
        tmp_path,
        source,
        mode="native-mode",
        prefix="requested/prefix",
        sink_dir=str(sink),
        adapter_path=adapter,
    )
    assert runner_exit == 0
    assert result["summary"]["schema_version"] == 2  # type: ignore[index]
    assert result["summary"]["status"] == "counted"  # type: ignore[index]
    assert result["summary"]["row_count"] == 2  # type: ignore[index]
    assert result["summary"]["duckdb_version"] == "1.5.5"  # type: ignore[index]
    assert gzip.decompress((tmp_path / "attempt/stdout.raw.gz").read_bytes()) == payload
    assert (tmp_path / "attempt/native/dataset/part-0.native").read_bytes() == (
        b"original native rows"
    )


def test_summary_failure_keeps_raw_evidence_and_has_distinct_exit(tmp_path: Path) -> None:
    adapter = tmp_path / "normalize.py"
    secret = "customer/key/that/must/not/enter/result.json"
    adapter.write_text(
        f"def count_rows(data, mode, prefix='', native_root=''):\n"
        f"    raise RuntimeError({secret!r})\n"
    )
    result, runner_exit = _run(
        tmp_path,
        "print('retained raw evidence')",
        mode="synthetic",
        adapter_path=adapter,
    )
    assert runner_exit == cli.POST_ATTEMPT_EXIT
    assert _outcome(result)["status"] == "completed"
    assert result["summary"]["schema_version"] == 2  # type: ignore[index]
    assert result["summary"]["status"] == "error"  # type: ignore[index]
    assert result["summary"]["row_count"] is None  # type: ignore[index]
    assert result["summary"]["error"] == {  # type: ignore[index]
        "code": "row_count_failed",
        "type": "RuntimeError",
    }
    assert secret not in (tmp_path / "attempt/result.json").read_text()
    assert (tmp_path / "attempt/result.json").is_file()
    assert gzip.decompress((tmp_path / "attempt/stdout.raw.gz").read_bytes())


def test_worker_requires_count_rows_and_never_falls_back_to_normalize(tmp_path: Path) -> None:
    adapter = tmp_path / "normalize.py"
    marker = tmp_path / "normalize-was-called"
    adapter.write_text(
        "from pathlib import Path\n"
        "def normalize(*args, **kwargs):\n"
        f"    Path({str(marker)!r}).touch()\n"
        "    return 0\n"
    )
    result, runner_exit = _run(
        tmp_path,
        "print('retained original')",
        mode="synthetic",
        adapter_path=adapter,
    )

    assert runner_exit == cli.POST_ATTEMPT_EXIT
    assert result["summary"]["status"] == "error"  # type: ignore[index]
    assert result["summary"]["error"] == {  # type: ignore[index]
        "code": "row_count_failed",
        "type": "SummaryError",
    }
    assert not marker.exists()
    assert gzip.decompress((tmp_path / "attempt/stdout.raw.gz").read_bytes()) == (
        b"retained original\n"
    )


def test_failed_tool_skips_summary_without_opening_adapter(tmp_path: Path) -> None:
    result, runner_exit = _run(
        tmp_path,
        "print('partial'); raise SystemExit(4)",
        mode="any",
        adapter_path=tmp_path / "does-not-exist.py",
    )
    assert runner_exit == 0
    assert result["summary"]["schema_version"] == 2  # type: ignore[index]
    assert result["summary"]["status"] == "skipped"  # type: ignore[index]
    assert result["summary"]["reason"] == "tool_outcome_failed"  # type: ignore[index]
    assert result["summary"]["row_count"] is None  # type: ignore[index]


def test_campaign_provenance_and_batch_retry_are_recorded_but_not_forwarded(
    tmp_path: Path,
) -> None:
    campaign = CampaignProvenance(
        campaign_id="2026-08-10-first",
        job_id="c-one-r1-s1",
        case_id="case.one",
        case_fingerprint="a" * 64,
        attempt_fingerprint="b" * 64,
        run_ordinal=1,
        submission_number=1,
        resources=DeclaredResources("n4-highcpu-2", 2, 4, None),
    )
    result, runner_exit = _run(
        tmp_path,
        "pass",
        campaign=campaign,
        results_destination="gs://results/campaigns/2026-08-10-first/bucket/tool/case/run-1",
        source_env={"BATCH_TASK_RETRY_ATTEMPT": "0"},
    )
    assert runner_exit == 0
    assert result["campaign"] == {
        "campaign_id": campaign.campaign_id,
        "job_id": campaign.job_id,
        "case_id": campaign.case_id,
        "case_fingerprint": campaign.case_fingerprint,
        "attempt_fingerprint": campaign.attempt_fingerprint,
        "run_ordinal": 1,
        "submission_number": 1,
        "declared_resources": {
            "machine_type": "n4-highcpu-2",
            "vcpus": 2,
            "memory_gb": 4,
            "container_memory_gb": None,
        },
    }
    assert result["scheduler"] == {"batch_task_retry_attempt": 0}
    assert "BATCH_TASK_RETRY_ATTEMPT" not in result["invocation"]["environment"]  # type: ignore[index]
    assert str(result["artifact_uri"]).endswith("/test-attempt")
    assert str(result["result_uri"]).endswith("/test-attempt/result.json")


def test_cli_campaign_provenance_is_all_or_none_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "resolve_invocation",
        lambda _request: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )
    output = tmp_path / "attempt"
    assert (
        cli.main(
            [
                "--output",
                str(output),
                *LOGICAL_ARGS,
                "--campaign-id",
                "2026-08-10-first",
            ]
        )
        == 2
    )
    assert not output.exists()


def test_cgroup_v2_before_after_and_oom_deltas_are_captured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "memory.current").write_text("100\n")
    (cgroup / "memory.peak").write_text("200\n")
    (cgroup / "memory.max").write_text("4096\n")
    (cgroup / "memory.events").write_text("oom 1\noom_kill 0\n")
    monkeypatch.setattr(attempt_engine, "_cgroup_v2_directory", lambda: cgroup)
    source = (
        "from pathlib import Path; "
        f"p=Path({str(cgroup)!r}); "
        "(p/'memory.current').write_text('150\\n'); "
        "(p/'memory.peak').write_text('300\\n'); "
        "(p/'memory.events').write_text('oom 3\\noom_kill 1\\n')"
    )
    result, _runner_exit = _run(tmp_path, source)
    memory = result["resources"]["cgroup_v2_memory"]  # type: ignore[index]
    assert memory["before"]["memory_current_bytes"] == 100
    assert memory["after"]["memory_peak_bytes"] == 300
    assert (memory["oom_delta"], memory["oom_kill_delta"]) == (2, 1)
    assert result["platform"]["cgroup_v2_memory_limit_bytes"] == 4096  # type: ignore[index]
    assert result["timing"]["started_at_utc"].endswith("Z")  # type: ignore[index]
    assert result["timing"]["ended_at_utc"].endswith("Z")  # type: ignore[index]


def test_tool_nonzero_is_a_recorded_outcome_and_cli_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "attempt"
    monkeypatch.setattr(
        cli,
        "resolve_invocation",
        lambda _request: ResolvedInvocation(_python("raise SystemExit(23)"), ADAPTER_BUNDLE_SHA256),
    )
    runner_exit = cli.main(
        [
            "--output",
            str(output),
            "--timeout",
            "2",
            "--derived-image",
            DERIVED_IMAGE_DIGEST,
            "--shared-base-digest",
            SHARED_BASE_IMAGE_DIGEST,
            "--shared-base-uri",
            SHARED_BASE_IMAGE_URI,
            "--tool",
            "fixture-tool",
            "--operation",
            "list",
            "--mode",
            "s3api-v2-text",
            "--bucket",
            "public-bucket",
            "--region",
            "us-east-1",
            "--prefix",
            "objects/",
            "--scope",
            "prefix",
        ]
    )

    assert runner_exit == 0
    assert _outcome(_result(output)) == {
        "status": "failed",
        "exit_code": 23,
        "signal": None,
        "timed_out": False,
        "cleanup": {
            "state": "not_needed",
            "term_sent": False,
            "kill_sent": False,
            "process_group_empty": True,
            "escaped_descendants": [],
        },
    }
    assert _result(output)["target"] == {
        "mode": "s3api-v2-text",
        "bucket": "public-bucket",
        "region": "us-east-1",
        "prefix": "objects/",
        "scope": "prefix",
    }
    assert _result(output)["logical_request"] == {
        "schema_version": 1,
        "operation": "list",
        "mode": "s3api-v2-text",
        "bucket": "public-bucket",
        "region": "us-east-1",
        "prefix": "objects/",
        "authentication": "anonymous",
        "concurrency": None,
    }
    assert _result(output)["adapter_bundle_sha256"] == ADAPTER_BUNDLE_SHA256
    assert _result(output)["images"] == {
        "derived": DERIVED_IMAGE_DIGEST,
        "tool": {
            "digest": "sha256:" + "0" * 64,
            "uri": "local/tool@sha256:" + "0" * 64,
        },
        "shared_base": {
            "digest": SHARED_BASE_IMAGE_DIGEST,
            "uri": SHARED_BASE_IMAGE_URI,
        },
    }
    build_inputs = cast(Mapping[str, object], _result(output)["build_inputs"])
    shared_base = cast(Mapping[str, object], build_inputs["shared_base"])
    assert shared_base["source_sha256"] == "0" * 64


def test_signal_is_not_misreported_as_an_exit_code(tmp_path: Path) -> None:
    result, runner_exit = _run(
        tmp_path,
        "import os, signal; os.kill(os.getpid(), signal.SIGUSR1)",
    )

    assert runner_exit == 0
    outcome = _outcome(result)
    assert outcome["status"] == "signaled"
    assert outcome["exit_code"] is None
    assert outcome["signal"] == signal.SIGUSR1
    assert outcome["timed_out"] is False


def test_timeout_kills_and_reaps_the_subject_process_group(tmp_path: Path) -> None:
    source = """
import os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([
    sys.executable, "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
])
print(child.pid, flush=True)
time.sleep(60)
"""
    result, runner_exit = _run(tmp_path, source, timeout_s=0.5, term_grace_s=0.05)

    assert runner_exit == 0
    outcome = _outcome(result)
    assert outcome["status"] == "timed_out"
    assert outcome["timed_out"] is True
    assert outcome["cleanup"] == {
        "state": "killed",
        "term_sent": True,
        "kill_sent": True,
        "process_group_empty": True,
        "escaped_descendants": [],
    }
    child_pid = int(gzip.decompress((tmp_path / "attempt" / "stdout.raw.gz").read_bytes()).strip())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_success_waits_for_an_adopted_same_group_child(tmp_path: Path) -> None:
    source = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.1)"])
print(child.pid, flush=True)
"""
    result, runner_exit = _run(tmp_path, source)

    assert runner_exit == 0
    assert _outcome(result)["status"] == "completed"
    assert _outcome(result)["cleanup"]["escaped_descendants"] == []  # type: ignore[index]
    child_pid = int(gzip.decompress((tmp_path / "attempt" / "stdout.raw.gz").read_bytes()).strip())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_indeterminate_adopted_child_inspection_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid_file = tmp_path / "child.pid"
    source = f"""
import pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))
"""

    inspect_process = attempt_engine._process_facts

    def fail_inspection(pid: int) -> tuple[str, int, int] | None:
        facts = inspect_process(pid)
        if facts is not None and facts[1] != facts[2]:
            raise AttemptError("indeterminate inspection")
        return facts

    monkeypatch.setattr(attempt_engine, "_process_facts", fail_inspection)
    with pytest.raises(AttemptError, match="indeterminate inspection"):
        _run(tmp_path, source)

    child_pid = int(child_pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_gzip_bytes_are_deterministic(tmp_path: Path) -> None:
    archives: list[bytes] = []
    for index in range(2):
        output = tmp_path / f"attempt-{index}"
        _run(
            tmp_path,
            "import os; os.write(1, b'repeated payload\\n' * 1000)",
            output=output,
            attempt_id=f"attempt-{index}",
        )
        archives.append((output / "stdout.raw.gz").read_bytes())

    assert archives[0] == archives[1]


def test_completed_attempt_has_exactly_three_artifacts_and_result_is_last(
    tmp_path: Path,
) -> None:
    output = tmp_path / "attempt"

    def before_finalization() -> None:
        assert list(output.iterdir()) == []
        assert not (output / "result.json").exists()

    run_attempt(
        AttemptOptions(
            output=output,
            argv=_python("pass"),
            timeout_s=2,
            adapter_bundle_sha256=ADAPTER_BUNDLE_SHA256,
            shared_base_digest=SHARED_BASE_IMAGE_DIGEST,
            shared_base_uri=SHARED_BASE_IMAGE_URI,
            derived_image=DERIVED_IMAGE_DIGEST,
        ),
        post_measure_hook=before_finalization,
    )

    assert {entry.name for entry in output.iterdir()} == {
        "result.json",
        "stdout.raw.gz",
        "stderr.raw.gz",
    }
    assert _result(output)["schema_version"] == 3


def test_post_measure_delay_is_excluded_from_elapsed_time(tmp_path: Path) -> None:
    delay_s = 0.25
    result, _runner_exit = run_attempt(
        AttemptOptions(
            output=tmp_path / "attempt",
            argv=_python("pass"),
            timeout_s=2,
            adapter_bundle_sha256=ADAPTER_BUNDLE_SHA256,
            shared_base_digest=SHARED_BASE_IMAGE_DIGEST,
            shared_base_uri=SHARED_BASE_IMAGE_URI,
            derived_image=DERIVED_IMAGE_DIGEST,
        ),
        post_measure_hook=lambda: time.sleep(delay_s),
    )

    elapsed_ns = result["timing"]["elapsed_ns"]  # type: ignore[index]
    assert isinstance(elapsed_ns, int)
    assert elapsed_ns < delay_s * 1_000_000_000


def test_adapter_resolution_and_measurement_do_not_import_normalizer_or_duckdb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    normalizer = (root / "tools/aws-cli/adapter/normalize.py").resolve()
    imported_duckdb: list[str] = []
    before_normalizer_modules = {
        name
        for name, module in sys.modules.items()
        if Path(str(getattr(module, "__file__", ""))).resolve() == normalizer
    }
    original_import = builtins.__import__

    def observe_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if name == "duckdb" or name.startswith("duckdb."):
            imported_duckdb.append(name)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", observe_import)
    invocation = resolve_real_invocation(
        CommandRequest(
            "s3api-v2-text",
            "bucket-x",
            "region-y",
            tool="aws-cli",
        ),
        selection_path=root / "tools/aws-cli/build/image.json",
        adapter_dir=root / "tools/aws-cli/adapter",
    )

    def elapsed_boundary() -> None:
        after_normalizer_modules = {
            name
            for name, module in sys.modules.items()
            if Path(str(getattr(module, "__file__", ""))).resolve() == normalizer
        }
        assert after_normalizer_modules == before_normalizer_modules
        assert imported_duckdb == []

    run_attempt(
        AttemptOptions(
            output=tmp_path / "attempt",
            argv=_python("pass"),
            timeout_s=2,
            adapter_bundle_sha256=invocation.adapter_bundle_sha256,
            shared_base_digest=SHARED_BASE_IMAGE_DIGEST,
            shared_base_uri=SHARED_BASE_IMAGE_URI,
            derived_image=DERIVED_IMAGE_DIGEST,
        ),
        post_measure_hook=elapsed_boundary,
    )


def test_anonymous_child_environment_is_a_strict_recorded_allowlist(tmp_path: Path) -> None:
    names = [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_ENDPOINT_URL",
        "HTTPS_PROXY",
        "LD_PRELOAD",
        "REQUESTS_CA_BUNDLE",
        "PYTHONPATH",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "ARBITRARY_RUNNER_VALUE",
    ]
    source_env = dict.fromkeys(names, "must-not-reach-child")
    source_env["PATH"] = "/ambient/bin"
    source_env["HOME"] = "/ambient/home"
    source_env["LANG"] = "ambient-locale"
    source = "import json,os; print(json.dumps(dict(os.environ), sort_keys=True))"
    output = tmp_path / "attempt"
    run_attempt(
        AttemptOptions(
            output=output,
            argv=_python(source),
            timeout_s=2,
            adapter_bundle_sha256=ADAPTER_BUNDLE_SHA256,
            shared_base_digest=SHARED_BASE_IMAGE_DIGEST,
            shared_base_uri=SHARED_BASE_IMAGE_URI,
            derived_image=DERIVED_IMAGE_DIGEST,
        ),
        source_env=source_env,
    )
    observed = json.loads(gzip.decompress((output / "stdout.raw.gz").read_bytes()))

    expected = {
        "AWS_EC2_METADATA_DISABLED": "true",
        "HOME": "/home/s3study",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    assert observed == expected
    result_env = _result(output)["invocation"]["environment"]  # type: ignore[index]
    assert result_env == expected


def test_native_sink_is_published_with_its_layout(tmp_path: Path) -> None:
    sink = tmp_path / "sink"
    (sink / "data").mkdir(parents=True)
    (sink / "data" / "part-0.bin").write_bytes(b"rows")
    (sink / "_SUCCESS").write_bytes(b"")
    result, runner_exit = _run(tmp_path, "pass", sink_dir=str(sink))

    assert runner_exit == 0
    assert result["native_refusal"] is None
    native = cast(list[dict[str, object]], result["native_output"])
    assert [entry["path"] for entry in native] == ["native/_SUCCESS", "native/data/part-0.bin"]
    assert (tmp_path / "attempt" / "native" / "data" / "part-0.bin").read_bytes() == b"rows"


def test_flagged_native_sink_fails_closed_without_artifacts(tmp_path: Path) -> None:
    """The sink is planned before the streams, so a refusal publishes nothing."""
    sink = tmp_path / "sink"
    sink.mkdir()
    (sink / "part.bin").write_bytes(b"A" + b"KIA" + b"Q" * 16)
    with pytest.raises(AttemptError, match="secret scan did not clear native"):
        _run(tmp_path, "pass", sink_dir=str(sink))
    assert list((tmp_path / "attempt").iterdir()) == []


def test_symlinked_sink_directory_is_refused(tmp_path: Path) -> None:
    sink = tmp_path / "sink"
    (sink / "data").mkdir(parents=True)
    (sink / "link").symlink_to(sink / "data")
    with pytest.raises(AttemptError, match="symlinked directory"):
        _run(tmp_path, "pass", sink_dir=str(sink))
    assert list((tmp_path / "attempt").iterdir()) == []


def test_native_sink_caps_stop_the_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = tmp_path / "sink"
    sink.mkdir()
    for index in range(4):
        (sink / f"part-{index}.bin").write_bytes(b"rows")
    monkeypatch.setattr(attempt_engine, "NATIVE_MAX_FILES", 2)
    with pytest.raises(AttemptError, match="more than 2 files"):
        _run(tmp_path, "pass", sink_dir=str(sink))
    monkeypatch.setattr(attempt_engine, "NATIVE_MAX_FILES", 4096)
    monkeypatch.setattr(attempt_engine, "NATIVE_MAX_BYTES", 7)
    with pytest.raises(AttemptError, match="more than 7 bytes"):
        _run(tmp_path, "pass", sink_dir=str(sink))


def test_flagged_raw_stream_fails_closed_without_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "attempt"
    source = "import os; os.write(1, ('A' + 'KIA' + 'Q' * 16).encode())"

    monkeypatch.setattr(
        cli,
        "resolve_invocation",
        lambda _request: ResolvedInvocation(_python(source), ADAPTER_BUNDLE_SHA256),
    )
    runner_exit = cli.main(
        [
            "--output",
            str(output),
            "--timeout",
            "2",
            "--derived-image",
            DERIVED_IMAGE_DIGEST,
            "--shared-base-digest",
            SHARED_BASE_IMAGE_DIGEST,
            "--shared-base-uri",
            SHARED_BASE_IMAGE_URI,
            "--tool",
            "fixture-tool",
            "--operation",
            "list",
            "--mode",
            "list",
            "--bucket",
            "bucket-x",
            "--region",
            "region-y",
        ]
    )

    assert runner_exit == 2
    assert list(output.iterdir()) == []


def test_scanner_error_fails_closed_without_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "attempt"

    def scanner_error(_path: Path) -> ScanOutcome:
        return ScanOutcome.ERROR

    monkeypatch.setattr(attempt_engine, "scan_binary_file", scanner_error)
    monkeypatch.setattr(
        cli,
        "resolve_invocation",
        lambda _request: ResolvedInvocation(_python("print('clean')"), ADAPTER_BUNDLE_SHA256),
    )
    runner_exit = cli.main(
        [
            "--output",
            str(output),
            "--timeout",
            "2",
            "--derived-image",
            DERIVED_IMAGE_DIGEST,
            "--shared-base-digest",
            SHARED_BASE_IMAGE_DIGEST,
            "--shared-base-uri",
            SHARED_BASE_IMAGE_URI,
            "--tool",
            "fixture-tool",
            "--operation",
            "list",
            "--mode",
            "list",
            "--bucket",
            "bucket-x",
            "--region",
            "region-y",
        ]
    )

    assert runner_exit == 2
    assert list(output.iterdir()) == []


def test_populated_output_is_refused_without_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "attempt"
    output.mkdir()
    sentinel = output / "keep"
    sentinel.write_bytes(b"original")

    with pytest.raises(AttemptError, match="populated"):
        run_attempt(
            AttemptOptions(
                output=output,
                argv=_python("pass"),
                timeout_s=2,
                adapter_bundle_sha256=ADAPTER_BUNDLE_SHA256,
                shared_base_digest=SHARED_BASE_IMAGE_DIGEST,
                shared_base_uri=SHARED_BASE_IMAGE_URI,
                derived_image=DERIVED_IMAGE_DIGEST,
            )
        )

    assert sentinel.read_bytes() == b"original"
    assert {entry.name for entry in output.iterdir()} == {"keep"}


@pytest.mark.parametrize(
    "field",
    ["shared_base_digest", "derived_image"],
)
@pytest.mark.parametrize(
    "identity",
    [
        "",
        "sha256:0",
        "SHA256:" + "0" * 64,
        "sha256:" + "A" * 64,
        "repo/image@sha256:" + "0" * 64,
        "sha256:" + "0" * 65,
    ],
)
def test_direct_engine_requires_canonical_exact_image_digests(
    tmp_path: Path,
    field: str,
    identity: str,
) -> None:
    output = tmp_path / "attempt"
    label = "shared base" if field == "shared_base_digest" else "derived"
    with pytest.raises(AttemptError, match=f"{label} image identity"):
        _run(tmp_path, "pass", **{field: identity})
    assert not output.exists()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"tool_image_digest": "sha256:0"}, "tool image identity"),
        (
            {
                "tool_image_digest": TOOL_IMAGE_DIGEST,
                "tool_image_uri": "registry.example/study/other@" + DERIVED_IMAGE_DIGEST,
            },
            "tool image URI",
        ),
        ({"selection_sha256": "A" * 64}, "tool selection identity"),
    ],
)
def test_direct_engine_rejects_invalid_tool_image_provenance(
    tmp_path: Path,
    changes: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(AttemptError, match=message):
        _run(tmp_path, "pass", **changes)
    assert not (tmp_path / "attempt").exists()


def test_cli_has_no_subject_image_override_and_requires_derived_digest() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([*LOGICAL_ARGS, "--subject-image", SHARED_BASE_IMAGE_DIGEST])
    without_derived = LOGICAL_ARGS[:-2]
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(without_derived)


@pytest.mark.parametrize("kind", ["missing", "corrupt"])
def test_cli_maps_invalid_staged_selection_to_infrastructure_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    selection_path = tmp_path / "selection.json"
    if kind == "corrupt":
        selection_path.write_text("{not-json", encoding="utf-8")

    def resolve(request: CommandRequest) -> ResolvedInvocation:
        return resolve_real_invocation(
            request,
            selection_path=selection_path,
            adapter_dir=root / "tools/aws-cli/adapter",
        )

    monkeypatch.setattr(cli, "resolve_invocation", resolve)
    output = tmp_path / "attempt"
    assert cli.main(["--output", str(output), *LOGICAL_ARGS]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("attempt-runner: cannot read registered build metadata:")
    assert "Traceback" not in captured.err
    assert not output.exists()


def test_output_directory_symlink_is_rejected_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    output = tmp_path / "attempt"
    output.symlink_to(target, target_is_directory=True)
    with pytest.raises(AttemptError, match="symlink or non-directory"):
        _run(tmp_path, "pass")
    assert list(target.iterdir()) == []


def test_symlinked_output_parent_is_rejected_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(target, target_is_directory=True)
    with pytest.raises(AttemptError, match="symlink or non-directory"):
        _run(tmp_path, "pass", output=linked_parent / "attempt")
    assert list(target.iterdir()) == []


def test_publication_stays_anchored_when_output_parent_path_is_swapped(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    output = parent / "attempt"
    output.mkdir()
    original_parent = tmp_path / "original-parent"

    def swap_parent_path() -> None:
        parent.rename(original_parent)
        parent.mkdir()
        (parent / "attempt").mkdir()

    result, runner_exit = run_attempt(
        AttemptOptions(
            output=output,
            argv=_python("print('anchored')"),
            timeout_s=2,
            adapter_bundle_sha256=ADAPTER_BUNDLE_SHA256,
            shared_base_digest=SHARED_BASE_IMAGE_DIGEST,
            shared_base_uri=SHARED_BASE_IMAGE_URI,
            derived_image=DERIVED_IMAGE_DIGEST,
        ),
        post_measure_hook=swap_parent_path,
    )

    assert runner_exit == 0
    assert result["schema_version"] == 3
    assert list((parent / "attempt").iterdir()) == []
    anchored = original_parent / "attempt"
    assert {path.name for path in anchored.iterdir()} == {
        "result.json",
        "stdout.raw.gz",
        "stderr.raw.gz",
    }


def test_detached_descendant_is_cleaned_up_and_fails_the_harness(tmp_path: Path) -> None:
    source = """
import subprocess, sys
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
)
print(child.pid, flush=True)
"""
    result, runner_exit = _run(tmp_path, source)

    assert runner_exit == 2
    outcome = _outcome(result)
    assert outcome["status"] == "harness_error"
    cleanup = outcome["cleanup"]
    assert cleanup["state"] == "failed"  # type: ignore[index]
    escaped = cleanup["escaped_descendants"]  # type: ignore[index]
    assert isinstance(escaped, list)
    assert len(escaped) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(escaped[0], 0)


def test_generic_dockerfile_bakes_no_tool_specific_command_prefix() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "harness/derived-image/Dockerfile").read_text()

    assert dockerfile.count("FROM ${TOOL_IMAGE}") == 2
    # Which package layers make up the payload is owned by
    # tests/test_payload_boundary.py; this only pins that the recipe stays
    # generic, so assert the copy exists without restating the boundary.
    assert "COPY --from=adapter command.py /opt/s3-listing-study/tool/command.py" in dockerfile
    assert "COPY --from=adapter normalize.py /opt/s3-listing-study/tool/normalize.py" in dockerfile
    assert "COPY --from=selection image.json /opt/s3-listing-study/selection.json" in dockerfile
    assert "ARG TOOL_IMAGE" in dockerfile
    assert "WORKDIR ${SUBJECT_WORKDIR}" not in dockerfile
    assert "aws-cli" not in dockerfile
    assert "command-prefix" not in dockerfile
    assert "sh -c" not in dockerfile
    assert "S3_STUDY_ATTEMPT_OUT" not in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert not (root / "harness/images/aws-cli").exists()
    assert (root / "tools/aws-cli/build/Dockerfile").exists()
