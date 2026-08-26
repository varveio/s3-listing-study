from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path
from typing import TypedDict

import benchmark.campaign as campaign
import benchmark.ledger as ledger
import benchmark.measure as measure
import benchmark.report as report
import benchmark.verify as verify
from benchmark.contract import sha256_of

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
}}
SUPPORTS_UNSIGNED = True


def build_command(request):
    return ("/usr/bin/{tool}", request.mode)
"""

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


class CompleteReplayEvidence(TypedDict):
    replay: dict[str, object]
    replay_evidence: dict[str, object]
    started_at: str
    finished_at: str


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
    produced_by: str | None = None,
    replay: str | None = None,
) -> ledger.Attempt:
    case_id = f"{tool}.{digest}"
    config = json.dumps({"mode": mode}, sort_keys=True, separators=(",", ":"))

    def build(ordinal: int) -> tuple[ledger.Attempt, str]:
        attempt_id = f"{case_id}.s{ordinal}"
        return (
            ledger.Attempt(
                case_id=case_id,
                attempt=ordinal,
                case_inputs=json.dumps({"case": case_id}),
                group_id="g1",
                tool=tool,
                auth_role=None,
                executor=campaign.EXECUTOR,
                location="us-east1",
                machine_type="n4-standard-2",
                vcpus=10,
                memory_gb=32,
                container_memory_gb=8,
                heap_percent=75,
                timeout_s=600,
                target_bucket=bucket,
                target_region="us-east-1",
                target_prefix="",
                config=config,
                input_artifact_sha256=None,
                produced_by=produced_by,
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
                replay=replay,
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
    wall_seconds: float = 1.5,
    **overrides: object,
) -> Path:
    prefix = Path(attempt.result_prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    stderr_gz = prefix / "stderr.log.gz"
    stderr_gz.write_bytes(gzip.compress(b""))
    native = prefix / "native"
    native.mkdir(exist_ok=True)
    product = native / "listing.txt"
    product.write_bytes(("\n".join(listing) + "\n").encode())
    result: dict[str, object] = {
        "attempt_id": attempt.attempt_id,
        "case_id": attempt.case_id,
        "group_id": attempt.group_id,
        "job_name": attempt.job_name,
        "tool": attempt.tool,
        "mode": json.loads(attempt.config)["mode"],
        "bucket": attempt.target_bucket,
        "region": attempt.target_region,
        "prefix": attempt.target_prefix,
        "auth_role": attempt.auth_role,
        "image": attempt.image_uri,
        "image_set_sha256": attempt.image_set_sha256,
        "config": json.loads(attempt.config),
        "replay": None if attempt.replay is None else json.loads(attempt.replay),
        "replay_evidence": None,
        "declared_resources": {
            "machine_type": attempt.machine_type,
            "vcpus": attempt.vcpus,
            "memory_gb": attempt.memory_gb,
            "container_memory_gb": attempt.container_memory_gb,
        },
        "exit_code": 0,
        "worker_exit_code": overrides.get("worker_exit_code", overrides.get("exit_code", 0)),
        "timed_out": False,
        "wall_seconds": wall_seconds,
        "max_rss_kb": 1024,
        "row_count": len(listing),
        "row_count_error": None,
        "execution": {
            "timed_out": False,
            "subreaper_enabled": True,
            "process_tree_clean": True,
            "process_group_empty": True,
            "descendants_empty": True,
            "max_rss_kb": 1024,
            "elapsed_ns": int(wall_seconds * 1_000_000_000),
            "cgroup": {"oom_delta": 0, "oom_kill_delta": 0},
        },
        "product": {
            "artifact": "listing",
            "name": "native/listing.txt",
            "channel": "stdout",
            "size_bytes": product.stat().st_size,
            "sha256": sha256_of(product),
        },
        "product_error": None,
        # The subject only printed, so fd 1 was the product and there is no log.
        "stdout": None,
        "stderr": {
            "name": stderr_gz.name,
            "size_bytes": stderr_gz.stat().st_size,
            "sha256": sha256_of(stderr_gz),
        },
        "native_manifest": {"listing.txt": sha256_of(product)},
        **overrides,
    }
    (prefix / "result.json").write_text(json.dumps(result))
    return prefix


def verified_group(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    """Two agreeing subjects, verified, so report has real verify.json records."""
    con = fixture_ledger(tmp_path)
    for tool, digest in (("alpha", "aaaa"), ("beta", "bbbb")):
        write_evidence(record(con, tmp_path, tool=tool, digest=digest))
    root = adapter_root(tmp_path, "alpha", "beta")
    verify.verify_group(con, "g1", adapter_root=root, write_record=True)
    return con, root


def replay_document(_unused: Path) -> str:
    return json.dumps(
        {
            "backend": {
                "server_image_uri": "registry/replay@sha256:" + "b" * 64,
                "fixture_sha256": "a" * 64,
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
            "capacity_status": "calibrated",
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def complete_replay_evidence(replay: str) -> CompleteReplayEvidence:
    before = {
        "observed_at": "2026-01-01T00:00:00+00:00",
        "metrics": replay_metrics(0),
    }
    calibrated = json.loads(replay)["capacity_status"] == "calibrated"
    return {
        "replay": json.loads(replay),
        "replay_evidence": {
            "readiness": {"state": "ready", "wait_ms": 1, "attempts": 1, "last_error": None},
            "before": before,
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
                "observed_at": "2026-01-01T00:00:02+00:00",
                "metrics": replay_metrics(2),
            },
            "errors": [],
        },
        "started_at": "2026-01-01T00:00:00.500000+00:00",
        "finished_at": "2026-01-01T00:00:01.500000+00:00",
    }


def rows_of(con: sqlite3.Connection, root: str) -> list[dict[str, object]]:
    return report.report_rows(ledger.attempt_rows(con), adapter_root=root)


def test_report_reads_bound_results_without_consuming_verify_records(tmp_path: Path) -> None:
    con, root = verified_group(tmp_path)
    rows = rows_of(con, root)
    assert {row["evidence_state"] for row in rows} == {"RESULT_BOUND"}
    assert report.report_exit_code(rows, blocked=[]) == 0
    assert "no content comparison" in report.render_markdown(rows, blocked=[])


def test_result_replay_document_is_bound_exactly_to_the_ledger(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    replay = replay_document(tmp_path / "manifest.tsv.gz")
    attempt = record(con, tmp_path, tool="alpha", digest="aaaa", replay=replay)
    complete = {
        "readiness": {"state": "ready", "wait_ms": 1, "attempts": 1, "last_error": None},
        "before": {"observed_at": "now", "metrics": replay_metrics(0)},
        "samples": [{"observed_at": "now", "elapsed_s": 0.1, "metrics": replay_metrics(1)}],
        "resource_samples": [{"server_cpuset": "0-3", "subject_cpuset": "4-7"}],
        "after": {"observed_at": "now", "metrics": replay_metrics(1)},
        "errors": [],
    }
    write_evidence(attempt, replay_evidence=complete)
    root = adapter_root(tmp_path, "alpha")
    row = rows_of(con, root)[0]
    assert row["evidence_state"] == "RESULT_BOUND"
    assert row["replay_state"] == "COMPLETE"

    write_evidence(attempt, replay={"different": True})
    row = rows_of(con, root)[0]
    assert row["evidence_state"] == "RESULT_MISMATCH"


def test_s3_result_must_bind_an_explicit_null_replay(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    attempt = record(con, tmp_path, tool="alpha", digest="aaaa")
    prefix = write_evidence(attempt)
    result_path = prefix / "result.json"
    result = json.loads(result_path.read_text())
    del result["replay"]
    result_path.write_text(json.dumps(result))
    row = rows_of(con, adapter_root(tmp_path, "alpha"))[0]
    assert row["evidence_state"] == "RESULT_MISMATCH"


def test_replay_report_uses_result_json_and_declared_allocations(tmp_path: Path) -> None:
    replay = replay_document(tmp_path / "manifest.tsv.gz")
    con = fixture_ledger(tmp_path)
    attempt = record(con, tmp_path, tool="alpha", digest="aaaa", replay=replay)
    write_evidence(attempt, **complete_replay_evidence(replay))
    root = adapter_root(tmp_path, "alpha")

    row = rows_of(con, root)[0]
    assert (row["evidence_state"], row["replay_state"], row["row_count"]) == (
        "RESULT_BOUND",
        "COMPLETE",
        2,
    )
    assert row["declared_server_allocation"] == "cpus=0-3;memory=8GiB"
    assert row["declared_subject_allocation"] == "cpus=4-7;memory=8GiB"
    assert row["derived_host_headroom"] == "vcpus=2;memory=16GiB"
    assert row["capacity_status"] == "CALIBRATED"


def test_uncalibrated_replay_diagnostic_stays_out_of_publishable_rows(tmp_path: Path) -> None:
    replay = json.loads(replay_document(tmp_path / "manifest.tsv.gz"))
    replay["capacity_status"] = "uncalibrated"
    replay_raw = json.dumps(replay, sort_keys=True, separators=(",", ":"))
    con = fixture_ledger(tmp_path)
    attempt = record(
        con,
        tmp_path,
        tool="alpha",
        digest="aaaa",
        purpose="diagnostic",
        replay=replay_raw,
    )
    write_evidence(attempt, **complete_replay_evidence(replay_raw))
    root = adapter_root(tmp_path, "alpha")

    row = rows_of(con, root)[0]

    assert (row["capacity_status"], row["purpose"], row["evidence_state"]) == (
        "UNCALIBRATED",
        "diagnostic",
        "RESULT_BOUND",
    )
    assert not report.is_timing(row)
    assert "0 successful timing(s)" in report.summary_line([row])


def test_replay_rate_counts_bound_successes_without_inspecting_products(tmp_path: Path) -> None:
    replay = replay_document(tmp_path / "manifest.tsv.gz")
    con = fixture_ledger(tmp_path)
    good = record(con, tmp_path, tool="alpha", digest="aaaa", statistic="rate", replay=replay)
    write_evidence(good, **complete_replay_evidence(replay))
    wrong = record(con, tmp_path, tool="alpha", digest="aaaa", statistic="rate", replay=replay)
    write_evidence(
        wrong,
        listing=("wrong 1",),
        **complete_replay_evidence(replay),
    )
    failed = record(con, tmp_path, tool="alpha", digest="aaaa", statistic="rate", replay=replay)
    write_evidence(
        failed,
        exit_code=2,
        row_count=None,
        **complete_replay_evidence(replay),
    )
    root = adapter_root(tmp_path, "alpha")
    rows = rows_of(con, root)
    assert report.rate_lines(rows) == [
        "- `alpha.aaaa` (alpha text-full): 2/3 succeeded, rate 0.6667 over 3 attempt(s)"
    ]


def test_a_failed_canary_subject_keeps_the_report_incomplete(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    attempt = record(con, tmp_path, tool="alpha", digest="aaaa", purpose="canary")
    write_evidence(attempt, exit_code=124, worker_exit_code=0, row_count=None)

    rows = rows_of(con, adapter_root(tmp_path, "alpha"))

    assert rows[0]["evidence_state"] == "RESULT_BOUND"
    assert (rows[0]["state"], rows[0]["exit"], rows[0]["worker_exit"]) == (
        "SUCCEEDED",
        124,
        0,
    )
    assert report.report_exit_code(rows, blocked=[]) == 1


def test_a_blocked_slot_keeps_the_report_from_being_final(tmp_path: Path) -> None:
    con, root = verified_group(tmp_path)
    rows = rows_of(con, root)
    blocked = ["slot 1 (beta) awaiting alpha.aaaa.s1"]
    assert report.report_exit_code(rows, blocked=blocked) == 1
    assert "Blocked slot" in report.render_markdown(rows, blocked=blocked)


def test_a_rate_case_renders_a_rate_and_a_sample_size(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(record(con, tmp_path, tool="alpha", digest="aaaa", statistic="rate"))
    record(con, tmp_path, tool="alpha", digest="aaaa", statistic="rate", state="FAILED")
    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    assert report.rate_lines(rows) == [
        "- `alpha.aaaa` (alpha text-full): 1/2 succeeded, rate 0.5000 over 2 attempt(s)"
    ]
    # A rate case's surviving attempt is not a verified timing: the statistic is
    # the rate, and a mean over the survivors would be a survivorship result.
    assert "0 successful timing(s)" in report.summary_line(rows)


def test_accepted_count_failure_is_not_a_successful_timing(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    attempt = record(con, tmp_path, tool="alpha", digest="aaaa", state="ACCEPTED")
    write_evidence(attempt, row_count=None, row_count_error="count failed")

    rows = rows_of(con, adapter_root(tmp_path, "alpha"))

    assert rows[0]["evidence_state"] == "RESULT_BOUND"
    assert "0 successful timing(s)" in report.summary_line(rows)


def test_a_preparations_cost_rides_with_the_timing_it_enabled(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    preparation = record(con, tmp_path, tool="alpha", digest="prep", purpose="preparation")
    write_evidence(preparation, wall_seconds=40.0)
    measurement = record(
        con, tmp_path, tool="alpha", digest="aaaa", produced_by=preparation.attempt_id
    )
    write_evidence(measurement, wall_seconds=60.0)
    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    measured = next(row for row in rows if row["attempt_id"] == measurement.attempt_id)
    assert measured["prep_seconds"] == 40.0
    assert report.preparation_lines(rows) == [
        f"- `{measurement.attempt_id}` ran behind {preparation.attempt_id} (40.0s of preparation)"
    ]


def test_a_preparation_from_another_group_is_a_cost_this_report_cannot_state(
    tmp_path: Path,
) -> None:
    """Summing only the links that are here is a smaller number wearing a total's name."""
    con = fixture_ledger(tmp_path)
    reused = record(con, tmp_path, tool="alpha", digest="bbbb", produced_by="alpha.deadbeef.s1")
    write_evidence(reused, wall_seconds=70.0)
    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    assert rows[0]["prep_seconds"] == "-"
    assert "crosses a group boundary" in report.preparation_lines(rows)[0]


def test_each_bucket_is_its_own_section(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    for bucket, tool in (("bucket-one", "alpha"), ("bucket-two", "beta")):
        write_evidence(record(con, tmp_path, tool=tool, digest=bucket[-3:], bucket=bucket))
    rows = rows_of(con, adapter_root(tmp_path, "alpha", "beta"))
    markdown = report.render_markdown(rows, blocked=[])
    assert markdown.count("## bucket-") == 2


def test_a_canary_is_reported_without_becoming_a_timing(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(record(con, tmp_path, tool="alpha", digest="aaaa"))
    write_evidence(record(con, tmp_path, tool="alpha", digest="cccc", purpose="canary"))
    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    assert len(rows) == 2
    assert sum(report.is_timing(row) for row in rows) == 1


def test_evidence_naming_another_attempt_is_refused(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(record(con, tmp_path, tool="alpha", digest="aaaa"), attempt_id="alpha.zzzz.s1")
    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    assert rows[0]["evidence_state"] == "IDENTITY_MISMATCH"
    assert rows[0]["wall_seconds"] == "-"


def test_evidence_that_ran_another_config_is_refused(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(
        record(con, tmp_path, tool="alpha", digest="aaaa"),
        config={"mode": "text-full", "concurrency": 8},
    )
    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    assert rows[0]["evidence_state"] == "RESULT_MISMATCH"


def test_a_subject_killed_before_its_product_reports_its_failure_not_a_mismatch(
    tmp_path: Path,
) -> None:
    """An OOM is an honest failure, and the row has to say so.

    `s3-fast-list list` on a 20M-object bucket dies before its output file is
    opened. Recording that as a product whose digest is null made the marker
    unreadable to `report`, which then blanked the exit code, the wall clock and
    the peak RSS behind `RESULT_MISMATCH` — telling the reader the evidence is
    corrupt when what happened is that the tool ran out of memory.
    """
    con = fixture_ledger(tmp_path)
    attempt = record(con, tmp_path, tool="alpha", digest="aaaa", state="FAILED")
    stdout_gz = Path(attempt.result_prefix)
    stdout_gz.mkdir(parents=True, exist_ok=True)
    stdout_gz = stdout_gz / "stdout.log.gz"
    stdout_gz.write_bytes(gzip.compress(b"listing 4000000 keys\n"))
    prefix = write_evidence(
        attempt,
        exit_code=137,
        row_count=None,
        # The writes-a-file class: nothing landed at the declared path, and
        # stdout is the log it always was.
        product=None,
        native_manifest={},
        stdout={
            "name": stdout_gz.name,
            "size_bytes": stdout_gz.stat().st_size,
            "sha256": sha256_of(stdout_gz),
        },
    )
    document = json.loads((prefix / "result.json").read_text())
    (prefix / "native/listing.txt").unlink()

    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    assert verify.result_semantic_errors(document) == []
    assert rows[0]["evidence_state"] == "RESULT_BOUND"
    assert (rows[0]["exit"], rows[0]["max_rss_kb"]) == (137, 1024)


def test_a_setup_failure_reads_as_evidence_rather_than_a_broken_result(tmp_path: Path) -> None:
    """An attempt whose inline setup exec failed publishes a marker with no
    execution in it, and both readers must take it for what it is.

    verify refuses on the subject's own exit code before it ever reaches the
    execution block; report reads that block for every attempt with a marker, so
    the null has to mean "the subject never ran" rather than "malformed".
    """
    con = fixture_ledger(tmp_path)
    prefix = write_evidence(
        record(con, tmp_path, tool="alpha", digest="aaaa", state="FAILED"),
        exit_code=measure.EXIT_SETUP_FAILED,
        execution=None,
        max_rss_kb=None,
        row_count=None,
        native_manifest={},
        product=None,
        setup={
            "mode": "prep",
            "command": ["/usr/bin/alpha", "prep"],
            "exit_code": 3,
            "wall_s": 0.2,
            "output": {},
            "validated": False,
        },
    )
    document = json.loads((prefix / "result.json").read_text())
    # `write_evidence` derives the timing from a duration; a setup failure has
    # none to derive it from.
    document["wall_seconds"] = None
    (prefix / "result.json").write_text(json.dumps(document))

    assert verify.result_semantic_errors(document) == []
    assert verify.check_failed_subject(document) == f"subject exited {measure.EXIT_SETUP_FAILED}"
    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    assert rows[0]["evidence_state"] == "RESULT_BOUND"
    # And the null is still load-bearing: a zero exit beside no execution is a
    # result that contradicts itself.
    assert verify.result_semantic_errors({**document, "exit_code": 0}) == ["exit_code"]


def test_an_rss_figure_is_rendered_beside_the_floor_it_sits_on(tmp_path: Path) -> None:
    """The floor travels with the figure, and its absence is not a zero.

    A figure near the mark a fork handed the child has measured nothing about
    the subject, and only the floor beside it says which case this is. Evidence
    written before the worker recorded one renders `-` rather than inviting a
    reader to treat a missing floor as no floor.
    """
    con = fixture_ledger(tmp_path)
    write_evidence(
        record(con, tmp_path, tool="alpha", digest="aaaa"),
        execution={
            "timed_out": False,
            "subreaper_enabled": True,
            "process_tree_clean": True,
            "process_group_empty": True,
            "descendants_empty": True,
            "max_rss_kb": 1024,
            "max_rss_floor_kb": 900,
            "max_rss_floor_reset": True,
            "elapsed_ns": 1_500_000_000,
            "cgroup": {"oom_delta": 0, "oom_kill_delta": 0},
        },
    )
    write_evidence(record(con, tmp_path, tool="beta", digest="bbbb"))

    rows = {row["tool"]: row for row in rows_of(con, adapter_root(tmp_path, "alpha", "beta"))}
    assert rows["alpha"]["max_rss_kb"] == 1024
    assert rows["alpha"]["max_rss_floor_kb"] == 900
    assert rows["beta"]["max_rss_floor_kb"] == "-"
    assert "max_rss_floor_kb" in report.render_markdown(list(rows.values()), blocked=[])


def test_report_ignores_a_verify_record_and_keeps_the_bound_result(
    tmp_path: Path,
) -> None:
    con, root = verified_group(tmp_path)
    rows = rows_of(con, root)
    compared = rows[0]
    path = Path(str(tmp_path / "evidence" / "bucket-one" / str(compared["attempt_id"])))
    (path / "verify.json").write_text('{"untrusted": "derived content"}')
    rebuilt = rows_of(con, root)
    mismatched = next(row for row in rebuilt if row["attempt_id"] == compared["attempt_id"])
    assert mismatched["evidence_state"] == "RESULT_BOUND"
    assert report.report_exit_code(rebuilt, blocked=[]) == 0
