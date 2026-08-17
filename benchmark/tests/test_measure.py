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
        }
        for tool in TOOLBOX_TOOLS
    }
    projection = {
        "schema_version": 2,
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
        "schema_version": 4,
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


def test_unknown_cgroup_events_produce_unknown_oom_deltas(tmp_path: Path) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    snapshot = measure.cgroup_snapshot(cgroup)
    assert snapshot["memory_events"] is None
    assert measure._event_delta(snapshot["memory_events"], {}, "oom") is None
    assert measure._event_delta({}, snapshot["memory_events"], "oom_kill") is None


def test_subject_home_matches_non_root_image() -> None:
    assert measure.SUBJECT_ENV["HOME"] == "/home/s3study"


def test_environment_boundary_rejects_reserved_collisions() -> None:
    assert "reserved" in (measure.validate_environment_inputs({"HOME": "/tmp"}, {}) or "")
    assert "reserved" in (
        measure.validate_environment_inputs({}, {"AWS_ACCESS_KEY_ID": "leaked"}) or ""
    )
    with pytest.raises(ValueError, match="unsupported"):
        measure.parse_case_env(["AWS_ACCESS_KEY_ID=not-allowed"])


def test_credential_payload_parses_and_refuses_the_wrong_stratum() -> None:
    parsed = measure.resolve_credential_env(
        "authenticated",
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
    assert measure.resolve_credential_env("anonymous", {}) == {}
    with pytest.raises(ValueError, match="missing required key"):
        measure.resolve_credential_env(
            "authenticated", {CREDENTIAL_ENV_VAR: "AWS_ACCESS_KEY_ID=AKIAEXAMPLE"}
        )
    with pytest.raises(ValueError, match="unsupported key"):
        measure.resolve_credential_env("authenticated", {CREDENTIAL_ENV_VAR: "GOOGLE_TOKEN=nope"})
    with pytest.raises(ValueError, match="requires"):
        measure.resolve_credential_env("authenticated", {})
    with pytest.raises(ValueError, match="anonymous"):
        measure.resolve_credential_env(
            "anonymous", {CREDENTIAL_ENV_VAR: "AWS_ACCESS_KEY_ID=AKIAEXAMPLE"}
        )


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
        lambda path, uri: uploaded.append((uri, Path(path).read_bytes())),
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
            "--campaign-id",
            "2026-08-16-candidate",
            "--job-id",
            "job",
            "--case-id",
            "case",
            "--case-fingerprint",
            "f" * 64,
            "--image-set-sha256",
            "1" * 64,
            "--run-ordinal",
            "1",
            "--submission-number",
            "1",
            "--machine-type",
            "machine",
            "--vcpus",
            "2",
            "--memory-gb",
            "4",
            "--container-memory-gb",
            "none",
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
        "--auth",
        "authenticated",
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
        "--campaign-id",
        "2026-08-16-x",
        "--job-id",
        "j",
        "--case-id",
        "c",
        "--case-fingerprint",
        "e" * 64,
        "--image-set-sha256",
        "f" * 64,
        "--run-ordinal",
        "1",
        "--submission-number",
        "1",
        "--machine-type",
        "m",
        "--vcpus",
        "2",
        "--memory-gb",
        "4",
        "--container-memory-gb",
        "none",
    ]
    assert measure.main(required) == 2


def test_recursive_upload_preserves_native_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = tmp_path / "attempt"
    (attempt / "native/listing/data").mkdir(parents=True)
    (attempt / "native/listing/data/part.parquet").write_bytes(b"part")
    (attempt / "result.json").write_text("{}")
    uploaded: list[str] = []
    monkeypatch.setattr(gcs, "upload_file", lambda _path, uri: uploaded.append(uri))
    assert measure.upload(attempt, "gs://bucket/leaf/")
    assert "gs://bucket/leaf/native/listing/data/part.parquet" in uploaded
    assert uploaded[-1] == "gs://bucket/leaf/result.json"
