"""The public export must be allowlisted, deterministic, and self-validating."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import benchmark.ledger as ledger
import benchmark.public_export as public_export
import benchmark.public_validate as public_validate
import benchmark.report as report
from tests.test_report import (
    adapter_root,
    fixture_ledger,
    record,
    replay_document,
    write_evidence,
)

GOLDEN = Path(__file__).parent / "golden" / "public-export-attempts.jsonl"
COMMIT = "0" * 40
# A synthetic ledger is stamped as it is built, so the golden holds the shape and
# the values, with the wall-clock fields of that particular build stood down.
CLOCK = re.compile(r'"(recorded_at|settled_at|started_at|finished_at)":"[^"]*"')
# The fixture's own evidence is gzip, which stamps the hour it was written, so
# the digests *of that evidence* move per run. The digests of the inputs the
# ledger froze -- image, tool slice, platform, fixture, argv -- do not, and the
# golden still holds those.
EVIDENCE_DIGEST = re.compile(
    r'("kind":"[a-z-]+","published":(?:true|false),"reason":"[^"]*","sha256":")[0-9a-f]{64}"'
)


def stable(text: str) -> str:
    return EVIDENCE_DIGEST.sub(r'\1<digest>"', CLOCK.sub(r'"\1":"<timestamp>"', text))


SPEC = """
spec_version: 1
release_id: test-release
title: Test release
status: diagnostic
claim_ceiling:
  controlled_replay_diagnostics: true
  calibrated_replay_benchmark: false
include: all-terminal
exclusions: []
source_ledger:
  suite: fixture
  schema_version: 3
plans:
  g1: benchmark/plans/tools.yaml
"""

CHARTS = """
spec_version: 1
charts:
  - id: alpha-wall
    status: rendered
    kind: bars
    title: Wall clock
    select:
      pick: best-successful-wall-per-tool
      where:
        classification.purpose: diagnostic
    metric: outcome.wall_seconds
    axis_label: wall seconds
    series_label: "{tool.name} {tool.mode}"
    bar_label: "[{classification.replay_timing}]"
    caption: diagnostic; not a benchmark
    csv_columns: [attempt_id, tool.name, outcome.wall_seconds]
  - id: later
    status: pending
"""


def spec_at(tmp_path: Path, body: str = SPEC) -> Path:
    path = tmp_path / "release.yaml"
    path.write_text(body)
    return path


def charts_at(tmp_path: Path) -> Path:
    path = tmp_path / "charts.yaml"
    path.write_text(CHARTS)
    return path


def tiny_campaign(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    """One replay diagnostic, one preparation, one failure with no evidence."""
    con = fixture_ledger(tmp_path)
    replay = replay_document(tmp_path)
    write_evidence(
        record(con, tmp_path, tool="alpha", digest="aaaa", purpose="diagnostic", replay=replay),
        wall_seconds=2.5,
        argv=[
            "/usr/bin/alpha",
            "--out",
            "/tmp/attempt/native",
            "--endpoint-url",
            "http://10.0.0.1:19090",
        ],
        tool_version="1.2.3",
    )
    write_evidence(
        record(con, tmp_path, tool="beta", digest="bbbb", purpose="preparation"),
        wall_seconds=0.5,
        argv=["/usr/bin/beta"],
        tool_version="0.9",
    )
    record(con, tmp_path, tool="alpha", digest="cccc", purpose="diagnostic", state="FAILED")
    return con, adapter_root(tmp_path, "alpha", "beta")


def export(tmp_path: Path, output: Path, *, body: str = SPEC) -> public_export.Release:
    con, root = tiny_campaign(tmp_path)
    release = public_export.build_release(
        ledger.attempt_rows(con),
        spec=public_export.load_release_spec(spec_at(tmp_path, body)),
        commit=COMMIT,
        repo_root=Path(report.DEFAULT_ADAPTER_ROOT).parent.resolve(),
        adapter_root=root,
    )
    public_export.render_release(release, output, chart_spec=charts_at(tmp_path))
    return release


def test_export_matches_the_golden_rows(tmp_path: Path) -> None:
    release = export(tmp_path, tmp_path / "out")
    written = stable((tmp_path / "out" / "test-release" / "attempts.jsonl").read_text())
    if GOLDEN.read_text() != written:  # pragma: no cover - only on an intended change
        refreshed = tmp_path / "refreshed.jsonl"
        refreshed.write_text(written)
        pytest.fail(
            "public export drifted from the golden rows. Review the diff, then refresh with:\n"
            f"  cp {refreshed} {GOLDEN}\n"
        )
    assert [row["attempt_id"] for row in release.rows] == [
        "alpha.aaaa.s1",
        "alpha.cccc.s1",
        "beta.bbbb.s1",
    ]


def test_private_locations_are_normalised_out_of_argv(tmp_path: Path) -> None:
    release = export(tmp_path, tmp_path / "out")
    invocation = release.rows[0]["invocation"]
    assert invocation["argv"] == [
        "/usr/bin/alpha",
        "--out",
        "<attempt-dir>/native",
        "--endpoint-url",
        "<replay-endpoint>",
    ]
    assert invocation["paths_normalized"] is True
    assert invocation["provenance"] == "recorded"
    assert len(invocation["original_sha256"]) == 64


def test_a_diagnostic_never_exports_as_a_measurement(tmp_path: Path) -> None:
    release = export(tmp_path, tmp_path / "out")
    statuses = {row["classification"]["publication_status"] for row in release.rows}
    assert statuses == {"diagnostic", "preparation"}


def test_an_absent_metric_is_null_never_zero(tmp_path: Path) -> None:
    release = export(tmp_path, tmp_path / "out")
    failed = next(row for row in release.rows if row["attempt_id"] == "alpha.cccc.s1")
    assert failed["outcome"]["wall_seconds"] is None
    assert failed["outcome"]["row_count"] is None
    assert failed["state"]["evidence"] == "MISSING_EVIDENCE"


def test_the_allowlist_refuses_a_leaked_column(tmp_path: Path) -> None:
    release = export(tmp_path, tmp_path / "out")
    leaked = {**release.rows[0], "job_name": "nbm-capacity-alpha-s1"}
    with pytest.raises(public_export.ExportError, match="not in the public allowlist"):
        public_export.vet_row(leaked)

    nested = json.loads(json.dumps(release.rows[0]))
    nested["state"]["service_account"] = "worker@example.iam.gserviceaccount.com"
    with pytest.raises(public_export.ExportError, match="not in the public allowlist"):
        public_export.vet_row(nested)


def test_a_private_string_in_an_allowlisted_field_is_refused(tmp_path: Path) -> None:
    release = export(tmp_path, tmp_path / "out")
    smuggled = json.loads(json.dumps(release.rows[0]))
    smuggled["state"]["detail"] = "see gs://a-private-result-store/a-suite/"
    with pytest.raises(public_export.ExportError, match="private result-store URI"):
        public_export.vet_row(smuggled)


def test_export_is_byte_identical_when_run_twice(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    con, root = tiny_campaign(tmp_path)
    spec = public_export.load_release_spec(spec_at(tmp_path))
    for output in (first, second):
        release = public_export.build_release(
            ledger.attempt_rows(con),
            spec=spec,
            commit=COMMIT,
            repo_root=Path(report.DEFAULT_ADAPTER_ROOT).parent.resolve(),
            adapter_root=root,
        )
        public_export.render_release(release, output, chart_spec=charts_at(tmp_path))
    left = sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file())
    right = sorted(path.relative_to(second) for path in second.rglob("*") if path.is_file())
    assert left == right
    for relative in left:
        assert (first / relative).read_bytes() == (second / relative).read_bytes(), relative


def test_a_pending_chart_is_declared_but_not_drawn(tmp_path: Path) -> None:
    export(tmp_path, tmp_path / "out")
    charts = tmp_path / "out" / "test-release" / "charts"
    assert sorted(path.name for path in charts.iterdir()) == ["alpha-wall.csv", "alpha-wall.svg"]
    assert "<script" not in (charts / "alpha-wall.svg").read_text()


def test_the_validator_passes_a_freshly_exported_release(tmp_path: Path) -> None:
    export(tmp_path, tmp_path / "out")
    assert public_validate.validate(tmp_path / "out" / "test-release") == []


def test_the_validator_catches_a_hand_edited_row(tmp_path: Path) -> None:
    export(tmp_path, tmp_path / "out")
    directory = tmp_path / "out" / "test-release"
    rows = directory.joinpath("attempts.jsonl").read_text().splitlines()
    edited = json.loads(rows[0])
    edited["outcome"]["wall_seconds"] = 0.001
    rows[0] = json.dumps(edited, sort_keys=True, separators=(",", ":"))
    directory.joinpath("attempts.jsonl").write_text("\n".join(rows) + "\n")
    problems = public_validate.validate(directory)
    assert any("sha256" in problem for problem in problems)


def test_the_validator_catches_a_leaked_path_in_a_generated_file(tmp_path: Path) -> None:
    export(tmp_path, tmp_path / "out")
    directory = tmp_path / "out" / "test-release"
    directory.joinpath("subjects.json").write_text('{"leak": "/home/someone/campaign.db"}\n')
    assert any("home-directory path" in problem for problem in public_validate.validate(directory))


def test_the_validator_catches_a_diagnostic_relabelled_as_a_measurement(tmp_path: Path) -> None:
    export(tmp_path, tmp_path / "out")
    directory = tmp_path / "out" / "test-release"
    rows = [
        json.loads(line) for line in directory.joinpath("attempts.jsonl").read_text().splitlines()
    ]
    rows[0]["classification"]["publication_status"] = "measurement"
    directory.joinpath("attempts.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    )
    assert any(
        "without a calibrated capacity" in problem
        for problem in public_validate.validate(directory)
    )


def test_the_validator_catches_a_summary_that_lost_a_row(tmp_path: Path) -> None:
    export(tmp_path, tmp_path / "out")
    directory = tmp_path / "out" / "test-release"
    lines = directory.joinpath("summary.csv").read_text().splitlines()
    directory.joinpath("summary.csv").write_text("\n".join(lines[:-1]) + "\n")
    assert any("summary.csv has" in problem for problem in public_validate.validate(directory))


def test_the_release_spec_refuses_a_selective_release(tmp_path: Path) -> None:
    body = SPEC.replace("include: all-terminal", "include: hand-picked")
    with pytest.raises(public_export.ExportError, match="only implements 'all-terminal'"):
        public_export.load_release_spec(spec_at(tmp_path, body))


def test_a_withheld_workload_leaves_the_release_entirely(tmp_path: Path) -> None:
    con = fixture_ledger(tmp_path)
    write_evidence(
        record(con, tmp_path, tool="alpha", digest="aaaa", purpose="diagnostic"),
        wall_seconds=2.5,
        argv=["/usr/bin/alpha"],
        tool_version="1.2.3",
    )
    write_evidence(
        record(con, tmp_path, tool="alpha", digest="dddd", purpose="diagnostic", bucket="licensed"),
        wall_seconds=3.5,
        argv=["/usr/bin/alpha"],
        tool_version="1.2.3",
    )
    body = SPEC + "withheld_workloads:\n  licensed: the dataset licence forbids publication\n"
    release = public_export.build_release(
        ledger.attempt_rows(con),
        spec=public_export.load_release_spec(spec_at(tmp_path, body)),
        commit=COMMIT,
        repo_root=Path(report.DEFAULT_ADAPTER_ROOT).parent.resolve(),
        adapter_root=adapter_root(tmp_path, "alpha"),
    )
    assert [row["target"]["bucket"] for row in release.rows] == ["bucket-one"]
    assert release.manifest["counts"]["attempts"] == 1
    assert release.manifest["counts"]["withheld_attempts"] == 1
    assert "licensed" not in json.dumps(release.manifest)
    withheld = [d for d in release.manifest["disclosures"] if d["id"] == "workloads-withheld"]
    assert withheld and withheld[0]["affects"] == "1 attempt(s) across 1 workload(s)"
