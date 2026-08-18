from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

from benchmark import campaign, ledger, measure, report, verify
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
        "declared_resources": {
            "machine_type": attempt.machine_type,
            "vcpus": attempt.vcpus,
            "memory_gb": attempt.memory_gb,
            "container_memory_gb": attempt.container_memory_gb,
        },
        "exit_code": 0,
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


def rows_of(con: sqlite3.Connection, root: str) -> list[dict[str, object]]:
    return report.report_rows(ledger.attempt_rows(con), adapter_root=root)


def test_a_verified_group_reports_its_verdicts(tmp_path: Path) -> None:
    con, root = verified_group(tmp_path)
    rows = rows_of(con, root)
    assert {row["evidence_state"] for row in rows} == {"VERIFIED", "VERIFY_UNAVAILABLE"}
    assert report.report_exit_code(rows, blocked=[]) == 0
    assert "PASS" in report.render_markdown(rows, blocked=[])


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
    assert "0 verified timing(s)" in report.summary_line(rows)
    assert report.stratum_lines(rows) == []


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
    # The preparation is measured, never compared.
    assert report.stratum_lines(rows) == [
        "- **text** [key,size,etag,mtime,storage_class]: 1 attempt(s) -- alpha/text-full "
        "-- verdicts -"
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


def test_each_bucket_is_its_own_section_and_its_own_strata(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    for bucket, tool in (("bucket-one", "alpha"), ("bucket-two", "beta")):
        write_evidence(record(con, tmp_path, tool=tool, digest=bucket[-3:], bucket=bucket))
    rows = rows_of(con, adapter_root(tmp_path, "alpha", "beta"))
    markdown = report.render_markdown(rows, blocked=[])
    assert markdown.count("## bucket-") == 2
    for bucket, tool in (("bucket-one", "alpha"), ("bucket-two", "beta")):
        section = [row for row in rows if row["bucket"] == bucket]
        assert report.stratum_lines(section) == [
            f"- **text** [key,size,etag,mtime,storage_class]: 1 attempt(s) -- {tool}/text-full "
            "-- verdicts -"
        ]


def test_a_key_only_mode_is_not_ranked_against_a_five_field_one(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(record(con, tmp_path, tool="alpha", digest="aaaa"))
    write_evidence(record(con, tmp_path, tool="beta", digest="bbbb", mode="text-keys"))
    rows = rows_of(con, adapter_root(tmp_path, "alpha", "beta"))
    assert [line.split(":")[0] for line in report.stratum_lines(rows)] == [
        "- **text** [key]",
        "- **text** [key,size,etag,mtime,storage_class]",
    ]


def test_a_canary_is_not_a_comparison_subject(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(record(con, tmp_path, tool="alpha", digest="aaaa"))
    write_evidence(record(con, tmp_path, tool="alpha", digest="cccc", purpose="canary"))
    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    assert len(rows) == 2
    assert report.stratum_lines(rows) == [
        "- **text** [key,size,etag,mtime,storage_class]: 1 attempt(s) -- alpha/text-full "
        "-- verdicts -"
    ]


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
    assert report.result_semantic_errors(document) == []
    assert rows[0]["evidence_state"] == "VERIFY_UNAVAILABLE"
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

    assert report.result_semantic_errors(document) == []
    assert verify.check_failed_subject(document) == f"subject exited {measure.EXIT_SETUP_FAILED}"
    rows = rows_of(con, adapter_root(tmp_path, "alpha"))
    assert rows[0]["evidence_state"] == "VERIFY_UNAVAILABLE"
    # And the null is still load-bearing: a zero exit beside no execution is a
    # result that contradicts itself.
    assert report.result_semantic_errors({**document, "exit_code": 0}) == ["exit_code"]


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


def test_a_verify_record_that_disagrees_with_its_own_diff_is_not_a_verdict(
    tmp_path: Path,
) -> None:
    con, root = verified_group(tmp_path)
    rows = rows_of(con, root)
    compared = next(row for row in rows if row["evidence_state"] == "VERIFIED")
    path = Path(str(tmp_path / "evidence" / "bucket-one" / str(compared["attempt_id"])))
    record_json = json.loads((path / "verify.json").read_text())
    record_json["diff"]["missing"] = ["a/three"]
    (path / "verify.json").write_text(json.dumps(record_json))
    rebuilt = rows_of(con, root)
    mismatched = next(row for row in rebuilt if row["attempt_id"] == compared["attempt_id"])
    assert (mismatched["evidence_state"], mismatched["verdict"]) == ("VERIFY_MISMATCH", "-")
    assert report.report_exit_code(rebuilt, blocked=[]) == 1
