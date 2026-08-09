"""The bucket plan reader: cascade, matrix expansion, case identity, refusals."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from s3_listing_study.manager.bench import cli as bench_cli
from s3_listing_study.manager.bench import plan as bench

MINIMAL = """
spec_version: 1
bucket: {bucket}
region: us-east-1
defaults:
  reps: 3
  timeout_s: 3600
  resources:
    machine_type: e2-standard-4
    memory_mib: 8192
    cpu_milli: 4000
tools:
{tools}
"""


def write(tmp_path: Path, tools: str, *, bucket: str = "b", extra: str = "") -> Path:
    path = tmp_path / f"{bucket}.yaml"
    body = MINIMAL.format(bucket=bucket, tools=textwrap.indent(textwrap.dedent(tools), "  "))
    path.write_text(body + extra, encoding="utf-8")
    return path


ONE_CASE = """
aws-cli:
  matrix:
    mode: [s3api-v2-text]
"""


# ── the shipped plan ─────────────────────────────────────────────────────────


def test_the_committed_plan_loads() -> None:
    """The plan in the tree is the one a campaign would submit; keep it valid."""
    loaded = bench.Plan.load(bench.default_path("noaa-ghcn-pds"))
    assert loaded.bucket == "noaa-ghcn-pds"
    assert loaded.region == "us-east-1"
    assert len(loaded.tools()) == 11
    # Ten sampled tools plus swath's 3 modes x 2 memory sizes.
    assert len(loaded.cases) == 16
    assert len(loaded.cases_for("swath")) == 6


def test_the_committed_plan_matches_the_registered_tools() -> None:
    """The roster rule only means anything if the shipped plan actually obeys it."""
    root = Path(__file__).resolve().parents[1]
    registered = {p.parents[1].name for p in root.glob("tools/*/build/image.json")}
    bench.check_roster(bench.Plan.load(bench.default_path("noaa-ghcn-pds")), registered)


# ── expansion and cascade ────────────────────────────────────────────────────


def test_a_matrix_expands_to_its_cross_product(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        swath:
          matrix:
            mode: [recursive-tsv, recursive-parquet]
            memory_mib: [2048, 4096]
        """,
    )
    ids = [case.case_id for case in bench.Plan.load(path).cases]
    assert ids == [
        "recursive-tsv.memory_mib-2048",
        "recursive-tsv.memory_mib-4096",
        "recursive-parquet.memory_mib-2048",
        "recursive-parquet.memory_mib-4096",
    ]


def test_an_axis_overrides_the_tool_which_overrides_the_defaults(tmp_path: Path) -> None:
    """Three layers, shallow and per-key: the nearest statement of a key wins."""
    path = write(
        tmp_path,
        """
        swath:
          resources:
            memory_mib: 16384
            cpu_milli: 8000
          matrix:
            mode: [recursive-tsv]
            memory_mib: [2048]
        """,
    )
    case = bench.Plan.load(path).cases[0]
    assert case.resources.memory_mib == 2048  # axis
    assert case.resources.cpu_milli == 8000  # tool level
    assert case.resources.machine_type == "e2-standard-4"  # defaults


def test_a_tool_may_override_the_schedule(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        ps3:
          timeout_s: 7200
          matrix:
            mode: [list]
        """,
    )
    case = bench.Plan.load(path).cases[0]
    assert (case.timeout_s, case.reps) == (7200, 3)


# ── identity ─────────────────────────────────────────────────────────────────


def test_a_single_valued_axis_still_appears_in_the_id(tmp_path: Path) -> None:
    """Otherwise the ID would mean "whatever the default was", unrecoverably."""
    path = write(
        tmp_path,
        """
        swath:
          matrix:
            mode: [recursive-tsv]
            memory_mib: [2048]
        """,
    )
    assert bench.Plan.load(path).cases[0].case_id == "recursive-tsv.memory_mib-2048"


def test_resource_changes_move_the_fingerprint(tmp_path: Path) -> None:
    """The guard that stops an edited case appending into its own old evidence."""
    path = write(
        tmp_path,
        """
        swath:
          matrix:
            mode: [recursive-tsv]
            memory_mib: [2048, 4096]
        """,
    )
    first, second = bench.Plan.load(path).cases
    assert first.fingerprint != second.fingerprint


def test_reps_are_not_part_of_identity(tmp_path: Path) -> None:
    """How many times we ran something is not part of what we ran."""
    fingerprints = []
    for reps in (3, 7):
        # Same bucket, separate directories: the bucket name is part of identity,
        # so varying it here would prove nothing about reps.
        directory = tmp_path / str(reps)
        directory.mkdir()
        path = directory / "b.yaml"
        path.write_text(
            MINIMAL.format(bucket="b", tools=textwrap.indent(ONE_CASE, "  ")).replace(
                "reps: 3", f"reps: {reps}"
            ),
            encoding="utf-8",
        )
        fingerprints.append(bench.Plan.load(path).cases[0].fingerprint)
    assert fingerprints[0] == fingerprints[1]


def test_timeout_is_part_of_identity(tmp_path: Path) -> None:
    """A timeout can truncate a run, so it can change the result."""
    fingerprints = []
    for timeout in (3600, 7200):
        directory = tmp_path / str(timeout)
        directory.mkdir()
        path = directory / "b.yaml"
        path.write_text(
            MINIMAL.format(bucket="b", tools=textwrap.indent(ONE_CASE, "  ")).replace(
                "timeout_s: 3600", f"timeout_s: {timeout}"
            ),
            encoding="utf-8",
        )
        fingerprints.append(bench.Plan.load(path).cases[0].fingerprint)
    assert fingerprints[0] != fingerprints[1]


# ── refusals ─────────────────────────────────────────────────────────────────


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    """An unknown key is a misspelling of a real one, and would silently do nothing."""
    path = write(tmp_path, ONE_CASE, extra="concurrency: 8\n")
    with pytest.raises(bench.PlanError, match="unknown key"):
        bench.Plan.load(path)


def test_an_unsupported_spec_version_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, ONE_CASE)
    path.write_text(
        path.read_text(encoding="utf-8").replace("spec_version: 1", "spec_version: 2"),
        encoding="utf-8",
    )
    with pytest.raises(bench.PlanError, match="spec_version"):
        bench.Plan.load(path)


def test_a_filename_that_disagrees_with_the_bucket_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, ONE_CASE, bucket="b")
    renamed = path.with_name("other.yaml")
    path.rename(renamed)
    with pytest.raises(bench.PlanError, match="is named"):
        bench.Plan.load(renamed)


def test_a_matrix_without_a_mode_axis_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        swath:
          matrix:
            memory_mib: [2048]
        """,
    )
    with pytest.raises(bench.PlanError, match="no 'mode' axis"):
        bench.Plan.load(path)


def test_a_repeated_axis_value_is_refused(tmp_path: Path) -> None:
    """Two identical cases would collide on one directory and look like reps."""
    path = write(
        tmp_path,
        """
        swath:
          matrix:
            mode: [recursive-tsv]
            memory_mib: [2048, 2048]
        """,
    )
    with pytest.raises(bench.PlanError, match="repeats a value"):
        bench.Plan.load(path)


def test_incomplete_defaults_are_refused(tmp_path: Path) -> None:
    path = write(tmp_path, ONE_CASE)
    path.write_text(
        path.read_text(encoding="utf-8").replace("    cpu_milli: 4000\n", ""), encoding="utf-8"
    )
    with pytest.raises(bench.PlanError, match="missing cpu_milli"):
        bench.Plan.load(path)


def test_a_yaml_bool_is_not_a_memory_size(tmp_path: Path) -> None:
    """YAML 1.1 reads a bare ``yes`` as True, and ``isinstance(True, int)`` holds."""
    path = write(
        tmp_path,
        """
        swath:
          matrix:
            mode: [recursive-tsv]
            memory_mib: [yes]
        """,
    )
    with pytest.raises(bench.PlanError, match="positive integer"):
        bench.Plan.load(path)


def test_running_and_excluding_the_same_tool_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        ONE_CASE,
        extra="exclude:\n  - tool: aws-cli\n    reason: contradicts itself\n",
    )
    with pytest.raises(bench.PlanError, match="both runs and excludes"):
        bench.Plan.load(path)


def test_an_exclusion_without_a_reason_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, ONE_CASE, extra="exclude:\n  - tool: s3p\n")
    with pytest.raises(bench.PlanError, match="reason"):
        bench.Plan.load(path)


# ── cross-checks ─────────────────────────────────────────────────────────────


def test_a_registered_tool_the_plan_ignores_is_refused(tmp_path: Path) -> None:
    """Registering a tool and forgetting a campaign is the mistake this catches."""
    loaded = bench.Plan.load(write(tmp_path, ONE_CASE))
    with pytest.raises(bench.PlanError, match="does not mention s5cmd"):
        bench.check_roster(loaded, {"aws-cli", "s5cmd"})


def test_an_excluded_tool_satisfies_the_roster(tmp_path: Path) -> None:
    loaded = bench.Plan.load(
        write(tmp_path, ONE_CASE, extra="exclude:\n  - tool: s5cmd\n    reason: not yet built\n")
    )
    bench.check_roster(loaded, {"aws-cli", "s5cmd"})


def test_an_unregistered_tool_is_refused(tmp_path: Path) -> None:
    loaded = bench.Plan.load(write(tmp_path, ONE_CASE))
    with pytest.raises(bench.PlanError, match="unregistered"):
        bench.check_roster(loaded, set())


def test_a_mode_the_adapter_lacks_is_refused(tmp_path: Path) -> None:
    """Caught before submission rather than at Batch runtime."""
    loaded = bench.Plan.load(write(tmp_path, ONE_CASE))
    with pytest.raises(bench.PlanError, match="no mode 's3api-v2-text'"):
        bench.check_modes(loaded, {"aws-cli": {"s3-ls-recursive"}})


# ── the resolve-plan dry run ─────────────────────────────────────────────────


def test_resolve_plan_expands_the_committed_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert bench_cli.resolve_plan_main(["--bucket", "noaa-ghcn-pds"]) == 0
    out = capsys.readouterr().out
    assert "16 cases, 48 attempts" in out
    assert "recursive-parquet-sorted.memory_mib-2048" in out


def test_resolve_plan_emits_machine_readable_cases(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert bench_cli.resolve_plan_main(["--bucket", "noaa-ghcn-pds", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["cases"]) == 16
    # The plan digest travels with the resolution so a submission can cite the
    # exact bytes it expanded.
    assert len(payload["plan_sha256"]) == 64


def test_resolve_plan_reports_a_bad_plan_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, ONE_CASE, extra="concurrency: 8\n")
    assert bench_cli.resolve_plan_main(["--path", str(path), "--skip-roster"]) == 1
    assert "unknown key" in capsys.readouterr().err


def test_every_registered_tool_is_discoverable() -> None:
    """The roster check is only as good as the set it compares against."""
    assert len(bench_cli.registered_tools()) == 11
