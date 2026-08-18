"""What the loader refuses when a capsule declares its manifest.

Every case here is a declaration a capsule could plausibly write and the harness
must not accept: an axis name nobody reserved, a default with no provenance, a
plan setting a knob the tool fixes, a prerequisite chain that never terminates,
an executable the registered image does not have. Each one would otherwise
survive into a campaign as a recorded number that means something other than
what it says.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from benchmark.runtime.command_adapter import (
    HEAP_PERCENT,
    Ceiling,
    CommandAdapterError,
    CommandRequest,
    Default,
    Executable,
    Fixed,
    Inert,
    LoadedCommandAdapter,
    Mode,
    Requirement,
    load_command_adapter,
)

ARGV = ("/usr/local/bin/fixture",)
TEXT, KEY = "text", ("key",)
HEADER = '''\
"""A fixture capsule: the smallest thing the loader will accept."""

from benchmark.runtime.command_adapter import (  # noqa: F401
    HEAP_PERCENT,
    Ceiling,
    CommandRequest,
    Default,
    Executable,
    Fixed,
    Inert,
    Mode,
    Stated,
)

TOOL = "fixture"
EXECUTABLES = (Executable("fixture", ("/usr/local/bin/fixture",)),)
SUPPORTS_UNSIGNED = True
MODES = {"list": Mode(product="text", fields=("key", "size"))}


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return (*EXECUTABLES[0].argv, request.mode)
'''


def write_capsule(
    tmp_path: Path,
    body: str = "",
    *,
    registered: tuple[str, ...] | None = None,
) -> Path:
    """Write a fixture capsule and return its ``adapter/command.py``.

    ``registered`` writes a ``build/image.json``; without one there is no
    registered image to cross-check against, as for a staged capsule.
    """
    adapter = tmp_path / "fixture" / "adapter"
    adapter.mkdir(parents=True)
    path = adapter / "command.py"
    path.write_text(HEADER + body, encoding="utf-8")
    if registered is not None:
        build = tmp_path / "fixture" / "build"
        build.mkdir()
        (build / "image.json").write_text(
            json.dumps({"tool": "fixture", "executable": list(registered)}), encoding="utf-8"
        )
    return path


def load(
    tmp_path: Path,
    body: str = "",
    *,
    registered: tuple[str, ...] | None = None,
) -> LoadedCommandAdapter:
    return load_command_adapter(write_capsule(tmp_path, body, registered=registered))


def test_a_declared_axis_reaches_the_config_the_identity_hashes(tmp_path: Path) -> None:
    """A plan stating no concurrency must still record what the run listed at."""
    adapter = load(
        tmp_path,
        "\nMODES = {\n"
        '    "list": Mode(\n'
        '        product="text", fields=("key",), axes={"concurrency": Ceiling(8, "help")}\n'
        "    ),\n"
        '    "default": Mode(\n'
        '        product="text", fields=("key",), axes={"concurrency": Default(4, "help")}\n'
        "    ),\n"
        '    "fixed": Mode(product="text", fields=("key",), axes={"concurrency": Fixed(256)}),\n'
        '    "inert": Mode(product="text", fields=("key",), axes={"concurrency": Inert()}),\n'
        '    "absent": Mode(product="text", fields=("key",)),\n'
        "}\n",
    )
    assert adapter.effective_config("list", {}) == {"concurrency": 8, "mode": "list"}
    assert adapter.effective_config("default", {}) == {"concurrency": 4, "mode": "default"}
    assert adapter.effective_config("fixed", {}) == {"concurrency": 256, "mode": "fixed"}
    # Inert and absent are different facts and both leave the key out: the flag
    # did nothing here, and there is no such flag there.
    assert adapter.effective_config("inert", {}) == {"mode": "inert"}
    assert adapter.effective_config("absent", {}) == {"mode": "absent"}


def test_a_stated_axis_folds_the_plans_value_and_refuses_silence(tmp_path: Path) -> None:
    """`Stated` asserts the capsule has no number of its own: a plan value passes
    through into the hashed blob, and a silent plan is refused rather than
    handed an invented default — s3-fast-list's `segments`."""
    adapter = load(
        tmp_path,
        "\nMODES = {\n"
        '    "split": Mode(\n'
        '        product="text",\n'
        '        fields=("key",),\n'
        '        axes={"segments": Stated()},\n'
        '        purpose_ceiling="preparation",\n'
        "    ),\n"
        "}\n",
    )
    assert adapter.effective_config("split", {"segments": 16}) == {
        "mode": "split",
        "segments": 16,
    }
    with pytest.raises(CommandAdapterError, match="the plan must state it"):
        adapter.effective_config("split", {})


def test_a_plan_sets_a_settable_axis_and_never_a_fixed_one(tmp_path: Path) -> None:
    adapter = load(
        tmp_path,
        '\nCONFIG_KEYS = frozenset({"concurrency"})\n'
        "MODES = {\n"
        '    "list": Mode(\n'
        '        product="text", fields=("key",), axes={"concurrency": Default(4, "help")}\n'
        "    ),\n"
        '    "fixed": Mode(product="text", fields=("key",), axes={"concurrency": Fixed(256)}),\n'
        "}\n",
    )
    assert adapter.effective_config("list", {"concurrency": 16}) == {
        "concurrency": 16,
        "mode": "list",
    }
    with pytest.raises(CommandAdapterError):
        adapter.effective_config("fixed", {"concurrency": 16})


def test_the_merged_blob_round_trips_through_compile(tmp_path: Path) -> None:
    """The loader must not refuse its own output.

    No capsule lists the heap share in ``CONFIG_KEYS`` — no plan may ever set it
    — yet the merge writes it into the blob that identity hashes and that the
    engine hands back on the way to argv.
    """
    adapter = load(
        tmp_path,
        "\nMODES = {\n"
        '    "list": Mode(\n'
        '        product="text",\n'
        '        fields=("key",),\n'
        '        axes={"heap_percent": Fixed(HEAP_PERCENT), "concurrency": Fixed(256)},\n'
        "    )\n"
        "}\n",
    )
    assert not adapter.config_keys
    blob = adapter.effective_config("list", {})
    assert blob == {"concurrency": 256, "heap_percent": HEAP_PERCENT, "mode": "list"}
    request = CommandRequest("list", "bucket", "region", tool="fixture", config=blob)
    assert adapter.compile(request) == (*ARGV, "list")


def test_an_axis_is_declared_once_in_the_manifest_and_not_again_in_config_keys(
    tmp_path: Path,
) -> None:
    """Accepting the axis name does not make a fixed knob settable."""
    adapter = load(
        tmp_path,
        "\nMODES = {\n"
        '    "list": Mode(\n'
        '        product="text", fields=("key",), axes={"concurrency": Default(4, "help")}\n'
        "    ),\n"
        '    "fixed": Mode(product="text", fields=("key",), axes={"concurrency": Fixed(256)}),\n'
        "}\n",
    )
    assert not adapter.config_keys
    assert adapter.effective_config("list", {"concurrency": 16}) == {
        "concurrency": 16,
        "mode": "list",
    }
    with pytest.raises(CommandAdapterError):
        adapter.effective_config("fixed", {"concurrency": 16})


@pytest.mark.parametrize("config", [{"page_size": 1000}, {"mode": "list"}])
def test_the_effective_config_refuses_what_the_capsule_never_declared(
    tmp_path: Path, config: dict[str, object]
) -> None:
    adapter = load(tmp_path)
    with pytest.raises(CommandAdapterError):
        adapter.effective_config("list", config)
    with pytest.raises(CommandAdapterError):
        adapter.effective_config("recursive", {})


@pytest.mark.parametrize("axis", [Default, Ceiling])
@pytest.mark.parametrize("provenance", ["help", "unverified", "source@8f2c1a0"])
def test_a_recorded_number_carries_its_provenance(
    axis: type[Default | Ceiling], provenance: str
) -> None:
    """Both states that record a subject's own number state where it came from."""
    assert axis(4, provenance).value == 4


@pytest.mark.parametrize("axis", [Default, Ceiling])
@pytest.mark.parametrize("provenance", ["", "believed", "source@", "help ", "SOURCE@abc"])
def test_a_recorded_number_with_no_receipt_behind_it_is_refused(
    axis: type[Default | Ceiling], provenance: str
) -> None:
    """A recorded-but-wrong value is worse than an absent one: it claims knowledge."""
    with pytest.raises(CommandAdapterError):
        axis(4, provenance)


@pytest.mark.parametrize(
    "axes",
    [
        {"checkers": Ceiling(4, "help")},
        {"workers": Fixed(8)},
        {"page_size": Default(1000, "help")},
    ],
)
def test_an_axis_the_study_has_not_reserved_is_refused(axes: dict[str, object]) -> None:
    """A capsule free to name its own axis makes the axis unqueryable across tools."""
    with pytest.raises(CommandAdapterError):
        Mode(product="text", fields=("key",), axes=axes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "axis",
    [Fixed(50), Fixed(100), Default(HEAP_PERCENT, "help"), Ceiling(HEAP_PERCENT, "help"), Inert()],
)
def test_the_heap_share_is_the_harness_s_and_a_capsule_may_only_restate_it(axis: object) -> None:
    with pytest.raises(CommandAdapterError):
        Mode(product="text", fields=("key",), axes={"heap_percent": axis})  # type: ignore[dict-item]


def test_a_managed_runtime_capsule_hashes_the_share_it_ran_under(tmp_path: Path) -> None:
    adapter = load(
        tmp_path,
        "\nMODES = {\n"
        '    "list": Mode(\n'
        '        product="text",\n'
        '        fields=("key",),\n'
        '        axes={"heap_percent": Fixed(HEAP_PERCENT)},\n'
        "    )\n"
        "}\n",
    )
    assert adapter.effective_config("list", {}) == {"heap_percent": HEAP_PERCENT, "mode": "list"}


@pytest.mark.parametrize(
    ("product", "fields"),
    [("text", ("key", "path")), ("dataset", ("key",)), ("text", ()), ("text", ("key", "key"))],
)
def test_a_mode_describes_its_artifact_in_the_shared_vocabulary(
    product: str, fields: tuple[str, ...]
) -> None:
    with pytest.raises(CommandAdapterError):
        Mode(product=product, fields=fields)


def test_mode_fields_are_canonically_ordered() -> None:
    """Two modes populating the same columns must declare the same tuple."""
    assert Mode(product="text", fields=("mtime", "key", "size")).fields == ("key", "size", "mtime")


def test_a_plan_may_demote_a_mode_and_never_promote_one() -> None:
    assert Mode(product="text", fields=("key",)).purpose_ceiling == "measurement"
    summarize = Mode(product="text", fields=("key",), purpose_ceiling="diagnostic")
    assert summarize.permits_purpose("diagnostic")
    assert not summarize.permits_purpose("canary")
    assert not summarize.permits_purpose("measurement")
    assert Mode(product="text", fields=("key",)).permits_purpose("canary")
    with pytest.raises(CommandAdapterError):
        summarize.permits_purpose("smoke")


CHAIN_MODES = (
    "\nMODES = {\n"
    '    "list": Mode(product="text", fields=("key",), artifacts={"keyspace": "k.ks"}),\n'
    '    "split": Mode(\n'
    '        product="text",\n'
    '        fields=("key",),\n'
    '        purpose_ceiling="preparation",\n'
    '        artifacts={"hints": "hints.input"},\n'
    "    ),\n"
    '    "hinted": Mode(product="text", fields=("key",)),\n'
    "}\n"
)
"""Two producers, each publishing one named file, and the mode that consumes them."""


def test_a_prerequisite_chain_names_a_mode_and_the_artifact_taken_from_it(
    tmp_path: Path,
) -> None:
    adapter = load(
        tmp_path,
        CHAIN_MODES + 'REQUIRES = {"hinted": (("list", "keyspace"), ("split", "hints"))}\n',
    )
    assert adapter.requires == {
        "hinted": (Requirement("list", "keyspace"), Requirement("split", "hints"))
    }
    # What a settled producer's evidence is read with: the name, and the file the
    # producing mode publishes it under.
    assert adapter.consumed_artifact("list") == ("keyspace", "k.ks")
    with pytest.raises(CommandAdapterError):
        # Nothing declares a chain through `hinted`, so nothing consumes it.
        adapter.consumed_artifact("hinted")


@pytest.mark.parametrize(
    "requires",
    [
        # A mode of another tool: that artifact is an input the study supplies.
        '{"hinted": (("inventory", "keyspace"),)}',
        '{"unknown": (("list", "keyspace"),)}',
        '{"hinted": (("hinted", "keyspace"),)}',
        '{"hinted": (("list", "keyspace"),), "list": (("hinted", "keyspace"),)}',
        '{"hinted": (("list", "keyspace"), ("list", "keyspace"))}',
        '{"hinted": ()}',
        '{"hinted": "list"}',
        # The bare mode: sugar for "its sole artifact" is exactly the inference
        # that breaks when a producer publishes a second file.
        '{"hinted": ("list",)}',
        # An artifact the producing mode does not publish.
        '{"hinted": (("list", "hints"),)}',
        # A producer with nothing declared to consume.
        '{"hinted": (("hinted", "keyspace"),)}',
        # Two consumers disagreeing about what one producer is consumed for.
        '{"hinted": (("list", "keyspace"),), "split": (("list", "listing"),)}',
    ],
)
def test_a_prerequisite_the_planner_could_not_expand_offline_is_refused(
    tmp_path: Path, requires: str
) -> None:
    with pytest.raises(CommandAdapterError):
        load(tmp_path, CHAIN_MODES + f"REQUIRES = {requires}\n")


@pytest.mark.parametrize(
    "declare",
    [
        # A measured product this mode does not publish is a promise nothing keeps.
        lambda: Mode(TEXT, KEY, artifacts={"keyspace": "k.ks"}, product_artifact="listing"),
        lambda: Mode(TEXT, KEY, product_artifact="listing"),
        # One file under two names makes the reverse lookup a guess.
        lambda: Mode(TEXT, KEY, artifacts={"keyspace": "k.ks", "listing": "k.ks"}),
        # A path outside the sink names bytes no attempt record accounts for.
        lambda: Mode(TEXT, KEY, artifacts={"keyspace": "/etc/passwd"}),
        lambda: Mode(TEXT, KEY, artifacts={"keyspace": "../k.ks"}),
        lambda: Mode(TEXT, KEY, artifacts={"keyspace": ""}),
        lambda: Mode(TEXT, KEY, artifacts={"": "k.ks"}),
    ],
)
def test_an_artifact_declaration_the_sink_could_not_hold_is_refused(
    declare: Callable[[], Mode],
) -> None:
    with pytest.raises(CommandAdapterError):
        declare()


def test_a_mode_names_the_artifact_that_carries_its_measured_product() -> None:
    """``product_artifact`` is which file, ``product`` is which format: a mode
    publishing a key distribution beside a Parquet listing has one of each."""
    mode = Mode(
        product="parquet",
        fields=("key",),
        artifacts={"keyspace": "k.ks", "listing": "listing.parquet"},
        product_artifact="listing",
    )
    assert mode.product == "parquet"
    assert mode.artifacts["listing"] == "listing.parquet"
    # Empty is the honest state while a product still streams through stdout.
    assert Mode(product="text", fields=("key",)).product_artifact == ""


def test_the_declared_executable_is_cross_checked_against_the_registered_image(
    tmp_path: Path,
) -> None:
    assert load(tmp_path, registered=ARGV).executables == (Executable("fixture", ARGV),)


def test_an_executable_the_registered_image_does_not_have_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CommandAdapterError):
        load(tmp_path, registered=("/usr/local/bin/fixture", "--serve"))


def test_a_mode_running_an_undeclared_executable_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CommandAdapterError):
        load(
            tmp_path,
            '\nMODES = {"list": Mode(product="text", fields=("key",), executable="ks-tool")}\n',
        )


def test_a_subject_that_can_issue_no_request_at_all_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CommandAdapterError, match="no request at all"):
        load(tmp_path, "\nSUPPORTS_UNSIGNED = False\nSUPPORTS_SIGNED = False\n")


def test_build_env_defaults_to_the_static_functional_environment(tmp_path: Path) -> None:
    adapter = load(tmp_path, '\nFUNCTIONAL_ENV = {"MC_HOST_s3": "https://s3.amazonaws.com"}\n')
    request = CommandRequest("list", "bucket", "region", tool="fixture")
    assert adapter.build_env(request) == {"MC_HOST_s3": "https://s3.amazonaws.com"}


def test_build_env_renders_the_share_of_the_ceiling_the_request_carries(tmp_path: Path) -> None:
    adapter = load(
        tmp_path,
        "\n\ndef build_env(request: CommandRequest) -> dict[str, str]:\n"
        '    return {"JAVA_TOOL_OPTIONS": f"-XX:MaxRAMPercentage={request.heap_percent}"}\n',
    )
    request = CommandRequest("list", "bucket", "region", tool="fixture", visible_memory_gb=8.0)
    assert request.heap_percent == HEAP_PERCENT
    assert adapter.build_env(request) == {
        "JAVA_TOOL_OPTIONS": f"-XX:MaxRAMPercentage={HEAP_PERCENT}"
    }


@pytest.mark.parametrize(
    "declaration",
    [
        "VALIDATE_ARTIFACT = 3",
        "VALIDATE_ARTIFACT = lambda: None",
        'VALIDATE_ARTIFACT = {"list": lambda: None}',
        'VALIDATE_ARTIFACT = {"split": lambda path: None}',
        "VALIDATE_ARTIFACT = {}",
        "def build_env(): return {}",
    ],
)
def test_a_capsule_hook_the_harness_could_not_call_is_refused(
    tmp_path: Path, declaration: str
) -> None:
    with pytest.raises(CommandAdapterError):
        load(tmp_path, f"\n{declaration}\n")


def test_a_declared_artifact_validator_is_exposed_per_producing_mode(tmp_path: Path) -> None:
    adapter = load(
        tmp_path,
        "\nfrom pathlib import Path\n\n\n"
        "def _cut_points(path: Path) -> None:\n"
        "    if not path.read_text().strip():\n"
        '        raise ValueError("empty cut point")\n\n\n'
        'VALIDATE_ARTIFACT = {"list": _cut_points}\n',
    )
    artifact = tmp_path / "hints"
    artifact.write_text("\n")
    with pytest.raises(ValueError, match="empty cut point"):
        adapter.validate_artifact["list"](artifact)
    # A mode with no entry produces bytes nothing structural can be said about.
    assert adapter.validate_artifact.get("absent") is None


def test_an_inline_setup_exec_names_one_preparation_mode_of_this_capsule(tmp_path: Path) -> None:
    adapter = load(
        tmp_path,
        "\nMODES = {\n"
        '    "split": Mode(product="text", fields=("key",), purpose_ceiling="preparation"),\n'
        '    "hinted": Mode(product="text", fields=("key",), inline="split"),\n'
        "}\n",
    )
    assert adapter.modes["hinted"].inline == "split"
    assert adapter.modes["split"].inline == ""


@pytest.mark.parametrize(
    "body",
    [
        # An inline mode this capsule does not have.
        '\nMODES = {"list": Mode(product="text", fields=("key",), inline="split")}\n',
        # A mode running itself as its own setup.
        '\nMODES = {"list": Mode(product="text", fields=("key",), inline="list")}\n',
        # A setup exec that is allowed to claim it measured something.
        "\nMODES = {\n"
        '    "split": Mode(product="text", fields=("key",)),\n'
        '    "hinted": Mode(product="text", fields=("key",), inline="split"),\n'
        "}\n",
        # A setup exec with a setup exec of its own.
        "\nMODES = {\n"
        '    "seed": Mode(product="text", fields=("key",), purpose_ceiling="preparation"),\n'
        '    "split": Mode(\n'
        '        product="text", fields=("key",), purpose_ceiling="preparation", inline="seed"\n'
        "    ),\n"
        '    "hinted": Mode(product="text", fields=("key",), inline="split"),\n'
        "}\n",
        # A setup exec that is also a chain link: it needs a step of its own.
        "\nMODES = {\n"
        '    "list": Mode(product="text", fields=("key",), artifacts={"keyspace": "k.ks"}),\n'
        '    "split": Mode(product="text", fields=("key",), purpose_ceiling="preparation"),\n'
        '    "hinted": Mode(product="text", fields=("key",), inline="split"),\n'
        "}\n"
        'REQUIRES = {"split": (("list", "keyspace"),)}\n',
    ],
)
def test_an_inline_setup_the_planner_could_not_read_offline_is_refused(
    tmp_path: Path, body: str
) -> None:
    """One flat step, inside one attempt: a nested inline or a chain under it
    would put a graph back where no reviewer and no slot can see it."""
    with pytest.raises(CommandAdapterError):
        load(tmp_path, body)
