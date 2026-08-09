"""The bucket plan reader: cascade, matrix expansion, case identity, refusals."""

from __future__ import annotations

import ast
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
    # Ten sampled tools, plus swath's two blocks: 2 streaming modes at one size
    # and the sorted mode at two.
    assert len(loaded.cases) == 14
    assert len(loaded.cases_for("swath")) == 4
    # The sorted block's own box, not the plan default.
    sorted_cases = [c for c in loaded.cases_for("swath") if "sorted" in c.mode]
    assert {c.resources.machine_type for c in sorted_cases} == {"n4-highcpu-4"}


def test_the_committed_plan_matches_the_registered_tools() -> None:
    """The roster rule only means anything if the shipped plan actually obeys it."""
    root = Path(__file__).resolve().parents[1]
    registered = {p.parents[1].name for p in root.glob("tools/*/build/image.json")}
    bench.check_roster(bench.Plan.load(bench.default_path("noaa-ghcn-pds")), registered)


def test_every_default_mode_is_one_its_adapter_implements() -> None:
    """The drift guard for bench/tools.yaml, read from the adapters themselves.

    A mode renamed in an adapter would otherwise leave a default that only fails
    once a campaign is already submitting work.
    """
    root = Path(__file__).resolve().parents[1]
    defaults = bench.load_default_modes(bench.bench_dir() / "tools.yaml")
    for tool, mode in defaults.items():
        source = (root / "tools" / tool / "adapter" / "command.py").read_text(encoding="utf-8")
        node = next(
            n
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "MODES" for t in n.targets)
        )
        value = node.value
        implemented = ast.literal_eval(value.args[0] if isinstance(value, ast.Call) else value)
        assert mode in implemented, f"{tool}: {mode!r} not in {sorted(implemented)}"


def test_the_committed_plan_declares_every_registered_tool() -> None:
    loaded = bench.Plan.load(bench.default_path("noaa-ghcn-pds"))
    assert loaded.declared() == bench_cli.registered_tools()


# ── expansion and cascade ────────────────────────────────────────────────────


def test_an_empty_tool_runs_once_at_its_default_mode(tmp_path: Path) -> None:
    """Writing the name and stopping is the whole declaration."""
    path = write(tmp_path, "s5cmd:\ns3p:\n")
    loaded = bench.Plan.load(path, default_modes={"s5cmd": "recursive", "s3p": "ls"})
    assert [(c.tool, c.case_id, c.mode) for c in loaded.cases] == [
        ("s5cmd", "recursive", "recursive"),
        ("s3p", "ls", "ls"),
    ]
    # An empty tool takes the plan's allocation, not one of its own.
    assert {c.resources.machine_type for c in loaded.cases} == {"e2-standard-4"}


def test_an_explicitly_empty_mapping_reads_the_same_as_a_bare_key(tmp_path: Path) -> None:
    path = write(tmp_path, "s5cmd: {}\n")
    loaded = bench.Plan.load(path, default_modes={"s5cmd": "recursive"})
    assert [c.case_id for c in loaded.cases] == ["recursive"]


def test_an_empty_tool_with_no_default_mode_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, "s5cmd:\n")
    with pytest.raises(bench.PlanError, match="no default mode"):
        bench.Plan.load(path, default_modes={"s3p": "ls"})


def test_an_unindented_matrix_does_not_silently_become_a_default_case(tmp_path: Path) -> None:
    """The cost of the empty-tool shorthand, and why it is affordable.

    Losing a level of indentation turns ``matrix`` into a sibling tool rather
    than swath's body. That refuses, instead of quietly running swath once.
    """
    path = write(tmp_path, "swath:\nmatrix:\n  - mode: [recursive-tsv]\n")
    with pytest.raises(bench.PlanError, match=r"'tools\.matrix' .* is not a mapping"):
        bench.Plan.load(path, default_modes={"swath": "recursive-tsv"})


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


def test_blocks_let_modes_take_different_sweeps(tmp_path: Path) -> None:
    """The point of blocks: only the mode that cares about memory is swept."""
    path = write(
        tmp_path,
        """
        swath:
          matrix:
            - mode: [recursive-tsv]
              memory_mib: [2048]
            - mode: [recursive-parquet-sorted]
              memory_mib: [2048, 4096]
              resources:
                machine_type: n4-highcpu-4
        """,
    )
    cases = bench.Plan.load(path).cases
    assert [c.case_id for c in cases] == [
        "recursive-tsv.memory_mib-2048",
        "recursive-parquet-sorted.memory_mib-2048",
        "recursive-parquet-sorted.memory_mib-4096",
    ]
    # A block's resources override the tool and the defaults, and its axes still
    # override the block.
    assert cases[0].resources.machine_type == "e2-standard-4"
    assert [c.resources.machine_type for c in cases[1:]] == ["n4-highcpu-4"] * 2
    assert [c.resources.memory_mib for c in cases[1:]] == [2048, 4096]


def test_blocks_that_declare_different_axes_are_refused(tmp_path: Path) -> None:
    """Mixed axis sets would give one tool IDs of two different shapes."""
    path = write(
        tmp_path,
        """
        swath:
          matrix:
            - mode: [recursive-tsv]
            - mode: [recursive-parquet]
              memory_mib: [4096]
        """,
    )
    with pytest.raises(bench.PlanError, match="mixes axis sets"):
        bench.Plan.load(path)


def test_blocks_generating_the_same_case_twice_are_refused(tmp_path: Path) -> None:
    """Two blocks can overlap; the second would append into the first's evidence."""
    path = write(
        tmp_path,
        """
        swath:
          matrix:
            - mode: [recursive-tsv]
              memory_mib: [2048]
            - mode: [recursive-tsv]
              memory_mib: [2048]
              resources:
                machine_type: n4-highcpu-4
        """,
    )
    with pytest.raises(bench.PlanError, match="twice"):
        bench.Plan.load(path)


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
    assert "14 cases, 14 attempts" in out
    assert "recursive-parquet-sorted.memory_mib-2048" in out


def test_resolve_plan_emits_machine_readable_cases(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert bench_cli.resolve_plan_main(["--bucket", "noaa-ghcn-pds", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["cases"]) == 14
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
