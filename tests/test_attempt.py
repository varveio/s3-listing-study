"""Focused, offline contract tests for the single Python attempt engine."""

from __future__ import annotations

import gzip
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from s3_listing_study.attempt import AttemptError, AttemptOptions, cli, run_attempt
from s3_listing_study.attempt import engine as attempt_engine
from s3_listing_study.secret_scan import Outcome as ScanOutcome


def _python(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


def _run(tmp_path: Path, source: str, **changes: object) -> tuple[dict[str, object], int]:
    values: dict[str, object] = {
        "output": tmp_path / "attempt",
        "argv": _python(source),
        "timeout_s": 2.0,
        "term_grace_s": 0.1,
        "attempt_id": "test-attempt",
        "tool": "synthetic",
    }
    values.update(changes)
    return run_attempt(AttemptOptions(**values))  # type: ignore[arg-type]


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


def test_tool_nonzero_is_a_recorded_outcome_and_cli_success(tmp_path: Path) -> None:
    output = tmp_path / "attempt"
    runner_exit = cli.main(
        [
            "--output",
            str(output),
            "--timeout",
            "2",
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
            "--command-prefix",
            sys.executable,
            "--",
            "-c",
            "raise SystemExit(23)",
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
    result, runner_exit = _run(tmp_path, source, timeout_s=0.1, term_grace_s=0.05)

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
    child_pid = int(
        gzip.decompress((tmp_path / "attempt" / "stdout.raw.gz").read_bytes()).strip()
    )
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
    child_pid = int(
        gzip.decompress((tmp_path / "attempt" / "stdout.raw.gz").read_bytes()).strip()
    )
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
        AttemptOptions(output=output, argv=_python("pass"), timeout_s=2),
        post_measure_hook=before_finalization,
    )

    assert {entry.name for entry in output.iterdir()} == {
        "result.json",
        "stdout.raw.gz",
        "stderr.raw.gz",
    }
    assert _result(output)["schema_version"] == 1


def test_post_measure_delay_is_excluded_from_elapsed_time(tmp_path: Path) -> None:
    delay_s = 0.25
    result, _runner_exit = run_attempt(
        AttemptOptions(
            output=tmp_path / "attempt",
            argv=_python("pass"),
            timeout_s=2,
        ),
        post_measure_hook=lambda: time.sleep(delay_s),
    )

    elapsed_ns = result["timing"]["elapsed_ns"]  # type: ignore[index]
    assert isinstance(elapsed_ns, int)
    assert elapsed_ns < delay_s * 1_000_000_000


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
        AttemptOptions(output=output, argv=_python(source), timeout_s=2),
        source_env=source_env,
    )
    observed = json.loads(gzip.decompress((output / "stdout.raw.gz").read_bytes()))

    expected = {
        "AWS_EC2_METADATA_DISABLED": "true",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    assert observed == expected
    result_env = _result(output)["invocation"]["environment"]  # type: ignore[index]
    assert result_env == expected


def test_flagged_raw_stream_fails_closed_without_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "attempt"
    source = "import os; os.write(1, ('A' + 'KIA' + 'Q' * 16).encode())"

    runner_exit = cli.main(
        ["--output", str(output), "--timeout", "2", "--", *_python(source)]
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
    runner_exit = cli.main(
        ["--output", str(output), "--timeout", "2", "--", *_python("print('clean')")]
    )

    assert runner_exit == 2
    assert list(output.iterdir()) == []


def test_populated_output_is_refused_without_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "attempt"
    output.mkdir()
    sentinel = output / "keep"
    sentinel.write_bytes(b"original")

    with pytest.raises(AttemptError, match="populated"):
        run_attempt(AttemptOptions(output=output, argv=_python("pass"), timeout_s=2))

    assert sentinel.read_bytes() == b"original"
    assert {entry.name for entry in output.iterdir()} == {"keep"}


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


def test_aws_cli_dockerfile_bakes_the_pinned_exec_form_zipapp_contract() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[1] / "harness/images/aws-cli/Dockerfile"
    ).read_text()
    pinned = (
        "amazon/aws-cli@sha256:"
        "406ca32d31e640a56e8d52921b40528cc64bfa59ec9cb4ee1456db6746cb7292"
    )

    assert dockerfile.count(f"FROM {pinned}") == 2
    assert "COPY src/s3_listing_study/attempt/" in dockerfile
    assert "COPY src/s3_listing_study/secret_scan.py" in dockerfile
    assert "COPY harness/images/aws-cli/zipapp_main.py" in dockerfile
    assert 'WORKDIR /aws' in dockerfile
    assert 'ENTRYPOINT ["/usr/bin/python3", "-I"' in dockerfile
    assert '"--command-prefix", "/usr/local/bin/aws"]' in dockerfile
    assert "sh -c" not in dockerfile
    assert "S3_STUDY_ATTEMPT_OUT" not in dockerfile
