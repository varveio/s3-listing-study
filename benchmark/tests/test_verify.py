from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import TypedDict

import duckdb
import pytest

import benchmark.adapters as adapters
import benchmark.campaign as campaign
import benchmark.ledger as ledger
import benchmark.verify as verify
from benchmark.contract import EXIT_INCOMPLETE_GROUP, sha256_of

COMMAND_PY = """
from benchmark.runtime.command_adapter import Executable, Mode

PRODUCT = {{"listing": "listing.txt"}}
LISTING = "listing"

TOOL = "{tool}"
EXECUTABLES = (Executable("{tool}", ("/usr/bin/{tool}",)),)
MODES = {{
    "text-full": Mode(
        product="text",
        fields=("key", "size", "etag", "mtime", "storage_class"),
        artifacts=PRODUCT,
        product_artifact=LISTING,
    ),
    "text-keys": Mode(
        product="text", fields=("key",), artifacts=PRODUCT, product_artifact=LISTING
    ),
    "parquet-full": Mode(
        product="parquet",
        fields=("key", "size", "etag", "mtime", "storage_class"),
        artifacts={{"listing": "listing"}},
        product_artifact=LISTING,
        product_channel="dataset",
    ),
}}
SUPPORTS_UNSIGNED = True


def build_command(request):
    return ("/usr/bin/{tool}", request.mode)
"""

# Refuses unless the recorded config blob reaches it, which is the capsule
# contract's requirement that config reach BOTH entry points -- so a normalizer
# handed a default empty blob fails here rather than parsing its own output wrong.
NORMALIZE_PY = """
import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("mode")
parser.add_argument("prefix")
parser.add_argument("--input")
parser.add_argument("--dataset")
parser.add_argument("--config", default="{}")
args = parser.parse_args()

config = json.loads(args.config)
if config.get("mode") != args.mode:
    sys.exit(f"config blob did not reach normalize.py: {config!r}")
source = Path(args.input) if args.input else sorted(Path(args.dataset).rglob("*.txt"))[0]
for line in source.read_text().splitlines():
    if not line:
        continue
    key, size = line.split(" ")
    print("\\t".join((key, size, "e" + size, "2026-01-01T00:00:00Z", "STANDARD")))
"""

LISTING = ("a/one 1", "a/two 2")


def replay_metrics(requests: int, errors: int = 0) -> dict[str, object]:
    return {
        "meters": [
            {
                "name": "swath.replay.http.requests",
                "type": "counter",
                "tags": {},
                "count": float(requests),
            },
            {
                "name": "swath.replay.http.errors",
                "type": "counter",
                "tags": {},
                "count": float(errors),
            },
        ]
    }


class ReplayEvidence(TypedDict):
    replay: dict[str, object]
    replay_evidence: dict[str, object]


def adapter_root(tmp_path: Path, *tools: str) -> str:
    root = tmp_path / "tools"
    for tool in tools:
        adapter = root / tool / "adapter"
        adapter.mkdir(parents=True, exist_ok=True)
        (adapter / "command.py").write_text(COMMAND_PY.format(tool=tool))
        (adapter / "normalize.py").write_text(NORMALIZE_PY)
    return str(root)


def fixture_ledger(tmp_path: Path) -> sqlite3.Connection:
    return ledger.open_ledger(str(tmp_path / "campaign.db"), suite="fixture")


def record(
    con: sqlite3.Connection,
    tmp_path: Path,
    *,
    tool: str,
    digest: str,
    mode: str = "text-full",
    bucket: str = "bucket-one",
    purpose: str = "measurement",
    statistic: str = "timing",
    state: str = "SUCCEEDED",
    group_id: str = "g1",
    replay: dict[str, object] | None = None,
) -> ledger.Attempt:
    """Journal one attempt through the ledger's own writer, then settle it."""
    case_id = f"{tool}.{digest}"
    config = json.dumps({"mode": mode}, sort_keys=True, separators=(",", ":"))

    def build(ordinal: int) -> tuple[ledger.Attempt, str]:
        attempt_id = f"{case_id}.s{ordinal}"
        return (
            ledger.Attempt(
                case_id=case_id,
                attempt=ordinal,
                case_inputs=json.dumps({"case": case_id}),
                group_id=group_id,
                tool=tool,
                auth_role=None,
                executor=campaign.EXECUTOR,
                location="us-east1",
                machine_type="n4-standard-2",
                vcpus=2,
                memory_gb=8,
                container_memory_gb=None,
                heap_percent=75,
                timeout_s=600,
                target_bucket=bucket,
                target_region="us-east-1",
                target_prefix="",
                config=config,
                input_artifact_sha256=None,
                produced_by=None,
                tool_slice_sha256="a" * 64,
                platform_sha256="b" * 64,
                image_uri="registry/toolbox@sha256:" + "c" * 64,
                image_set_sha256="d" * 64,
                executor_env="{}",
                service_account="worker@example.iam.gserviceaccount.com",
                secret_resource=None,
                job_name=f"fixture-{tool}-{digest}-s{ordinal}".replace(".", "-"),
                result_prefix=str(tmp_path / "evidence" / bucket / attempt_id),
                purpose=purpose,
                statistic=statistic,
                origin="planned",
                replay=(
                    None
                    if replay is None
                    else json.dumps(replay, sort_keys=True, separators=(",", ":"))
                ),
            ),
            "{}",
        )

    attempt, _request = ledger.journal_intent(
        con, case_id=case_id, case_inputs=json.dumps({"case": case_id}), build=build, repeat=True
    )
    ledger.set_state(con, attempt.attempt_id, state)
    return attempt


def write_evidence(
    attempt: ledger.Attempt,
    *,
    listing: tuple[str, ...] = LISTING,
    compress: bool = False,
    **overrides: object,
) -> Path:
    prefix = Path(attempt.result_prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    stderr_gz = prefix / "stderr.log.gz"
    stderr_gz.write_bytes(gzip.compress(b""))
    mode = json.loads(attempt.config)["mode"]
    native = prefix / "native"
    body = ("\n".join(listing) + "\n").encode()
    if mode == "parquet-full":
        # A directory of parts: the shape nothing but a declaration can route to
        # the right normalizer, since a side output makes any sink non-empty.
        product_name, channel = "listing", "dataset"
        (native / product_name).mkdir(parents=True, exist_ok=True)
        (native / product_name / "part-0.txt").write_bytes(body)
    else:
        product_name, channel = "listing.txt", "stdout"
        native.mkdir(parents=True, exist_ok=True)
        if compress:
            # What a text product looks like in the sink: published gzipped,
            # under the name the block records.
            product_name += ".gz"
            body = gzip.compress(body)
        (native / product_name).write_bytes(body)
    published = sorted(path for path in native.rglob("*") if path.is_file())
    product = native / product_name
    result: dict[str, object] = {
        "attempt_id": attempt.attempt_id,
        "case_id": attempt.case_id,
        "tool": attempt.tool,
        "mode": json.loads(attempt.config)["mode"],
        "exit_code": 0,
        "worker_exit_code": 0,
        "timed_out": False,
        "wall_seconds": 1.5,
        "started_at": "2026-01-01T00:00:00.500000+00:00",
        "finished_at": "2026-01-01T00:00:01.500000+00:00",
        "max_rss_kb": 1024,
        "row_count": len(listing),
        "execution": {
            "timed_out": False,
            "subreaper_enabled": True,
            "process_tree_clean": True,
            "process_group_empty": True,
            "descendants_empty": True,
            "max_rss_kb": 1024,
            "elapsed_ns": 1_500_000_000,
            "cgroup": {"oom_delta": 0, "oom_kill_delta": 0},
        },
        "product": {
            "artifact": "listing",
            "name": f"native/{product_name}",
            "channel": channel,
            "size_bytes": sum(path.stat().st_size for path in published),
            "sha256": sha256_of(product) if product.is_file() else None,
        },
        "product_error": None,
        # The subject only printed, so fd 1 was the product and there is no log.
        "stdout": None,
        "stderr": {
            "name": stderr_gz.name,
            "size_bytes": stderr_gz.stat().st_size,
            "sha256": sha256_of(stderr_gz),
        },
        "native_manifest": {
            path.relative_to(native).as_posix(): sha256_of(path) for path in published
        },
        **overrides,
    }
    (prefix / "result.json").write_text(json.dumps(result))
    return prefix


def replay_document(
    manifest: Path, digest: str, *, capacity_status: str = "calibrated"
) -> dict[str, object]:
    return {
        "backend": {
            "server_image_uri": "registry/replay@sha256:" + "1" * 64,
            "fixture_sha256": "2" * 64,
            "serving_mode": "sorted",
            "latency_model": {
                "deadlines_ms": {
                    "worker_page": 1,
                    "pivot_probe": 1,
                    "structure_probe": 1,
                },
                "scale": 1.0,
                "jitter": "none",
            },
        },
        "allocation": {
            "subject_vcpus": 4,
            "replay_vcpus": 4,
            "replay_memory_gb": 8,
            "replay_parquet_connections": 20,
            "replay_max_concurrent_requests": 256,
            "replay_prefetch": False,
            "replay_prefetch_max_windows": 96,
            "replay_heap_percent": 75,
        },
        "capacity_status": capacity_status,
    }


def replay_evidence(replay: dict[str, object]) -> ReplayEvidence:
    before = "2026-01-01T00:00:00+00:00"
    after = "2026-01-01T00:00:02+00:00"
    calibrated = replay["capacity_status"] == "calibrated"
    return {
        "replay": replay,
        "replay_evidence": {
            "readiness": {"state": "ready", "wait_ms": 2, "attempts": 1, "last_error": None},
            "before": {
                "observed_at": before,
                "metrics": replay_metrics(0),
            },
            "samples": (
                [
                    {
                        "observed_at": "2026-01-01T00:00:01+00:00",
                        "elapsed_s": 1.0,
                        "metrics": replay_metrics(1),
                    }
                ]
                if calibrated
                else []
            ),
            "resource_samples": (
                [
                    {
                        "observed_at": "2026-01-01T00:00:01+00:00",
                        "elapsed_s": 1.0,
                        "interval_s": 1.0,
                        "server_cpuset": "0-3",
                        "subject_cpuset": "4-7",
                        "server_cpuset_utilization": 0.25,
                        "server_cores_used": 1.0,
                        "subject_cpuset_utilization": 0.5,
                        "subject_cores_used": 2.0,
                        "host_mem_available_kb": 1024,
                        "host_load1": 0.5,
                    }
                ]
                if calibrated
                else []
            ),
            "after": {
                "observed_at": after,
                "metrics": replay_metrics(2),
            },
            "errors": [],
        },
    }


def write_manifest(path: Path, listing: tuple[str, ...] = LISTING) -> str:
    rows = b"".join(
        f"{key}\t{size}\te{size}\t2026-01-01T00:00:00Z\tSTANDARD\n".encode()
        for key, size in (line.split(" ") for line in listing)
    )
    path.write_bytes(gzip.compress(rows, mtime=0))
    return sha256_of(path)


def add_slot(con: sqlite3.Connection, *, state: str, group_id: str = "g1", slot: int = 1) -> None:
    con.execute(
        "INSERT INTO pending (group_id, slot, tool, purpose, known_inputs, awaiting, state, "
        "recorded_at) VALUES (?, ?, 'beta', 'measurement', '{}', 'alpha.aaaa.s1', ?, 'now')",
        (group_id, slot, state),
    )


def agreeing_group(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    """One bucket, two tools, identical listings: the shape a comparison wants."""
    con = fixture_ledger(tmp_path)
    for tool, digest in (("alpha", "aaaa"), ("beta", "bbbb")):
        write_evidence(record(con, tmp_path, tool=tool, digest=digest))
    return con, adapter_root(tmp_path, "alpha", "beta")


def test_agreement_within_one_bucket_passes(tmp_path: Path) -> None:
    con, root = agreeing_group(tmp_path)
    code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    assert (report["verdict"], report["complete"], code) == ("PASS", True, 0)
    assert [stratum["verdict"] for stratum in report["buckets"][0]["strata"]] == ["PASS"]


def test_replay_content_verification_is_refused_without_reading_raw_products(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay = replay_document(tmp_path / "unused.tsv.gz", "0" * 64)
    con = fixture_ledger(tmp_path)
    attempt = record(con, tmp_path, tool="alpha", digest="aaaa", replay=replay)
    write_evidence(attempt, **replay_evidence(replay))
    monkeypatch.setattr(
        verify,
        "stage_evidence",
        lambda *_args, **_kwargs: pytest.fail("replay verifier must not stage raw evidence"),
    )

    code, report = verify.verify_group(
        con, "g1", adapter_root=adapter_root(tmp_path, "alpha"), write_record=True
    )

    assert code == EXIT_INCOMPLETE_GROUP
    assert "row-count-only" in report["refusal"]
    assert report["replay_attempts"] == [attempt.attempt_id]
    assert not (Path(attempt.result_prefix) / "verify.json").exists()


def test_a_product_published_compressed_is_compared_as_its_bytes(tmp_path: Path) -> None:
    """The comparison unpacks a gzipped text product rather than refusing it.

    A text product is uploaded compressed, so the file the sink holds is not the
    file a normalizer reads — and an attempt whose evidence carries the plain
    file is still read as it lies, which is what the other tests here publish.
    """
    con = fixture_ledger(tmp_path)
    for tool, digest in (("alpha", "aaaa"), ("beta", "bbbb")):
        write_evidence(record(con, tmp_path, tool=tool, digest=digest), compress=True)
    root = adapter_root(tmp_path, "alpha", "beta")
    code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    assert (report["verdict"], code) == ("PASS", 0)


def test_each_target_bucket_is_its_own_comparison(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    for bucket, listing in (("bucket-one", LISTING), ("bucket-two", ("b/other 9",))):
        for tool, digest in (("alpha", f"a{bucket[-3:]}"), ("beta", f"b{bucket[-3:]}")):
            write_evidence(
                record(con, tmp_path, tool=tool, digest=digest, bucket=bucket), listing=listing
            )
    root = adapter_root(tmp_path, "alpha", "beta")
    _code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    assert [bucket["target_bucket"] for bucket in report["buckets"]] == [
        "bucket-one",
        "bucket-two",
    ]
    assert [bucket["verdict"] for bucket in report["buckets"]] == ["PASS", "PASS"]


def test_a_blocked_slot_makes_the_group_incomplete(tmp_path: Path) -> None:
    con, root = agreeing_group(tmp_path)
    add_slot(con, state="BLOCKED")
    code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    assert (report["verdict"], report["complete"]) == ("INCOMPLETE", False)
    assert len(report["blocked"]) == 1
    assert code == EXIT_INCOMPLETE_GROUP


def test_an_abandoned_slot_is_an_absent_subject(tmp_path: Path) -> None:
    con, root = agreeing_group(tmp_path)
    add_slot(con, state="ABANDONED")
    _code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    assert (report["verdict"], report["abandoned"]) == (
        "INCOMPLETE",
        ["slot 1 (beta) awaiting alpha.aaaa.s1"],
    )


def test_an_accepted_attempt_is_absent_not_a_smaller_comparison(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(record(con, tmp_path, tool="alpha", digest="aaaa"))
    record(con, tmp_path, tool="beta", digest="bbbb", state="ACCEPTED")
    root = adapter_root(tmp_path, "alpha", "beta")
    _code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    gaps = report["buckets"][0]["gaps"]
    assert [gap["reason"] for gap in gaps] == ["absent"]
    assert report["verdict"] == "INCOMPLETE"


def test_evidence_naming_another_attempt_is_refused(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(record(con, tmp_path, tool="alpha", digest="aaaa"))
    stray = record(con, tmp_path, tool="beta", digest="bbbb")
    write_evidence(stray, attempt_id="beta.cccc.s1")
    root = adapter_root(tmp_path, "alpha", "beta")
    _code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    assert [gap["reason"] for gap in report["buckets"][0]["gaps"]] == ["identity"]


def test_identity_binds_evidence_to_its_prefix_and_its_row() -> None:
    result = {"attempt_id": "alpha.aaaa.s1", "case_id": "alpha.aaaa"}
    assert (
        verify.identity_errors(
            result,
            attempt_id="alpha.aaaa.s1",
            case_id="alpha.aaaa",
            result_prefix="gs://results/suite/bucket/alpha.aaaa.s1/",
        )
        == []
    )
    assert (
        len(
            verify.identity_errors(
                result,
                attempt_id="alpha.aaaa.s1",
                case_id="alpha.aaaa",
                result_prefix="gs://results/suite/bucket/alpha.aaaa.s2/",
            )
        )
        == 1
    )


def test_a_canary_is_not_in_the_population(tmp_path: Path) -> None:
    con, root = agreeing_group(tmp_path)
    record(con, tmp_path, tool="alpha", digest="cccc", purpose="canary", state="FAILED")
    _code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    assert (report["verdict"], report["subjects"]) == ("PASS", 2)


def test_rate_case_failures_are_data_points(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(record(con, tmp_path, tool="alpha", digest="aaaa", statistic="rate"))
    record(con, tmp_path, tool="alpha", digest="aaaa", statistic="rate", state="FAILED")
    root = adapter_root(tmp_path, "alpha")
    _code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    bucket = report["buckets"][0]
    assert bucket["gaps"] == []
    assert bucket["rates"] == [
        {
            "case_id": "alpha.aaaa",
            "tool": "alpha",
            "mode": "text-full",
            "attempts": 2,
            "successes": 1,
            "rate": 0.5,
        }
    ]
    assert report["complete"] is True


def test_provider_success_does_not_hide_a_failed_rate_subject(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    attempt = record(con, tmp_path, tool="alpha", digest="aaaa", statistic="rate")
    write_evidence(attempt, exit_code=124, row_count=None)
    root = adapter_root(tmp_path, "alpha")

    _code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)

    assert report["buckets"][0]["rates"][0] == {
        "case_id": "alpha.aaaa",
        "tool": "alpha",
        "mode": "text-full",
        "attempts": 1,
        "successes": 0,
        "rate": 0.0,
    }


def test_a_mode_is_only_compared_within_its_product_and_field_set(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    for tool, digest, mode in (
        ("alpha", "aaaa", "text-full"),
        ("beta", "bbbb", "text-keys"),
        ("beta", "cccc", "parquet-full"),
    ):
        write_evidence(record(con, tmp_path, tool=tool, digest=digest, mode=mode))
    root = adapter_root(tmp_path, "alpha", "beta")
    _code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=False)
    strata = report["buckets"][0]["strata"]
    assert [(s["product"], s["fields"], s["verdict"]) for s in strata] == [
        ("parquet", ["key", "size", "etag", "mtime", "storage_class"], "UNCOMPARED"),
        ("text", ["key"], "UNCOMPARED"),
        ("text", ["key", "size", "etag", "mtime", "storage_class"], "UNCOMPARED"),
    ]


def test_normalize_is_given_the_rows_recorded_config(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    attempt = record(con, tmp_path, tool="alpha", digest="aaaa")
    prefix = write_evidence(attempt)
    root = adapter_root(tmp_path, "alpha")
    subject = verify.Subject.from_row(ledger.attempt_rows(con)[0])
    adapter_dir = adapters.adapter_dir_for("alpha", root)
    result = json.loads((prefix / "result.json").read_text())
    manifest = adapters.mode_manifest(adapter_dir, "alpha", subject.mode)
    output = tmp_path / "out.tsv"
    verify.normalize_evidence(prefix, result, adapter_dir, subject, manifest, output)
    assert output.read_text().splitlines()[0].split("\t")[0] == "a/one"

    stripped = tmp_path / "stripped.tsv"
    with pytest.raises(adapters.AdapterError):
        verify.normalize_evidence(
            prefix,
            result,
            adapter_dir,
            verify.Subject(**{**vars(subject), "config": {}}),
            manifest,
            stripped,
        )


def test_reference_duplicates_can_never_pass(tmp_path: Path) -> None:
    actual = tmp_path / "actual.tsv"
    reference = tmp_path / "reference.tsv"
    actual.write_text("key\t1\te\t2026-01-01T00:00:00Z\tSTANDARD\n")
    reference.write_text(
        "key\t1\te\t2026-01-01T00:00:00Z\tSTANDARD\nkey\t1\te\t2026-01-01T00:00:00Z\tSTANDARD\n"
    )
    con = duckdb.connect()
    try:
        verify.load_tables(con, reference, actual)
        diff = verify.compute_diff(con)
    finally:
        con.close()
    assert diff["reference_duplicates"] == ["key"]
    assert verify.verdict_for(diff) == "FAIL"


def test_two_empty_listings_pass(tmp_path: Path) -> None:
    actual = tmp_path / "actual.tsv"
    reference = tmp_path / "reference.tsv"
    actual.write_bytes(b"")
    reference.write_bytes(b"")
    con = duckdb.connect()
    try:
        verify.load_tables(con, reference, actual)
        diff = verify.compute_diff(con)
    finally:
        con.close()
    assert diff == {
        "missing": [],
        "extra": [],
        "duplicates": [],
        "reference_duplicates": [],
        "mismatches": [],
    }
    assert verify.verdict_for(diff) == "PASS"


@pytest.mark.parametrize(
    ("actual_etag", "reference_etag"),
    [("etag", "-"), ("-", "etag")],
)
def test_field_is_compared_only_when_both_attempts_expose_it(
    tmp_path: Path, actual_etag: str, reference_etag: str
) -> None:
    actual = tmp_path / "actual.tsv"
    reference = tmp_path / "reference.tsv"
    actual.write_text(f"key\t1\t{actual_etag}\t2026-01-01T00:00:00Z\tSTANDARD\n")
    reference.write_text(f"key\t1\t{reference_etag}\t2026-01-01T00:00:00Z\tSTANDARD\n")
    con = duckdb.connect()
    try:
        verify.load_tables(con, reference, actual)
        diff = verify.compute_diff(con)
    finally:
        con.close()
    assert diff["mismatches"] == []
    assert verify.verdict_for(diff) == "PASS"


CLEAN_EXECUTION: dict[str, object] = {
    "timed_out": False,
    "subreaper_enabled": True,
    "process_tree_clean": True,
    "process_group_empty": True,
    "descendants_empty": True,
    "cgroup": {"oom_kill_delta": 0},
}


@pytest.mark.parametrize(
    "execution",
    [
        pytest.param(None, id="no-execution-evidence"),
        pytest.param({**CLEAN_EXECUTION, "process_tree_clean": False}, id="live-descendant"),
        pytest.param({**CLEAN_EXECUTION, "cgroup": {"oom_kill_delta": 1}}, id="oom-killed"),
        pytest.param(
            {**CLEAN_EXECUTION, "cgroup": {"oom_kill_delta": -1}}, id="invalid-oom-evidence"
        ),
    ],
)
def test_a_zero_exit_subject_is_still_refused_on_dirty_evidence(
    execution: dict[str, object] | None,
) -> None:
    result: dict[str, object] = {"exit_code": 0, "timed_out": False}
    if execution is not None:
        result["execution"] = execution
    assert verify.check_failed_subject(result) is not None
    assert verify.check_failed_subject({**result, "execution": CLEAN_EXECUTION}) is None


def test_artifact_hashes_bind_stream_and_native_bytes(tmp_path: Path) -> None:
    prefix = tmp_path / "evidence"
    native = prefix / "native/data"
    native.mkdir(parents=True)
    stdout = prefix / "stdout.log.gz"
    stderr = prefix / "stderr.log.gz"
    part = native / "part.parquet"
    stdout.write_bytes(b"stdout")
    stderr.write_bytes(b"stderr")
    part.write_bytes(b"native")
    result: dict[str, object] = {
        "stdout": {"name": stdout.name, "size_bytes": 6, "sha256": sha256_of(stdout)},
        "stderr": {"name": stderr.name, "size_bytes": 6, "sha256": sha256_of(stderr)},
        "native_manifest": {"data/part.parquet": sha256_of(part)},
    }
    verify.validate_captured_artifacts(prefix, result)
    part.write_bytes(b"mutated")
    with pytest.raises(adapters.AdapterError, match="native"):
        verify.validate_captured_artifacts(prefix, result)


def test_verify_json_binds_the_comparison_it_recorded(tmp_path: Path) -> None:
    con, root = agreeing_group(tmp_path)
    _code, report = verify.verify_group(con, "g1", adapter_root=root, write_record=True)
    comparison = report["buckets"][0]["strata"][0]["comparisons"][0]
    written = json.loads(
        (
            Path(tmp_path / "evidence" / "bucket-one" / comparison["attempt_id"]) / "verify.json"
        ).read_text()
    )
    result_raw = (
        Path(tmp_path / "evidence" / "bucket-one" / comparison["attempt_id"]) / "result.json"
    ).read_bytes()
    assert written["actual_result_sha256"] == hashlib.sha256(result_raw).hexdigest()
    assert written["verdict"] == verify.verdict_for(written["diff"])
