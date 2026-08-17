from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import tomllib
from pathlib import Path

import pytest

from benchmark import adapters, gcs, measure
from benchmark.contract import CREDENTIAL_ENV_VAR, TOOLBOX_TOOLS
from benchmark.runtime.command_adapter import HEAP_PERCENT

ROOT = Path(__file__).parents[2]


def image_metadata(workdir: str | None = None) -> dict[str, object]:
    selected_workdir = workdir or os.getcwd()
    tools = {
        tool: {
            "tool_version": "1",
            "tool_build_sha256": "b" * 64,
            "tool_artifact_kind": "release-binary",
            "tool_artifact_locator": f"https://example.test/{tool}",
            "tool_artifact_sha256": "e" * 64,
            "recipe_sha256": "f" * 64,
            "build_inputs_sha256": "a" * 64,
            "adapter_bundle_sha256": "c" * 64,
            "subject_workdir": selected_workdir,
            "executable": [sys.executable],
            "tool_slice_sha256": "1" * 64,
            "platform_sha256": "2" * 64,
        }
        for tool in TOOLBOX_TOOLS
    }
    projection = {
        "schema_version": 3,
        "toolbox_recipe_sha256": "9" * 64,
        "tools": {
            tool: {
                name: value for name, value in registered.items() if name != "adapter_bundle_sha256"
            }
            for tool, registered in tools.items()
        },
    }
    digest = hashlib.sha256(
        json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        **projection,
        "schema_version": 5,
        "tools": tools,
        "toolbox_manifest_sha256": digest,
        "toolbox_recipe_sha256": "9" * 64,
        "harness_revision": "d" * 40,
    }


def test_worker_requirement_versions_match_repository_lock() -> None:
    locked = {
        package["name"].lower(): package["version"]
        for package in tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    }
    for line in (ROOT / "benchmark/build/requirements-worker.txt").read_text().splitlines():
        name, version = line.split("==", 1)
        assert locked[name.lower()] == version


def test_normalize_to_path_always_forwards_the_config_blob() -> None:
    """The subprocess boundary carries ``--config`` unconditionally, never a
    Python-level default silently swallowed before the flag reaches argv."""
    command = adapters._normalizer_command(
        "adapter", "recursive", "prefix/", {"mode": "recursive", "concurrency": 4}
    )
    assert command[-2:] == ["--config", '{"concurrency":4,"mode":"recursive"}']

    empty = adapters._normalizer_command("adapter", "recursive", "prefix/", {})
    assert empty[-2:] == ["--config", "{}"]


def test_native_parquet_count_and_normalize_are_file_backed(tmp_path: Path) -> None:
    import duckdb

    native = tmp_path / "native/listing"
    (native / "data").mkdir(parents=True)
    (native / "_SUCCESS").write_text("")
    con = duckdb.connect()
    con.execute(
        'COPY (SELECT \'key\'::BLOB AS "key", 3::BIGINT AS "size", \'etag\' AS "etag", '
        "TIMESTAMPTZ '2026-01-01T00:00:00Z' last_modified, "
        "'STANDARD' AS storage_class, 'OBJECT' AS row_type) TO ? (FORMAT PARQUET)",
        [str(native / "data/part.parquet")],
    )
    con.close()
    adapter = ROOT / "tools/swath/adapter"
    stdout = tmp_path / "stdout.log"
    stdout.write_bytes(b"")
    assert (
        adapters.count_rows(adapter, "swath", "recursive-parquet", "", stdout, tmp_path / "native")
        == 1
    )
    normalized = tmp_path / "normalized.tsv"
    adapters.normalize_to_path(
        adapter,
        "swath",
        "recursive-parquet",
        "",
        normalized,
        dataset=tmp_path / "native",
    )
    assert normalized.read_bytes().startswith(b"key\t3\tetag\t")


def test_timeout_kills_process_group_and_records_cgroup_oom(tmp_path: Path) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "memory.current").write_text("10")
    (cgroup / "memory.peak").write_text("20")
    (cgroup / "memory.events").write_text("oom 1\noom_kill 0\n")
    script = (
        "import pathlib,subprocess,sys,time; "
        "p=pathlib.Path(sys.argv[1]); "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "p.joinpath('memory.events').write_text('oom 3\\noom_kill 1\\n'); "
        "time.sleep(60)"
    )
    old = os.environ.get("BENCHMARK_CGROUP_DIR")
    os.environ["BENCHMARK_CGROUP_DIR"] = str(cgroup)
    try:
        result = measure.run_tool(
            (sys.executable, "-c", script, str(cgroup)),
            tmp_path,
            timeout=1,
            term_grace=0.1,
            env=dict(os.environ),
        )
    finally:
        if old is None:
            os.environ.pop("BENCHMARK_CGROUP_DIR", None)
        else:
            os.environ["BENCHMARK_CGROUP_DIR"] = old
    assert result["timed_out"] is True
    assert result["process_group_empty"] is True
    assert result["descendants_empty"] is True
    assert result["subreaper_enabled"] is True
    elapsed_ns = result["elapsed_ns"]
    assert isinstance(elapsed_ns, int)
    assert elapsed_ns >= 1_000_000
    cgroup_result = result["cgroup"]
    assert isinstance(cgroup_result, dict)
    assert cgroup_result["oom_delta"] == 2
    assert cgroup_result["oom_kill_delta"] == 1


def test_each_exec_reports_its_own_peak_rss(tmp_path: Path) -> None:
    """Two execs share one worker, and RSS must not travel between them.

    ``RUSAGE_CHILDREN`` is a process-lifetime high-water mark: with an untimed
    setup exec ahead of the subject, a fat setup would publish its own peak as
    the subject's measurement. Per-invocation ``os.wait4`` rusage is what
    separates them (Linux semantics; this suite is Linux-only).

    Every figure here is read against the lean baseline rather than an absolute
    bound: a forked child inherits its parent's high-water mark on Linux, so the
    baseline is whatever process ran this suite, and the fat child is sized to
    clear it.
    """

    def peak(name: str, script: str) -> int:
        directory = tmp_path / name
        directory.mkdir()
        execution = measure.run_tool(
            (sys.executable, "-c", script),
            directory,
            timeout=60,
            term_grace=0.1,
            env=dict(os.environ),
        )
        value = execution["max_rss_kb"]
        assert isinstance(value, int)
        return value

    before = peak("before", "pass")
    # Written to, not merely allocated: a calloc this size is untouched zero
    # pages that never become resident.
    fatten = f"b = bytearray({before + 300_000} * 1024); b[::4096] = b'x' * (len(b) // 4096)"
    fat = peak("fat", fatten)
    after = peak("after", "pass")
    assert fat > before + 200_000
    # The claim: the fat exec's peak did not follow the lean one that came next.
    assert after < before + 20_000


def test_the_container_peak_records_whether_it_could_be_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cgroup peak is per container, so it is the setup exec's too.

    A kernel that takes the reset write leaves the subject a fresh high-water
    mark; one that refuses leaves the larger of the two phases, and the flag is
    what tells a reader which of the two the number is.
    """
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "memory.current").write_text("10")
    (cgroup / "memory.peak").write_text("2048")
    (cgroup / "memory.events").write_text("oom 0\noom_kill 0\n")
    monkeypatch.setenv("BENCHMARK_CGROUP_DIR", str(cgroup))
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    execution = measure.run_tool(
        (sys.executable, "-c", "pass"),
        attempt,
        timeout=30,
        term_grace=0.1,
        env=dict(os.environ),
        reset_peak=True,
    )
    cgroup_result = execution["cgroup"]
    assert isinstance(cgroup_result, dict)
    assert cgroup_result["memory_peak_reset"] is True
    assert (cgroup / "memory.peak").read_text() == "reset"

    assert measure.reset_memory_peak(tmp_path / "absent") is False
    assert measure.reset_memory_peak(None) is False


def test_unknown_cgroup_events_produce_unknown_oom_deltas(tmp_path: Path) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    snapshot = measure.cgroup_snapshot(cgroup)
    assert snapshot["memory_events"] is None
    assert measure._event_delta(snapshot["memory_events"], {}, "oom") is None
    assert measure._event_delta({}, snapshot["memory_events"], "oom_kill") is None


def test_environment_boundary_rejects_reserved_collisions() -> None:
    assert "reserved" in (measure.validate_environment_inputs({"HOME": "/tmp"}) or "")
    assert "reserved" in (measure.validate_environment_inputs({"AWS_ACCESS_KEY_ID": "x"}) or "")


def test_credential_payload_parses_and_refuses_the_wrong_stratum() -> None:
    parsed = measure.resolve_credential_env(
        "public-read",
        {
            CREDENTIAL_ENV_VAR: (
                "AWS_ACCESS_KEY_ID=AKIAEXAMPLE\nAWS_SECRET_ACCESS_KEY=secret/value+with=padding\n\n"
            )
        },
    )
    # A secret access key may itself contain '=', so only the first one splits.
    assert parsed == {
        "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "secret/value+with=padding",
    }
    assert measure.resolve_credential_env(None, {}) == {}
    with pytest.raises(ValueError, match="missing required key"):
        measure.resolve_credential_env(
            "public-read", {CREDENTIAL_ENV_VAR: "AWS_ACCESS_KEY_ID=AKIAEXAMPLE"}
        )
    with pytest.raises(ValueError, match="unsupported key"):
        measure.resolve_credential_env("authenticated", {CREDENTIAL_ENV_VAR: "GOOGLE_TOKEN=nope"})
    with pytest.raises(ValueError, match="requires"):
        measure.resolve_credential_env("public-read", {})
    with pytest.raises(ValueError, match="no auth role"):
        measure.resolve_credential_env(None, {CREDENTIAL_ENV_VAR: "AWS_ACCESS_KEY_ID=AKIAEXAMPLE"})


@pytest.mark.parametrize(
    "secret",
    [
        b"AKIAABCDEFGHIJKLMNOP",
        b"https://example.test/?X-Amz-Signature=" + b"a" * 64,
        b"AWS_SESSION_TOKEN=" + b"A" * 32,
    ],
)
def test_secret_scan_covers_nested_native_files(tmp_path: Path, secret: bytes) -> None:
    native = tmp_path / "native/deep/tree"
    native.mkdir(parents=True)
    (native / "part.bin").write_bytes(b"binary\x00prefix" + secret + b"\xffsuffix")
    hit = measure.scan_for_secrets([tmp_path / "native"])
    assert hit is not None
    assert "part.bin" in hit


def test_secret_scan_is_binary_streaming_and_crosses_chunk_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(measure, "SECRET_SCAN_CHUNK", 1024)
    clean = tmp_path / "clean.bin"
    clean.write_bytes((b"\x00\xffclean" * 300_000) + b"tail")
    assert measure.scan_for_secrets([clean]) is None
    boundary = tmp_path / "boundary.bin"
    boundary.write_bytes(b"x" * 1017 + b"AKIAABCDEFGHIJKLMNOP" + b"z" * 2048)
    assert measure.scan_for_secrets([boundary]) is not None


def test_secret_scan_refuses_native_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(b"clean")
    native = tmp_path / "native"
    native.mkdir()
    (native / "escape").symlink_to(outside)
    assert "symlink" in (measure.scan_for_secrets([native]) or "")


def test_image_metadata_claim_mismatch_refuses(tmp_path: Path) -> None:
    metadata = image_metadata()
    tools = metadata["tools"]
    assert isinstance(tools, dict)
    selected = tools["aws-cli"]
    assert isinstance(selected, dict)
    metadata_path = tmp_path / "image-metadata.json"
    metadata_path.write_text(json.dumps(metadata))
    args = type(
        "Args",
        (),
        {
            "image_metadata": str(metadata_path),
            "tool": "aws-cli",
            "tool_version": "wrong",
            "tool_build_sha256": "b" * 64,
            "adapter_bundle_sha256": "c" * 64,
            "harness_revision": "d" * 40,
            "tool_recipe_sha256": selected["recipe_sha256"],
            "tool_build_inputs_sha256": selected["build_inputs_sha256"],
            "image": "registry/derived@sha256:" + "f" * 64,
            "subject_workdir": os.getcwd(),
            "toolbox_manifest_sha256": metadata["toolbox_manifest_sha256"],
            "toolbox_recipe_sha256": metadata["toolbox_recipe_sha256"],
        },
    )()
    assert "does not match" in (measure.validate_image_metadata(args) or "")

    selected["executable"] = ["/tampered"]
    metadata_path.write_text(json.dumps(metadata))
    assert "invalid toolbox manifest hash" in (measure.validate_image_metadata(args) or "")


def test_subject_runs_in_registered_cwd_without_moving_attempt_paths(tmp_path: Path) -> None:
    subject_workdir = tmp_path / "subject"
    subject_workdir.mkdir()
    attempt = (tmp_path / "attempt").resolve()
    attempt.mkdir()
    execution = measure.run_tool(
        (sys.executable, "-c", "import os; print(os.getcwd())"),
        attempt,
        timeout=5,
        term_grace=0.1,
        env=dict(os.environ),
        cwd=str(subject_workdir),
    )
    assert execution["exit_code"] == 0
    assert (attempt / "stdout.log").read_text().strip() == str(subject_workdir)


def test_count_failure_is_distinct_postprocessing_failure() -> None:
    assert measure.final_exit_code(0, False, "count failed") == 11
    assert measure.final_exit_code(1, False, None) == 0
    assert measure.final_exit_code(124, True, None) == 0
    assert measure.final_exit_code(0, False, None, oom_kill_delta=1) == 1
    assert measure.final_exit_code(0, False, None, process_group_empty=False) == 1
    assert measure.final_exit_code(0, False, None, descendants_empty=False) == 1
    assert measure.final_exit_code(0, False, None, process_tree_clean=False) == 1


def test_setsid_descendant_is_killed_and_marks_process_tree_unclean(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'], "
        "start_new_session=True); pathlib.Path(sys.argv[1]).write_text(str(p.pid))"
    )
    result = measure.run_tool(
        (sys.executable, "-c", script, str(pid_file)),
        tmp_path,
        timeout=5,
        term_grace=0.1,
        env=dict(os.environ),
    )
    child_pid = int(pid_file.read_text())
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert result["process_tree_clean"] is False
    assert result["descendants_empty"] is True
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, signal.SIGCONT)


def test_count_failure_uploads_result_marker_before_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = image_metadata()
    tools = metadata["tools"]
    assert isinstance(tools, dict)
    selected = tools["aws-cli"]
    assert isinstance(selected, dict)
    metadata_path = tmp_path / "image-metadata.json"
    metadata_path.write_text(json.dumps(metadata))
    attempt = tmp_path / "attempt"
    uploaded: list[tuple[str, bytes]] = []
    monkeypatch.setattr(
        adapters,
        "compile_command",
        lambda *_args, **_kwargs: ((sys.executable, "-c", "print('one')"), {}),
    )
    monkeypatch.setattr(measure, "row_count_for", lambda *_args: (None, "count failed"))
    monkeypatch.setattr(
        gcs,
        "upload_file",
        lambda path, uri, **_kwargs: uploaded.append((uri, Path(path).read_bytes())),
    )
    code = measure.main(
        [
            "--tool",
            "aws-cli",
            "--mode",
            "recursive",
            "--bucket",
            "bucket",
            "--region",
            "region",
            "--output",
            str(attempt),
            "--destination",
            "gs://results/job/",
            "--image",
            "registry/derived@sha256:" + "a" * 64,
            "--toolbox-manifest-sha256",
            str(metadata["toolbox_manifest_sha256"]),
            "--toolbox-recipe-sha256",
            str(metadata["toolbox_recipe_sha256"]),
            "--tool-recipe-sha256",
            str(selected["recipe_sha256"]),
            "--tool-build-inputs-sha256",
            str(selected["build_inputs_sha256"]),
            "--tool-version",
            str(selected["tool_version"]),
            "--tool-build-sha256",
            str(selected["tool_build_sha256"]),
            "--adapter-bundle-sha256",
            str(selected["adapter_bundle_sha256"]),
            "--harness-revision",
            str(metadata["harness_revision"]),
            "--subject-workdir",
            str(selected["subject_workdir"]),
            "--group-id",
            "g20260816-000000",
            "--job-name",
            "suite-aws-cli-9f300cc4d2b1-s1",
            "--case-id",
            "aws-cli.9f300cc4d2b1",
            "--attempt-id",
            "aws-cli.9f300cc4d2b1.s1",
            "--image-set-sha256",
            "1" * 64,
            "--machine-type",
            "machine",
            "--vcpus",
            "2",
            "--memory-gb",
            "4",
            "--container-memory-gb",
            "none",
            "--config",
            '{"mode": "recursive"}',
            "--adapter-root",
            str(ROOT / "tools"),
            "--image-metadata",
            str(metadata_path),
        ]
    )
    assert code == measure.EXIT_POSTPROCESSING_FAILED
    assert uploaded[-1][0].endswith("/result.json")
    result = json.loads(uploaded[-1][1])
    assert result["exit_code"] == 0
    assert result["row_count"] is None
    assert result["row_count_error"] == "count failed"
    assert result["tool_recipe_sha256"] == selected["recipe_sha256"]
    assert result["toolbox_manifest_sha256"] == metadata["toolbox_manifest_sha256"]
    assert result["applied_subject_workdir"] == selected["subject_workdir"]


def test_the_cases_config_and_heap_share_reach_the_capsule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case's ``config`` blob and the visible ceiling reach the compiled request.

    ``visible_memory_gb`` is the container ceiling when the case set one, else
    the whole box; ``heap_percent`` is the harness's one methodology constant,
    never a per-case choice.
    """
    metadata = image_metadata()
    tools = metadata["tools"]
    assert isinstance(tools, dict)
    selected = tools["aws-cli"]
    assert isinstance(selected, dict)
    metadata_path = tmp_path / "image-metadata.json"
    metadata_path.write_text(json.dumps(metadata))
    attempt = tmp_path / "attempt"

    calls: list[dict[str, object]] = []

    def record_call(*_args: object, **kwargs: object) -> tuple[tuple[str, ...], dict[str, str]]:
        calls.append(kwargs)
        return (sys.executable, "-c", "print('one')"), {}

    monkeypatch.setattr(adapters, "compile_command", record_call)
    monkeypatch.setattr(measure, "row_count_for", lambda *_args: (0, None))
    monkeypatch.setattr(gcs, "upload_file", lambda *_args, **_kwargs: None)

    argv = [
        "--tool",
        "aws-cli",
        "--mode",
        "recursive",
        "--bucket",
        "bucket",
        "--region",
        "region",
        "--output",
        str(attempt),
        "--destination",
        "gs://results/job/",
        "--image",
        "registry/derived@sha256:" + "a" * 64,
        "--toolbox-manifest-sha256",
        str(metadata["toolbox_manifest_sha256"]),
        "--toolbox-recipe-sha256",
        str(metadata["toolbox_recipe_sha256"]),
        "--tool-recipe-sha256",
        str(selected["recipe_sha256"]),
        "--tool-build-inputs-sha256",
        str(selected["build_inputs_sha256"]),
        "--tool-version",
        str(selected["tool_version"]),
        "--tool-build-sha256",
        str(selected["tool_build_sha256"]),
        "--adapter-bundle-sha256",
        str(selected["adapter_bundle_sha256"]),
        "--harness-revision",
        str(metadata["harness_revision"]),
        "--subject-workdir",
        str(selected["subject_workdir"]),
        "--group-id",
        "g20260816-000000",
        "--job-name",
        "suite-aws-cli-9f300cc4d2b1-s1",
        "--case-id",
        "aws-cli.9f300cc4d2b1",
        "--attempt-id",
        "aws-cli.9f300cc4d2b1.s1",
        "--image-set-sha256",
        "1" * 64,
        "--machine-type",
        "machine",
        "--vcpus",
        "2",
        "--memory-gb",
        "4",
        "--container-memory-gb",
        "2",
        "--config",
        '{"mode": "recursive", "concurrency": 8}',
        "--adapter-root",
        str(ROOT / "tools"),
        "--image-metadata",
        str(metadata_path),
    ]
    assert measure.main(argv) == 0
    assert len(calls) == 1
    assert calls[0]["config"] == {"mode": "recursive", "concurrency": 8}
    assert calls[0]["visible_memory_gb"] == 2.0  # the container ceiling, not the 4 GB box
    assert calls[0]["heap_percent"] == HEAP_PERCENT

    # The same answer arrives twice in one request; disagreement is a controller
    # bug, and the config is what the case hashed.
    assert argv[2:4] == ["--mode", "recursive"]
    assert measure.main([*argv[:3], "recursive-jsonl", *argv[4:]]) == 2
    assert len(calls) == 1


def test_missing_credential_fails_before_adapter_or_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CREDENTIAL_ENV_VAR, raising=False)
    monkeypatch.setattr(
        adapters,
        "compile_command",
        lambda *_args, **_kwargs: pytest.fail("adapter must not be loaded"),
    )
    required = [
        "--tool",
        "x",
        "--mode",
        "x",
        "--bucket",
        "b",
        "--region",
        "r",
        "--auth-role",
        "public-read",
        "--output",
        str(tmp_path),
        "--destination",
        "gs://x/",
        "--image",
        "x@sha256:" + "a" * 64,
        "--toolbox-manifest-sha256",
        "9" * 64,
        "--toolbox-recipe-sha256",
        "8" * 64,
        "--tool-recipe-sha256",
        "7" * 64,
        "--tool-build-inputs-sha256",
        "6" * 64,
        "--tool-version",
        "1",
        "--tool-build-sha256",
        "b" * 64,
        "--adapter-bundle-sha256",
        "c" * 64,
        "--harness-revision",
        "d" * 40,
        "--subject-workdir",
        os.getcwd(),
        "--group-id",
        "g20260816-000000",
        "--job-name",
        "suite-aws-cli-9f300cc4d2b1-s1",
        "--case-id",
        "aws-cli.9f300cc4d2b1",
        "--attempt-id",
        "aws-cli.9f300cc4d2b1.s1",
        "--image-set-sha256",
        "f" * 64,
        "--machine-type",
        "m",
        "--vcpus",
        "2",
        "--memory-gb",
        "4",
        "--container-memory-gb",
        "none",
        "--config",
        "{}",
    ]
    assert measure.main(required) == 2


def test_recursive_upload_preserves_native_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = tmp_path / "attempt"
    (attempt / "native/listing/data").mkdir(parents=True)
    (attempt / "native/listing/data/part.parquet").write_bytes(b"part")
    (attempt / "result.json").write_text("{}")
    uploaded: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        gcs,
        "upload_file",
        lambda _path, uri, *, create_only=False: uploaded.append((uri, create_only)),
    )
    assert measure.upload(attempt, "gs://bucket/leaf/")
    assert ("gs://bucket/leaf/native/listing/data/part.parquet", True) in uploaded
    # Create-only, and the marker last: a deterministic prefix plus overwrite
    # semantics would let a second execution merge into the first.
    assert uploaded[-1] == ("gs://bucket/leaf/result.json", True)


def test_a_staged_artifact_whose_digest_moved_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case hashed this content, so other bytes are a different case in its clothes."""
    monkeypatch.setattr(gcs, "download_bytes", lambda _uri: b"cut/\npoints/\n")
    staged = measure.stage_artifact(
        "gs://results/suite/b/tool.abc.s1/native/hints.input",
        hashlib.sha256(b"cut/\npoints/\n").hexdigest(),
        tmp_path / "inbound",
    )
    assert staged.read_bytes() == b"cut/\npoints/\n"

    with pytest.raises(ValueError, match=r"not the \S+ this case consumes"):
        measure.stage_artifact(
            "gs://results/suite/b/tool.abc.s1/native/hints.input", "0" * 64, tmp_path / "inbound"
        )


INLINE_CAPSULE = '''\
"""A fixture capsule whose measurement runs a setup exec before the timed subject."""

import sys
from pathlib import Path

from benchmark.runtime.command_adapter import (
    CommandAdapterError,
    CommandRequest,
    Executable,
    Mode,
    Stated,
)

TOOL = "s3-fast-list"
EXECUTABLES = (Executable("fixture", (sys.executable,)),)
SUPPORTS_UNSIGNED = True
MODES = {
    "split": Mode(
        product="text",
        fields=("key",),
        axes={"segments": Stated()},
        purpose_ceiling="preparation",
    ),
    "hinted": Mode(product="text", fields=("key",), axes={"segments": Stated()}, inline="split"),
}


def build_command(request: CommandRequest) -> tuple[str, ...]:
    here = Path(__file__).parent
    if request.mode == "split":
        return (
            sys.executable,
            str(here / "split.py"),
            request.artifact_path,
            request.sink_dir,
            str(request.config["segments"]),
        )
    return (sys.executable, str(here / "subject.py"), request.artifact_path, request.sink_dir)


def _cut_points(path: Path) -> None:
    if not path.read_text().strip():
        raise CommandAdapterError("hints file holds no cut point at all")


VALIDATE_ARTIFACT = {"split": _cut_points}
'''

INLINE_SUBJECT = """\
import sys
from pathlib import Path

# The subject lists under whatever the setup exec published, and publishes into
# its own native sink -- which is not where the setup wrote.
hints = Path(sys.argv[1]).read_text()
Path(sys.argv[2], "listing.txt").write_text(hints)
print(hints, end="")
"""

INLINE_SPLIT = """\
import sys
import time
from pathlib import Path

time.sleep(0.2)
keyspace = Path(sys.argv[1]).read_text()
Path(sys.argv[2], "hints.input").write_text(keyspace)
"""


def inline_capsule(tmp_path: Path, split: str = INLINE_SPLIT) -> Path:
    """A capsule root whose `hinted` mode declares `split` as its inline setup."""
    adapter = tmp_path / "tools" / "s3-fast-list" / "adapter"
    adapter.mkdir(parents=True)
    (adapter / "command.py").write_text(INLINE_CAPSULE, encoding="utf-8")
    (adapter / "subject.py").write_text(INLINE_SUBJECT, encoding="utf-8")
    (adapter / "split.py").write_text(split, encoding="utf-8")
    return tmp_path / "tools"


def run_inline_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "hinted",
    split: str = INLINE_SPLIT,
    uploaded: list[tuple[str, bytes]] | None = None,
) -> int:
    """Run one attempt of the fixture capsule against a staged `.ks`.

    ``row_count_for`` is stubbed only for the measurement: a preparation must not
    reach it at all, and this capsule ships no normalizer for it to reach.
    """
    metadata = image_metadata()
    tools = metadata["tools"]
    assert isinstance(tools, dict)
    selected = tools["s3-fast-list"]
    assert isinstance(selected, dict)
    metadata_path = tmp_path / "image-metadata.json"
    metadata_path.write_text(json.dumps(metadata))
    keyspace = b"a/\nb/\n"
    monkeypatch.setattr(gcs, "download_bytes", lambda _uri: keyspace)
    if mode != "split":
        monkeypatch.setattr(measure, "row_count_for", lambda *_args: (2, None))
    monkeypatch.setattr(
        gcs,
        "upload_file",
        lambda path, uri, **_kwargs: (uploaded if uploaded is not None else []).append(
            (uri, Path(path).read_bytes())
        ),
    )
    monkeypatch.setattr(gcs, "upload_tree", lambda *_args, **_kwargs: None)
    return measure.main(
        [
            "--tool",
            "s3-fast-list",
            "--mode",
            mode,
            "--bucket",
            "bucket",
            "--region",
            "region",
            "--output",
            str(tmp_path / "attempt"),
            "--destination",
            "gs://results/job/",
            "--image",
            "registry/derived@sha256:" + "a" * 64,
            "--toolbox-manifest-sha256",
            str(metadata["toolbox_manifest_sha256"]),
            "--toolbox-recipe-sha256",
            str(metadata["toolbox_recipe_sha256"]),
            "--tool-recipe-sha256",
            str(selected["recipe_sha256"]),
            "--tool-build-inputs-sha256",
            str(selected["build_inputs_sha256"]),
            "--tool-version",
            str(selected["tool_version"]),
            "--tool-build-sha256",
            str(selected["tool_build_sha256"]),
            "--adapter-bundle-sha256",
            str(selected["adapter_bundle_sha256"]),
            "--harness-revision",
            str(metadata["harness_revision"]),
            "--subject-workdir",
            str(selected["subject_workdir"]),
            "--group-id",
            "g20260816-000000",
            "--job-name",
            "suite-s3-fast-list-9f300cc4d2b1-s1",
            "--case-id",
            "s3-fast-list.9f300cc4d2b1",
            "--attempt-id",
            "s3-fast-list.9f300cc4d2b1.s1",
            "--image-set-sha256",
            "1" * 64,
            "--machine-type",
            "machine",
            "--vcpus",
            "2",
            "--memory-gb",
            "4",
            "--container-memory-gb",
            "none",
            "--config",
            json.dumps({"mode": mode, "segments": 2}),
            "--input-artifact",
            "gs://results/prep/native/keyspace.ks",
            "--input-artifact-sha256",
            hashlib.sha256(keyspace).hexdigest(),
            "--adapter-root",
            str(inline_capsule(tmp_path, split)),
            "--image-metadata",
            str(metadata_path),
        ]
    )


def test_an_inline_setup_runs_untimed_before_the_subject_it_feeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of folding the split in: one attempt, two execs, one clock.

    The subject lists under what the setup published, the setup's own duration is
    recorded beside the measurement rather than inside it, and what it wrote is
    setup evidence rather than a listing the counter would read.
    """
    uploaded: list[tuple[str, bytes]] = []
    assert run_inline_worker(tmp_path, monkeypatch, uploaded=uploaded) == 0
    result = json.loads((tmp_path / "attempt/result.json").read_bytes())

    setup = result["setup"]
    hints = tmp_path / "attempt/inline/sink/hints.input"
    assert setup["mode"] == "split"
    assert setup["exit_code"] == 0
    assert setup["output"] == {"hints.input": hashlib.sha256(hints.read_bytes()).hexdigest()}
    assert setup["validated"] is True
    # The setup exec's argv carries the segment count the consumer stated, and
    # the subject's carries what the setup published.
    assert setup["command"][-1] == "2"
    assert result["argv"][2] == str(hints)
    # Untimed with respect to the measurement: the setup slept longer than the
    # subject ran, and the recorded timing is the subject's alone.
    assert setup["wall_s"] > result["wall_seconds"]
    # Setup evidence is not the subject's product: the native manifest is what
    # the counter reads, and the inline sink is not in it.
    assert result["native_manifest"] == {
        "listing.txt": hashlib.sha256(b"a/\nb/\n").hexdigest(),
    }
    assert (tmp_path / "attempt/inline/stdout.log").exists()
    assert uploaded[-1][0].endswith("/result.json")


def test_a_preparation_is_not_asked_for_a_row_count_it_has_no_answer_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The canary's finding: a mode capped at `preparation` publishes cut points,
    not a listing, so its normalizer rightly refuses the mode — and counting it
    anyway turned a perfect preparation into a failed task."""
    assert run_inline_worker(tmp_path, monkeypatch, mode="split") == 0
    result = json.loads((tmp_path / "attempt/result.json").read_bytes())
    assert result["exit_code"] == 0
    assert result["row_count"] is None
    assert result["row_count_error"] is None
    # The artifact is still evidence, digested like any other native output.
    assert set(result["native_manifest"]) == {"hints.input"}


def test_an_inline_setup_that_fails_fails_the_whole_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subject run on hints that were never made measures something else."""
    uploaded: list[tuple[str, bytes]] = []
    code = run_inline_worker(
        tmp_path,
        monkeypatch,
        split="import sys\n\nsys.exit(3)\n",
        uploaded=uploaded,
    )
    assert code == measure.EXIT_SETUP_FAILED
    assert uploaded == []


def test_an_inline_setup_publishing_more_than_one_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-artifact contract a consumed preparation holds to: the harness has
    no way to choose between two, and choosing wrong runs the wrong measurement."""
    two_files = (
        "import sys\nfrom pathlib import Path\n\n"
        'Path(sys.argv[2], "hints.input").write_text("a/\\n")\n'
        'Path(sys.argv[2], "hints.extra").write_text("b/\\n")\n'
    )
    assert run_inline_worker(tmp_path, monkeypatch, split=two_files) == measure.EXIT_SETUP_FAILED


def test_an_inline_artifact_the_capsule_refuses_is_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same claim as a preparation's, one location over: the bytes exist, digest
    cleanly, and mean nothing."""
    empty = (
        "import sys\nfrom pathlib import Path\n\n"
        'Path(sys.argv[2], "hints.input").write_text("\\n")\n'
    )
    assert run_inline_worker(tmp_path, monkeypatch, split=empty) == measure.EXIT_ARTIFACT_UNUSABLE
