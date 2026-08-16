"""The bucket plan reader: cascade, case rows, case identity, refusals."""

from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path

import pytest

from benchmark import plan as bench
from benchmark import plan_cli as bench_cli

MINIMAL = """
spec_version: 2
bucket: {bucket}
region: us-east-1
defaults:
  reps: 3
  timeout_s: 3600
  auth: anonymous
  vcpus: 2
  memory_gb: 8
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
  cases:
    - {mode: s3api-v2-text}
"""

# Enough of a catalogue to resolve every shape these plans ask for. Passed in
# rather than read from benchmark/plans/, so a fixture plan needs no adjacent tree.
INSTANCES = {
    (2, 2): "n4-custom-2-2048",
    (2, 4): "n4-highcpu-2",
    (2, 8): "n4-standard-2",
    (2, 16): "n4-highmem-2",
    (4, 8): "n4-highcpu-4",
    (4, 16): "n4-standard-4",
}


# swath is the tool with a managed heap in these fixtures, as in the real table.
HEAP = bench.HeapConfig(
    percent=75,
    policies={
        "swath": bench.HeapPolicy(env="JAVA_TOOL_OPTIONS", value="-XX:MaxRAMPercentage={percent}")
    },
)


def load(path: Path, **kwargs: object) -> bench.Plan:
    """``Plan.load`` with the fixture tables already supplied."""
    kwargs.setdefault("instances", INSTANCES)
    kwargs.setdefault("heap", HEAP)
    return bench.Plan.load(path, **kwargs)  # type: ignore[arg-type]


# ── the shipped plan ─────────────────────────────────────────────────────────


def test_the_committed_plan_loads() -> None:
    """The plan in the tree is the one a campaign would submit; keep it valid."""
    loaded = bench.Plan.load(bench.default_path("noaa-ghcn-pds"))
    assert loaded.bucket == "noaa-ghcn-pds"
    assert loaded.region == "us-east-1"
    assert len(loaded.tools()) == 11
    # Ten bare tools, plus swath's four rows: 2 streaming modes at one ceiling
    # and the sorted mode at two.
    assert len(loaded.cases) == 14
    assert len(loaded.cases_for("swath")) == 4

    # The sweep is the container ceiling; the box does not move, so nothing but
    # the memory the process can feel differs across the sorted pair.
    sorted_cases = [c for c in loaded.cases_for("swath") if "sorted" in c.mode]
    assert {c.resources.machine_type for c in sorted_cases} == {"n4-highcpu-2"}
    assert sorted(c.resources.container_memory_gb or 0 for c in sorted_cases) == [2, 4]

    # 75% of what the container can see, not of the box it sits on.
    constrained = next(c for c in sorted_cases if c.resources.container_memory_gb == 2)
    assert constrained.resources.docker_options == ("--memory=2g", "--memory-swap=2g")
    assert constrained.env == (("JAVA_TOOL_OPTIONS", "-XX:MaxRAMPercentage=75"),)


def test_the_committed_plan_matches_the_registered_tools() -> None:
    """The roster rule only means anything if the shipped plan actually obeys it."""
    root = Path(__file__).resolve().parents[2]
    registered = {p.parents[1].name for p in root.glob("tools/*/build/image.json")}
    bench.check_roster(bench.Plan.load(bench.default_path("noaa-ghcn-pds")), registered)


def test_every_default_mode_is_one_its_adapter_implements() -> None:
    """The drift guard for benchmark/plans/tools.yaml, read from the adapters themselves.

    A mode renamed in an adapter would otherwise leave a default that only fails
    once a campaign is already submitting work.
    """
    root = Path(__file__).resolve().parents[2]
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


def test_the_committed_catalogue_offers_no_shared_core_machines() -> None:
    """E2 picks Intel or AMD for you, so you cannot know which chip you timed on."""
    catalogue = bench.load_instances(bench.bench_dir() / "instances.yaml")
    assert catalogue
    assert not [m for m in catalogue.values() if m.startswith("e2-")]


def test_a_memory_sweep_holds_vcpus_constant() -> None:
    """What makes memory the only variable: same vCPU count, three memory sizes.

    If the catalogue ever offered a shape whose family changed vCPU alongside
    memory, a memory axis would silently be measuring two things.
    """
    catalogue = bench.load_instances(bench.bench_dir() / "instances.yaml")
    by_vcpu: dict[int, set[int]] = {}
    for vcpus, memory_gb in catalogue:
        by_vcpu.setdefault(vcpus, set()).add(memory_gb)
    assert all(len(sizes) > 1 for sizes in by_vcpu.values())


def test_a_shape_listed_twice_is_refused(tmp_path: Path) -> None:
    """It would resolve to whichever came last, so two campaigns could differ."""
    path = tmp_path / "instances.yaml"
    path.write_text(
        "spec_version: 2\ninstances:\n"
        "  - {vcpus: 2, memory_gb: 4, machine_type: n4-highcpu-2}\n"
        "  - {vcpus: 2, memory_gb: 4, machine_type: c4-highcpu-2}\n",
        encoding="utf-8",
    )
    with pytest.raises(bench.PlanError, match="twice"):
        bench.load_instances(path)


def test_a_shape_the_catalogue_lacks_is_refused(tmp_path: Path) -> None:
    """Caught while resolving, not when Batch rejects the job."""
    path = write(tmp_path, ONE_CASE)
    with pytest.raises(bench.PlanError, match="does not offer"):
        load(path, instances={(64, 512): "n4-highmem-64"})


def test_a_draft_outside_the_tree_resolves_against_the_repo_tables(tmp_path: Path) -> None:
    """Reviewing a plan before moving it into place is the point of `--path`.

    The tables are looked for beside the plan's directory first; a draft written
    anywhere else would otherwise fail on a `tools.yaml` its author never wrote.
    """
    path = write(tmp_path, "s5cmd:\n", bucket="noaa-ghcn-pds")
    case = bench.Plan.load(path).cases[0]
    assert case.mode == "recursive"  # from the repository's benchmark/plans/tools.yaml
    assert case.resources.machine_type == "n4-standard-2"  # from its instances.yaml


# ── expansion and cascade ────────────────────────────────────────────────────


def test_an_empty_tool_runs_once_at_its_default_mode(tmp_path: Path) -> None:
    """Writing the name and stopping is the whole declaration."""
    path = write(tmp_path, "s5cmd:\ns3p:\n")
    loaded = load(path, default_modes={"s5cmd": "recursive", "s3p": "ls"})
    assert [(c.tool, c.case_id, c.mode) for c in loaded.cases] == [
        ("s5cmd", "recursive", "recursive"),
        ("s3p", "ls", "ls"),
    ]
    # An empty tool takes the plan's allocation, not one of its own.
    assert {c.resources.machine_type for c in loaded.cases} == {"n4-standard-2"}


def test_an_explicitly_empty_mapping_reads_the_same_as_a_bare_key(tmp_path: Path) -> None:
    path = write(tmp_path, "s5cmd: {}\n")
    loaded = load(path, default_modes={"s5cmd": "recursive"})
    assert [c.case_id for c in loaded.cases] == ["recursive"]


def test_an_empty_tool_with_no_default_mode_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, "s5cmd:\n")
    with pytest.raises(bench.PlanError, match="has no default"):
        load(path, default_modes={"s3p": "ls"})


def test_unindented_cases_do_not_silently_become_a_default_case(tmp_path: Path) -> None:
    """The cost of the empty-tool shorthand, and why it is affordable.

    Losing a level of indentation turns ``cases`` into a sibling tool rather
    than swath's body. That refuses, instead of quietly running swath once.
    """
    path = write(tmp_path, "swath:\ncases:\n  - {mode: recursive-tsv}\n")
    with pytest.raises(bench.PlanError, match=r"'tools\.cases' .* is not a mapping"):
        load(path, default_modes={"swath": "recursive-tsv"})


def test_each_row_is_one_case(tmp_path: Path) -> None:
    """The number of cases is the number of lines; nothing is multiplied."""
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - {mode: recursive-tsv, memory_gb: 4}
            - {mode: recursive-tsv, memory_gb: 8}
            - {mode: recursive-parquet, memory_gb: 8}
        """,
    )
    ids = [case.case_id for case in load(path).cases]
    assert ids == [
        "recursive-tsv.memory_gb-4",
        "recursive-tsv.memory_gb-8",
        "recursive-parquet.memory_gb-8",
    ]


def test_a_row_overrides_the_tool_which_overrides_the_defaults(tmp_path: Path) -> None:
    """Three layers, shallow and per-key: the nearest statement of a key wins."""
    path = write(
        tmp_path,
        """
        swath:
          memory_gb: 8
          vcpus: 4
          cases:
            - {mode: recursive-tsv, memory_gb: 16}
        """,
    )
    case = load(path).cases[0]
    assert case.resources.memory_gb == 16  # the row beats the tool
    assert case.resources.vcpus == 4  # the tool beats the defaults
    assert case.timeout_s == 3600  # defaults, unmentioned by either
    # Resolved from the pair, never stated by any layer.
    assert case.resources.machine_type == "n4-standard-4"


def test_a_tool_may_override_the_schedule(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        ps3:
          timeout_s: 7200
          cases:
            - {mode: list}
        """,
    )
    case = load(path).cases[0]
    assert (case.timeout_s, case.reps) == (7200, 3)


def test_rows_are_ragged_so_one_mode_can_be_swept_alone(tmp_path: Path) -> None:
    """The point of rows: only the mode that cares about memory is written twice."""
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - {mode: recursive-tsv, memory_gb: 4}
            - {mode: recursive-parquet-sorted, memory_gb: 8, vcpus: 4}
            - {mode: recursive-parquet-sorted, memory_gb: 16, vcpus: 4}
        """,
    )
    cases = load(path).cases
    assert [c.case_id for c in cases] == [
        "recursive-tsv.vcpus-2.memory_gb-4",
        "recursive-parquet-sorted.vcpus-4.memory_gb-8",
        "recursive-parquet-sorted.vcpus-4.memory_gb-16",
    ]
    # The first row never mentions vcpus, so it inherits the plan's 2 — and says
    # so in its ID anyway, because a sibling put vcpus in the union.
    assert cases[0].resources.machine_type == "n4-highcpu-2"
    assert [c.resources.vcpus for c in cases[1:]] == [4, 4]
    assert [c.resources.machine_type for c in cases[1:]] == ["n4-highcpu-4", "n4-standard-4"]


def test_the_id_renders_the_union_of_the_keys_the_rows_state(tmp_path: Path) -> None:
    """Ragged rows, one ID shape — otherwise a tool's IDs could not be compared.

    A row stating no ceiling still renders one, because a sibling did: its
    resolved answer is *no ceiling*, which is a real answer and not an absent key.
    """
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - {mode: recursive-tsv}
            - {mode: recursive-parquet-sorted, container_memory_gb: 2}
        """,
    )
    cases = load(path).cases
    assert [c.case_id for c in cases] == [
        "recursive-tsv.container_memory_gb-none",
        "recursive-parquet-sorted.container_memory_gb-2",
    ]
    assert cases[0].resources.container_memory_gb is None


def test_two_rows_that_resolve_to_one_case_are_refused(tmp_path: Path) -> None:
    """The second would append into the first's evidence.

    Not written identically: the second states a value it would have inherited
    anyway, which is how this is actually mistyped.
    """
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - {mode: recursive-tsv, memory_gb: 8}
            - {mode: recursive-tsv, memory_gb: 8, vcpus: 2}
        """,
    )
    with pytest.raises(bench.PlanError, match="twice"):
        load(path)


def test_a_row_may_omit_the_mode_and_inherit_the_tool_default(tmp_path: Path) -> None:
    """What keeps a sweep over allocation alone at one line per case."""
    path = write(
        tmp_path,
        """
        s5cmd:
          cases:
            - {vcpus: 2}
            - {vcpus: 4}
        """,
    )
    cases = load(path, default_modes={"s5cmd": "recursive"}).cases
    assert [(c.mode, c.case_id) for c in cases] == [
        ("recursive", "recursive.vcpus-2"),
        ("recursive", "recursive.vcpus-4"),
    ]


def test_the_campaign_product_preserves_retained_cases_and_adds_only_8gb_ceiling(
    tmp_path: Path,
) -> None:
    """The revised sweep drops low ceilings from the larger VM deliberately."""
    old_plan = write(
        tmp_path,
        """
        swath:
          memory_gb: 4
          container_memory_gb: 2
          cases:
            - {mode: recursive-tsv}
            - {mode: recursive-parquet}
            - {mode: recursive-parquet, container_memory_gb: 4}
            - {mode: recursive-parquet-sorted}
            - {mode: recursive-parquet-sorted, container_memory_gb: 4}
            - {mode: recursive-parquet, vcpus: 4, memory_gb: 8}
            - {mode: recursive-parquet, vcpus: 4, memory_gb: 8, container_memory_gb: 4}
            - {mode: recursive-parquet-sorted, vcpus: 4, memory_gb: 8}
            - {mode: recursive-parquet-sorted, vcpus: 4, memory_gb: 8, container_memory_gb: 4}
        """,
    )
    old_cases = {case.case_id: case for case in load(old_plan).cases}

    revised_plan = write(
        tmp_path,
        """
        swath:
          memory_gb: 4
          container_memory_gb: 2
          cases:
            - {mode: recursive-tsv}
            - product:
                mode: [recursive-parquet, recursive-parquet-sorted]
                zip:
                  - {vcpus: 2, memory_gb: 4, container_memory_gb: 2}
                  - {vcpus: 2, memory_gb: 4, container_memory_gb: 4}
                  - {vcpus: 4, memory_gb: 8, container_memory_gb: 8}
        """,
    )
    revised = load(revised_plan).cases
    revised_cases = {case.case_id: case for case in revised}

    assert bench.SPEC_VERSION == 2
    assert bench.FINGERPRINT_VERSION == 1
    assert [case.case_id for case in revised] == [
        "recursive-tsv.vcpus-2.memory_gb-4.container_memory_gb-2",
        "recursive-parquet.vcpus-2.memory_gb-4.container_memory_gb-2",
        "recursive-parquet-sorted.vcpus-2.memory_gb-4.container_memory_gb-2",
        "recursive-parquet.vcpus-2.memory_gb-4.container_memory_gb-4",
        "recursive-parquet-sorted.vcpus-2.memory_gb-4.container_memory_gb-4",
        "recursive-parquet.vcpus-4.memory_gb-8.container_memory_gb-8",
        "recursive-parquet-sorted.vcpus-4.memory_gb-8.container_memory_gb-8",
    ]
    retained = [
        "recursive-tsv.vcpus-2.memory_gb-4.container_memory_gb-2",
        "recursive-parquet.vcpus-2.memory_gb-4.container_memory_gb-2",
        "recursive-parquet-sorted.vcpus-2.memory_gb-4.container_memory_gb-2",
        "recursive-parquet.vcpus-2.memory_gb-4.container_memory_gb-4",
        "recursive-parquet-sorted.vcpus-2.memory_gb-4.container_memory_gb-4",
    ]
    assert {case_id: revised_cases[case_id].fingerprint for case_id in retained} == {
        case_id: old_cases[case_id].fingerprint for case_id in retained
    }
    assert not set(revised_cases) & {
        "recursive-parquet.vcpus-4.memory_gb-8.container_memory_gb-2",
        "recursive-parquet.vcpus-4.memory_gb-8.container_memory_gb-4",
        "recursive-parquet-sorted.vcpus-4.memory_gb-8.container_memory_gb-2",
        "recursive-parquet-sorted.vcpus-4.memory_gb-8.container_memory_gb-4",
    }
    assert {
        (
            case.mode,
            case.resources.vcpus,
            case.resources.memory_gb,
            case.resources.container_memory_gb,
        )
        for case in revised
        if case.resources.vcpus == 4
    } == {
        ("recursive-parquet", 4, 8, 8),
        ("recursive-parquet-sorted", 4, 8, 8),
    }


def test_two_independent_product_axes_multiply(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - product:
                mode: [recursive-tsv, recursive-jsonl]
                container_memory_gb: [2, 4]
        """,
    )
    assert [(case.mode, case.resources.container_memory_gb) for case in load(path).cases] == [
        ("recursive-tsv", 2),
        ("recursive-tsv", 4),
        ("recursive-jsonl", 2),
        ("recursive-jsonl", 4),
    ]


def test_zip_keeps_only_the_resource_shapes_that_were_authored(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - product:
                mode: [recursive-tsv]
                zip:
                  - {vcpus: 2, memory_gb: 2}
                  - {vcpus: 2, memory_gb: 4}
                  - {vcpus: 4, memory_gb: 8}
        """,
    )
    shapes = [(case.resources.vcpus, case.resources.memory_gb) for case in load(path).cases]
    assert shapes == [(2, 2), (2, 4), (4, 8)]
    assert (4, 4) not in shapes


def test_zip_may_be_the_products_only_factor(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - product:
                zip:
                  - {mode: recursive-tsv, memory_gb: 4}
                  - {mode: recursive-jsonl, memory_gb: 8}
        """,
    )
    assert [(case.mode, case.resources.memory_gb) for case in load(path).cases] == [
        ("recursive-tsv", 4),
        ("recursive-jsonl", 8),
    ]


def test_product_order_does_not_depend_on_yaml_mapping_order(tmp_path: Path) -> None:
    first = write(
        tmp_path,
        """
        swath:
          cases:
            - product:
                mode: [recursive-tsv, recursive-jsonl]
                zip:
                  - {vcpus: 2, memory_gb: 4}
                  - {vcpus: 4, memory_gb: 8}
                container_memory_gb: [2, 4]
        """,
    )
    expected = [(case.case_id, case.fingerprint) for case in load(first).cases]
    second = write(
        tmp_path,
        """
        swath:
          cases:
            - product:
                container_memory_gb: [2, 4]
                zip:
                  - {memory_gb: 4, vcpus: 2}
                  - {memory_gb: 8, vcpus: 4}
                mode: [recursive-tsv, recursive-jsonl]
        """,
    )
    assert [(case.case_id, case.fingerprint) for case in load(second).cases] == expected


def test_a_product_may_inherit_the_default_mode(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        s5cmd:
          cases:
            - product:
                memory_gb: [4, 8]
        """,
    )
    cases = load(path, default_modes={"s5cmd": "recursive"}).cases
    assert [(case.mode, case.resources.memory_gb) for case in cases] == [
        ("recursive", 4),
        ("recursive", 8),
    ]


def test_a_product_omitting_mode_loads_the_repository_default(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        s5cmd:
          cases:
            - product:
                memory_gb: [4, 8]
        """,
    )
    assert [case.mode for case in bench.Plan.load(path).cases] == ["recursive", "recursive"]


def test_expanded_rows_override_the_tool_which_overrides_defaults(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        swath:
          vcpus: 4
          memory_gb: 8
          cases:
            - product:
                memory_gb: [8, 16]
        """,
    )
    cases = load(path, default_modes={"swath": "recursive-tsv"}).cases
    assert [(case.resources.vcpus, case.resources.memory_gb) for case in cases] == [
        (4, 8),
        (4, 16),
    ]
    assert {case.timeout_s for case in cases} == {3600}


@pytest.mark.parametrize(
    ("case_entry", "message"),
    [
        ("- {product: {}}", "product.*empty"),
        ("- {product: {mode: []}}", "product.mode.*empty"),
        ("- {product: {mode: recursive-tsv}}", "product.mode.*not a list"),
        ("- {product: {zip: []}}", "product.zip.*empty"),
        ("- {product: {zip: nope}}", "product.zip.*not a list"),
        ("- {product: {zip: [nope]}}", "product.zip.*not a mapping"),
        ("- {product: nope}", "product.*not a mapping"),
        ("- {product: {unknown: [1]}}", "unknown key"),
        ("- {product: {timeout_s: [60]}}", "scheduling, not what a case is"),
        (
            "- product: {mode: [recursive-tsv]}\n  timeout_s: 60",
            "scheduling, not what a case is",
        ),
        (
            "- product:\n    mode: [recursive-tsv]\n    zip:\n"
            "      - {vcpus: 2, memory_gb: 4, timeout_s: 60}",
            "scheduling, not what a case is",
        ),
        (
            "- product:\n    vcpus: [2]\n    zip:\n      - {vcpus: 2, memory_gb: 4}",
            "both as an independent axis and inside zip",
        ),
        (
            "- product:\n    zip:\n      - {vcpus: 2, memory_gb: 4}\n"
            "      - {vcpus: 4, container_memory_gb: 2}",
            "fields that differ",
        ),
        ("- product:\n    zip:\n      - {vcpus: 2}", "at least two row fields"),
        (
            "- product:\n    zip:\n      - {vcpus: 2, memory_gb: 4}\n"
            "      - {memory_gb: 4, vcpus: 2}",
            "same choice twice",
        ),
        (
            "- product:\n    zip:\n      - {vcpus: 2, mystery: 4}",
            "unknown key",
        ),
        ("- {product: {mode: [[recursive-tsv]]}}", "mode.*not a non-empty string"),
        ("- {product: {auth: [signed]}}", r"is not anonymous\|authenticated"),
        ("- {product: {memory_gb: [false]}}", "not a positive integer"),
        ("- {product: {mode: [recursive-tsv]}, mystery: 1}", "extra key"),
    ],
)
def test_invalid_product_structures_are_refused(
    tmp_path: Path, case_entry: str, message: str
) -> None:
    path = write(
        tmp_path,
        "swath:\n  cases:\n" + textwrap.indent(case_entry, "    ") + "\n",
    )
    with pytest.raises(bench.PlanError, match=message):
        load(path, default_modes={"swath": "recursive-tsv"})


def test_a_duplicate_resolved_case_across_literal_and_product_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - {mode: recursive-tsv, memory_gb: 8}
            - product:
                mode: [recursive-tsv]
                memory_gb: [8]
        """,
    )
    with pytest.raises(bench.PlanError, match="twice"):
        load(path)


def test_duplicate_values_on_an_independent_axis_are_refused_as_one_case(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - product:
                mode: [recursive-tsv, recursive-tsv]
        """,
    )
    with pytest.raises(bench.PlanError, match="twice"):
        load(path)


def test_literal_rows_remain_scalar_and_compatible(tmp_path: Path) -> None:
    literal = load(write(tmp_path, ONE_CASE)).cases[0]
    assert literal.mode == "s3api-v2-text"
    assert bench.SPEC_VERSION == 2

    listed = write(tmp_path, "aws-cli:\n  cases:\n    - {mode: [s3api-v2-text]}\n")
    with pytest.raises(bench.PlanError, match="not a non-empty string"):
        load(listed)


@pytest.mark.parametrize("bucket", ["noaa-rtma-pds", "sorel-20m"])
def test_large_bucket_campaign_plans_resolve_seventeen_cases(bucket: str) -> None:
    loaded = bench.Plan.load(bench.default_path(bucket))
    assert len(loaded.cases) == 17
    assert len(loaded.cases_for("swath")) == 7


# ── the container ceiling and the heap ───────────────────────────────────────


def test_no_ceiling_means_no_docker_flags(tmp_path: Path) -> None:
    """Absent is a real answer: the container sees the whole box."""
    case = load(write(tmp_path, ONE_CASE)).cases[0]
    assert case.resources.container_memory_gb is None
    assert case.resources.docker_options == ()
    assert case.resources.visible_memory_gb == 8  # the box


def test_a_ceiling_is_enforced_as_a_cgroup_limit(tmp_path: Path) -> None:
    """Batch's memoryMib constrains nothing; `docker run --memory` does.

    Swap is pinned to the same value on purpose — left alone Docker allows twice
    the limit, so "it fitted in 2 GB" could mean 2 GB of RAM plus 2 GB of disk.
    """
    path = write(tmp_path, "swath:\n  container_memory_gb: 2\n")
    case = load(path, default_modes={"swath": "recursive-tsv"}).cases[0]
    assert case.resources.docker_options == ("--memory=2g", "--memory-swap=2g")
    assert case.resources.visible_memory_gb == 2


def test_a_ceiling_above_the_box_is_refused(tmp_path: Path) -> None:
    """It would constrain nothing, so it is a plan that does not mean what it says."""
    path = write(tmp_path, "swath:\n  container_memory_gb: 64\n")
    with pytest.raises(bench.PlanError, match="constrains nothing"):
        load(path, default_modes={"swath": "recursive-tsv"})


def test_a_percentage_template_needs_no_ceiling_arithmetic(tmp_path: Path) -> None:
    """The JVM reads its own cgroup limit, so the share passes through as written.

    This deliberately does not claim to prove the heap follows the ceiling: a
    percent-only template renders the same string either way. That claim is
    tested against a `{mib}` template below, where the arithmetic is visible.
    """
    path = write(tmp_path, "swath:\n  container_memory_gb: 2\n")
    case = load(path, default_modes={"swath": "recursive-tsv"}).cases[0]
    assert case.env == (("JAVA_TOOL_OPTIONS", "-XX:MaxRAMPercentage=75"),)


def test_a_size_template_resolves_against_the_ceiling_not_the_box(tmp_path: Path) -> None:
    """The claim a percent-only template cannot make: 75% of 4 GB, not of the 8 GB box."""
    v8 = bench.HeapConfig(
        percent=75,
        policies={"s3p": bench.HeapPolicy(env="NODE_OPTIONS", value="--max-old-space-size={mib}")},
    )
    capped = load(
        write(tmp_path, "s3p:\n  container_memory_gb: 4\n"),
        default_modes={"s3p": "ls"},
        heap=v8,
    ).cases[0]
    uncapped = load(
        write(tmp_path, "s3p:\n", bucket="c"), default_modes={"s3p": "ls"}, heap=v8
    ).cases[0]
    assert capped.env == (("NODE_OPTIONS", "--max-old-space-size=3072"),)  # 75% of 4 GB
    assert uncapped.env == (("NODE_OPTIONS", "--max-old-space-size=6144"),)  # 75% of the box


def test_a_runtime_wanting_a_size_gets_one_computed(tmp_path: Path) -> None:
    """V8 cannot read its own ceiling, so `{mib}` is resolved against it."""
    path = write(tmp_path, "s3p:\n  container_memory_gb: 4\n")
    case = load(
        path,
        default_modes={"s3p": "ls"},
        heap=bench.HeapConfig(
            percent=75,
            policies={
                "s3p": bench.HeapPolicy(env="NODE_OPTIONS", value="--max-old-space-size={mib}")
            },
        ),
    ).cases[0]
    assert case.env == (("NODE_OPTIONS", "--max-old-space-size=3072"),)  # 75% of 4 GB


def test_a_tool_without_a_managed_heap_is_told_nothing(tmp_path: Path) -> None:
    """A Go tool has no ceiling to set, so setting one would be noise."""
    assert (
        load(write(tmp_path, "s5cmd:\n"), default_modes={"s5cmd": "recursive"}).cases[0].env == ()
    )


def test_an_impossible_heap_percentage_is_refused(tmp_path: Path) -> None:
    """A share over 100 is a heap larger than the memory it must fit in."""
    path = tmp_path / "tools.yaml"
    path.write_text(
        "spec_version: 2\ndefault_modes: {swath: recursive-tsv}\n"
        "heap:\n  percent: 150\n  tools:\n    swath:\n"
        "      env: JAVA_TOOL_OPTIONS\n      value: '-XX:MaxRAMPercentage={percent}'\n",
        encoding="utf-8",
    )
    with pytest.raises(bench.PlanError, match="over 100"):
        bench.load_heap_config(path)


def test_a_plan_may_not_set_a_heap_share(tmp_path: Path) -> None:
    """It configures two tools out of eleven, so it is not a plan's business."""
    path = write(tmp_path, ONE_CASE)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "  memory_gb: 8", "  memory_gb: 8\n  heap_percent: 50"
        ),
        encoding="utf-8",
    )
    with pytest.raises(bench.PlanError, match="unknown key"):
        load(path)


def test_an_unknown_heap_placeholder_is_refused(tmp_path: Path) -> None:
    """A typo would otherwise reach the runtime as a literal brace."""
    path = tmp_path / "tools.yaml"
    path.write_text(
        "spec_version: 2\ndefault_modes: {swath: recursive-tsv}\n"
        "heap:\n  percent: 75\n  tools:\n    swath:\n"
        "      env: JAVA_TOOL_OPTIONS\n      value: '-Xmx{gigabytes}'\n",
        encoding="utf-8",
    )
    with pytest.raises(bench.PlanError, match="unknown placeholder"):
        bench.load_heap_config(path)


def test_the_committed_heap_table_covers_the_managed_runtimes() -> None:
    """swath is Java and s3p is JavaScript; the rest have no heap to size."""
    heap = bench.load_heap_config(bench.bench_dir() / "tools.yaml")
    assert set(heap.policies) == {"swath", "s3p"}
    # The share lives with the policies, not in any plan: nine tools have no
    # heap to size, so a per-bucket setting would be restated and ignored.
    assert heap.percent == 75


def test_a_tools_file_from_a_future_reader_is_refused(tmp_path: Path) -> None:
    """The heap table is read on every plan; the mode table only sometimes.

    Validating in each caller left the common path unguarded, so a tools.yaml
    written for a later reader was accepted whenever the plan happened to name
    every mode itself.
    """
    path = tmp_path / "tools.yaml"
    body = "default_modes: {swath: recursive-tsv}\nheap:\n  percent: 75\n  tools: {}\n"
    path.write_text(f"spec_version: 99\n{body}", encoding="utf-8")
    with pytest.raises(bench.PlanError, match="spec_version"):
        bench.load_heap_config(path)

    path.write_text(f"spec_version: 2\nstray_key: true\n{body}", encoding="utf-8")
    with pytest.raises(bench.PlanError, match="unknown key"):
        bench.load_heap_config(path)


# ── identity ─────────────────────────────────────────────────────────────────


def test_every_field_of_a_case_moves_its_fingerprint(tmp_path: Path) -> None:
    """Identity must cover the whole resolved case, not just the parts with tests.

    The bucket in particular is load-bearing — other tests use separate
    directories on the strength of it — but nothing asserted it.
    """
    base = "swath:\n  cases:\n    - {mode: recursive-tsv}\n"

    def fingerprint_of(body: str, *, bucket: str = "b", region: str = "us-east-1") -> str:
        directory = tmp_path / f"{bucket}-{region}-{abs(hash(body))}"
        directory.mkdir()
        path = directory / f"{bucket}.yaml"
        text = MINIMAL.format(bucket=bucket, tools=textwrap.indent(body, "  "))
        path.write_text(text.replace("region: us-east-1", f"region: {region}"), encoding="utf-8")
        return load(path, default_modes={"swath": "recursive-tsv"}).cases[0].fingerprint

    reference = fingerprint_of(base)
    assert fingerprint_of(base, bucket="other") != reference
    assert fingerprint_of(base, region="eu-west-1") != reference
    # mode, via a different mode of the same tool.
    assert fingerprint_of("swath:\n  cases:\n    - {mode: recursive-jsonl}\n") != reference
    # env, via the ceiling that the heap share is rendered against.
    assert fingerprint_of(f"{base}  container_memory_gb: 2\n") != reference


def test_two_tools_running_the_same_mode_differ(tmp_path: Path) -> None:
    """The tool is part of identity, not merely part of the receipt path."""
    path = write(
        tmp_path, "s5cmd:\n  cases:\n    - {mode: list}\ns3kor:\n  cases:\n    - {mode: list}\n"
    )
    first, second = load(path).cases
    assert first.case_id == second.case_id  # same derived path segment
    assert first.fingerprint != second.fingerprint


def test_a_key_only_one_row_states_still_appears_in_the_id(tmp_path: Path) -> None:
    """Otherwise the ID would mean "whatever the default was", unrecoverably."""
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - {mode: recursive-tsv, memory_gb: 4}
        """,
    )
    assert load(path).cases[0].case_id == "recursive-tsv.memory_gb-4"


def test_resource_changes_move_the_fingerprint(tmp_path: Path) -> None:
    """The guard that stops an edited case appending into its own old evidence."""
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - {mode: recursive-tsv, memory_gb: 4}
            - {mode: recursive-tsv, memory_gb: 8}
        """,
    )
    first, second = load(path).cases
    assert first.fingerprint != second.fingerprint


def test_every_key_a_row_may_state_moves_both_the_id_and_the_fingerprint(
    tmp_path: Path,
) -> None:
    """The law that makes the row vocabulary checkable rather than remembered.

    A key visible to one but not the other is the ``timeout_s`` hazard: same ID,
    different fingerprints, so two non-comparable runs land in one directory.
    Adding a key to ``ROW_FIELDS`` without rendering it into the ID fails here
    rather than in a campaign.
    """
    # Two legal values per key, both resolving to a shape the catalogue offers.
    pairs: dict[str, tuple[object, object]] = {
        "mode": ("recursive-tsv", "recursive-jsonl"),
        "auth": ("anonymous", "authenticated"),
        "vcpus": (2, 4),
        "memory_gb": (8, 16),
        "container_memory_gb": (4, 8),
    }
    assert set(pairs) == set(bench.ROW_FIELDS), "a row key with no coverage here"

    def case(field: str, value: object, index: int) -> bench.Case:
        directory = tmp_path / f"{field}-{index}"
        directory.mkdir()
        path = directory / "b.yaml"
        row = {"mode": "recursive-tsv"} | {field: value}
        body = "swath:\n  cases:\n    - {" + ", ".join(f"{k}: {v}" for k, v in row.items()) + "}\n"
        path.write_text(
            MINIMAL.format(bucket="b", tools=textwrap.indent(body, "  ")), encoding="utf-8"
        )
        return load(path).cases[0]

    for field, (before, after) in pairs.items():
        first, second = case(field, before, 0), case(field, after, 1)
        assert first.case_id != second.case_id, f"{field} does not reach the id"
        assert first.fingerprint != second.fingerprint, f"{field} does not reach the fingerprint"


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
        fingerprints.append(load(path).cases[0].fingerprint)
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
        fingerprints.append(load(path).cases[0].fingerprint)
    assert fingerprints[0] != fingerprints[1]


# ── refusals ─────────────────────────────────────────────────────────────────


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    """An unknown key is a misspelling of a real one, and would silently do nothing."""
    path = write(tmp_path, ONE_CASE, extra="concurrency: 8\n")
    with pytest.raises(bench.PlanError, match="unknown key"):
        load(path)


def test_an_unsupported_spec_version_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, ONE_CASE)
    path.write_text(
        path.read_text(encoding="utf-8").replace("spec_version: 2", "spec_version: 3"),
        encoding="utf-8",
    )
    with pytest.raises(bench.PlanError, match="spec_version"):
        load(path)


def test_a_filename_that_disagrees_with_the_bucket_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, ONE_CASE, bucket="b")
    renamed = path.with_name("other.yaml")
    path.rename(renamed)
    with pytest.raises(bench.PlanError, match="is named"):
        load(renamed)


def test_a_row_without_a_mode_and_no_tool_default_is_refused(tmp_path: Path) -> None:
    """Omitting the mode means "the usual one", which has to exist somewhere."""
    path = write(tmp_path, "swath:\n  cases:\n    - {memory_gb: 4}\n")
    with pytest.raises(bench.PlanError, match="states no mode"):
        load(path, default_modes={"s3p": "ls"})


def test_a_plan_that_does_not_say_whether_it_signed_is_refused(tmp_path: Path) -> None:
    """Four of the eleven tools have no unsigned path, so this is never implicit."""
    path = write(tmp_path, ONE_CASE)
    path.write_text(
        path.read_text(encoding="utf-8").replace("  auth: anonymous\n", ""), encoding="utf-8"
    )
    with pytest.raises(bench.PlanError, match="has no 'auth'"):
        load(path)


def test_a_stratum_that_is_not_one_of_the_two_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, "swath:\n  auth: signed\n  cases:\n    - {mode: recursive-tsv}\n")
    with pytest.raises(bench.PlanError, match=r"is not anonymous\|authenticated"):
        load(path)


def test_a_row_may_sweep_the_stratum_and_it_renders(tmp_path: Path) -> None:
    """Running one tool both ways measures signing; the ID has to say which is which."""
    path = write(
        tmp_path,
        """
        aws-cli:
          cases:
            - {mode: s3api-v2-text, auth: anonymous}
            - {mode: s3api-v2-text, auth: authenticated}
        """,
    )
    first, second = load(path).cases
    assert [c.case_id for c in (first, second)] == [
        "s3api-v2-text.auth-anonymous",
        "s3api-v2-text.auth-authenticated",
    ]
    assert first.fingerprint != second.fingerprint


def test_a_row_stating_the_schedule_is_refused(tmp_path: Path) -> None:
    """``timeout_s`` is in the fingerprint but not the ID, so two rows differing
    only there would render one ID and two fingerprints."""
    path = write(tmp_path, "swath:\n  cases:\n    - {mode: recursive-tsv, timeout_s: 60}\n")
    with pytest.raises(bench.PlanError, match="scheduling, not what a case is"):
        load(path)


def test_a_layer_stating_a_mode_is_refused(tmp_path: Path) -> None:
    """Eleven tools have eleven mode vocabularies, so nothing above a row has one."""
    tool_level = write(tmp_path, "swath:\n  mode: recursive-tsv\n  cases:\n    - {memory_gb: 4}\n")
    with pytest.raises(bench.PlanError, match="belongs to a case row"):
        load(tool_level, default_modes={"swath": "recursive-tsv"})

    plan_level = write(tmp_path, ONE_CASE, bucket="c")
    plan_level.write_text(
        plan_level.read_text(encoding="utf-8").replace("  reps: 3", "  reps: 3\n  mode: list"),
        encoding="utf-8",
    )
    with pytest.raises(bench.PlanError, match="belongs to a case row"):
        load(plan_level)


def test_defaults_given_as_a_list_is_refused(tmp_path: Path) -> None:
    """The plan-level sweep this schema does not have.

    A one-entry list and a fallback row mean the same thing; a two-entry list
    could mean either a cascade or a cross-product over every tool at once.
    """
    path = tmp_path / "b.yaml"
    path.write_text(
        "spec_version: 2\n"
        "bucket: b\n"
        "region: us-east-1\n"
        "defaults:\n"
        "  - {reps: 3, timeout_s: 3600, vcpus: 2, memory_gb: 8}\n"
        "  - {reps: 3, timeout_s: 3600, vcpus: 4, memory_gb: 8}\n"
        "tools:\n"
        "  aws-cli:\n"
        "    cases:\n"
        "      - {mode: s3api-v2-text}\n",
        encoding="utf-8",
    )
    with pytest.raises(bench.PlanError, match="not a sweep"):
        load(path)


def test_a_tool_body_is_the_defaults_vocabulary_plus_its_rows() -> None:
    """``defaults`` and a tool body are the same shape; the tool body also carries
    the rows. Asserted so the sentence cannot rot away from the tuples."""
    assert ("cases", *bench.LAYER_FIELDS) == bench.TOOL_FIELDS
    assert not set(bench.ROW_FIELDS) & set(bench.SCHEDULE_FIELDS)
    assert "mode" not in bench.LAYER_FIELDS
    # The overlap is what a case is *and* can sensibly be defaulted: the
    # allocation, and which stratum it ran in.
    assert set(bench.ROW_FIELDS) & set(bench.LAYER_FIELDS) == {*bench.RESOURCE_FIELDS, "auth"}


def test_incomplete_defaults_are_refused(tmp_path: Path) -> None:
    path = write(tmp_path, ONE_CASE)
    path.write_text(
        path.read_text(encoding="utf-8").replace("  memory_gb: 8\n", ""), encoding="utf-8"
    )
    with pytest.raises(bench.PlanError, match="missing memory_gb"):
        load(path)


def test_a_yaml_bool_is_not_a_memory_size(tmp_path: Path) -> None:
    """YAML 1.1 reads a bare ``yes`` as True, and ``isinstance(True, int)`` holds."""
    path = write(
        tmp_path,
        """
        swath:
          cases:
            - {mode: recursive-tsv, memory_gb: yes}
        """,
    )
    with pytest.raises(bench.PlanError, match="positive integer"):
        load(path)


def test_running_and_excluding_the_same_tool_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        ONE_CASE,
        extra="exclude:\n  - tool: aws-cli\n    reason: contradicts itself\n",
    )
    with pytest.raises(bench.PlanError, match="both runs and excludes"):
        load(path)


def test_an_exclusion_without_a_reason_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, ONE_CASE, extra="exclude:\n  - tool: s3p\n")
    with pytest.raises(bench.PlanError, match="reason"):
        load(path)


# ── cross-checks ─────────────────────────────────────────────────────────────


def test_a_registered_tool_the_plan_ignores_is_refused(tmp_path: Path) -> None:
    """Registering a tool and forgetting a campaign is the mistake this catches."""
    loaded = load(write(tmp_path, ONE_CASE))
    with pytest.raises(bench.PlanError, match="does not mention s5cmd"):
        bench.check_roster(loaded, {"aws-cli", "s5cmd"})


def test_an_excluded_tool_satisfies_the_roster(tmp_path: Path) -> None:
    loaded = load(
        write(tmp_path, ONE_CASE, extra="exclude:\n  - tool: s5cmd\n    reason: not yet built\n")
    )
    bench.check_roster(loaded, {"aws-cli", "s5cmd"})


def test_an_unregistered_tool_is_refused(tmp_path: Path) -> None:
    loaded = load(write(tmp_path, ONE_CASE))
    with pytest.raises(bench.PlanError, match="unregistered"):
        bench.check_roster(loaded, set())


def test_a_mode_the_adapter_lacks_is_refused(tmp_path: Path) -> None:
    """Caught before submission rather than at Batch runtime."""
    loaded = load(write(tmp_path, ONE_CASE))
    with pytest.raises(bench.PlanError, match="no mode 's3api-v2-text'"):
        bench.check_modes(loaded, {"aws-cli": {"s3-ls-recursive"}})


# ── the resolve-plan dry run ─────────────────────────────────────────────────


def test_resolve_plan_advertises_its_supported_module_invocation() -> None:
    assert bench_cli.build_parser().prog == "python -m benchmark.plan_cli"


def test_resolve_plan_expands_the_committed_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert bench_cli.resolve_plan_main(["--bucket", "noaa-ghcn-pds"]) == 0
    out = capsys.readouterr().out
    assert "14 cases, 14 attempts" in out
    assert "recursive-parquet-sorted.container_memory_gb-2" in out


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
