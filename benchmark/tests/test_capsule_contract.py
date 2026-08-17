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
    load_command_adapter,
)

ARGV = ("/usr/local/bin/fixture",)
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
)

TOOL = "fixture"
EXECUTABLES = (Executable("fixture", ("/usr/local/bin/fixture",)),)
SUPPORTS_UNSIGNED = True
MODES = {"list": Mode(product="text", fields=("key", "size"))}


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return (*EXECUTABLES[0].argv, request.mode)
'''
LEGACY = '''\
"""A fixture capsule on the pre-manifest shape the loader still accepts."""

from benchmark.runtime.command_adapter import CommandRequest

TOOL = "fixture"
FIXED_COMMAND_PREFIX = ("/usr/local/bin/fixture",)
MODES = frozenset({"list", "shallow"})
SUPPORTS_UNSIGNED = True


def build_command(request: CommandRequest) -> tuple[str, ...]:
    return (*FIXED_COMMAND_PREFIX, request.mode)
'''


def write_capsule(
    tmp_path: Path,
    body: str = "",
    *,
    header: str = HEADER,
    registered: tuple[str, ...] | None = None,
) -> Path:
    """Write a fixture capsule and return its ``adapter/command.py``.

    ``registered`` writes a ``build/image.json``; without one there is no
    registered image to cross-check against, as for a staged capsule.
    """
    adapter = tmp_path / "fixture" / "adapter"
    adapter.mkdir(parents=True)
    path = adapter / "command.py"
    path.write_text(header + body, encoding="utf-8")
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
    header: str = HEADER,
    registered: tuple[str, ...] | None = None,
) -> LoadedCommandAdapter:
    return load_command_adapter(write_capsule(tmp_path, body, header=header, registered=registered))


def test_a_declared_axis_reaches_the_config_the_identity_hashes(tmp_path: Path) -> None:
    """A plan stating no concurrency must still record what the run listed at."""
    adapter = load(
        tmp_path,
        "\nMODES = {\n"
        '    "list": Mode(product="text", fields=("key",), axes={"concurrency": Ceiling(8)}),\n'
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
    with pytest.raises(CommandAdapterError, match="fixes concurrency"):
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
    with pytest.raises(CommandAdapterError, match="fixes concurrency"):
        adapter.effective_config("fixed", {"concurrency": 16})


@pytest.mark.parametrize(
    ("config", "match"),
    [
        ({"page_size": 1000}, "does not accept config key"),
        ({"mode": "list"}, "mode is a plan row field"),
    ],
)
def test_the_effective_config_refuses_what_the_capsule_never_declared(
    tmp_path: Path, config: dict[str, object], match: str
) -> None:
    adapter = load(tmp_path)
    with pytest.raises(CommandAdapterError, match=match):
        adapter.effective_config("list", config)
    with pytest.raises(CommandAdapterError, match="has no mode"):
        adapter.effective_config("recursive", {})


@pytest.mark.parametrize("provenance", ["help", "unverified", "source@8f2c1a0"])
def test_a_recorded_default_carries_its_provenance(provenance: str) -> None:
    assert Default(4, provenance).value == 4


@pytest.mark.parametrize("provenance", ["", "believed", "source@", "help ", "SOURCE@abc"])
def test_a_default_with_no_receipt_behind_it_is_refused(provenance: str) -> None:
    """A recorded-but-wrong value is worse than an absent one: it claims knowledge."""
    with pytest.raises(CommandAdapterError, match="provenance"):
        Default(4, provenance)


@pytest.mark.parametrize(
    "axes",
    [
        {"checkers": Ceiling(4)},
        {"workers": Fixed(8)},
        {"page_size": Default(1000, "help")},
    ],
)
def test_an_axis_the_study_has_not_reserved_is_refused(axes: dict[str, object]) -> None:
    """A capsule free to name its own axis makes the axis unqueryable across tools."""
    with pytest.raises(CommandAdapterError, match="not reserved"):
        Mode(product="text", fields=("key",), axes=axes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "axis",
    [Fixed(50), Fixed(100), Default(HEAP_PERCENT, "help"), Ceiling(HEAP_PERCENT), Inert()],
)
def test_the_heap_share_is_the_harness_s_and_a_capsule_may_only_restate_it(axis: object) -> None:
    with pytest.raises(CommandAdapterError, match="methodology share"):
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
    with pytest.raises(CommandAdapterError, match="unknown purpose"):
        summarize.permits_purpose("smoke")


def test_a_prerequisite_chain_is_this_capsule_s_own_modes_in_order(tmp_path: Path) -> None:
    adapter = load(
        tmp_path,
        "\nMODES = {\n"
        '    "list": Mode(product="text", fields=("key",)),\n'
        '    "split": Mode(product="text", fields=("key",), purpose_ceiling="preparation"),\n'
        '    "hinted": Mode(product="text", fields=("key",)),\n'
        "}\n"
        'REQUIRES = {"hinted": ("list", "split")}\n',
    )
    assert adapter.requires == {"hinted": ("list", "split")}


@pytest.mark.parametrize(
    ("requires", "match"),
    [
        ('{"hinted": ("inventory",)}', "does not have"),
        ('{"unknown": ("list",)}', "unknown mode"),
        ('{"hinted": ("hinted",)}', "depend on itself"),
        ('{"hinted": ("list",), "list": ("hinted",)}', "depend on itself"),
        ('{"hinted": ("list", "list")}', "repeats"),
        ('{"hinted": ()}', "non-empty tuple"),
        ('{"hinted": "list"}', "non-empty tuple"),
    ],
)
def test_a_prerequisite_the_planner_could_not_expand_offline_is_refused(
    tmp_path: Path, requires: str, match: str
) -> None:
    body = (
        "\nMODES = {\n"
        '    "list": Mode(product="text", fields=("key",)),\n'
        '    "hinted": Mode(product="text", fields=("key",)),\n'
        "}\n"
        f"REQUIRES = {requires}\n"
    )
    with pytest.raises(CommandAdapterError, match=match):
        load(tmp_path, body)


def test_the_declared_executable_is_cross_checked_against_the_registered_image(
    tmp_path: Path,
) -> None:
    assert load(tmp_path, registered=ARGV).executables == (Executable("fixture", ARGV),)


def test_an_executable_the_registered_image_does_not_have_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CommandAdapterError, match="registered executable"):
        load(tmp_path, registered=("/usr/local/bin/fixture", "--serve"))


def test_a_capsule_with_no_registered_image_still_loads(tmp_path: Path) -> None:
    """A staged or fixture capsule has no build receipt to disagree with."""
    assert load(tmp_path).fixed_command_prefix == ARGV


def test_a_mode_running_an_undeclared_executable_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CommandAdapterError, match="undeclared executable"):
        load(
            tmp_path,
            '\nMODES = {"list": Mode(product="text", fields=("key",), executable="ks-tool")}\n',
        )


def test_a_subject_that_can_issue_no_request_at_all_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CommandAdapterError, match="no request at all"):
        load(tmp_path, "\nSUPPORTS_UNSIGNED = False\nSUPPORTS_SIGNED = False\n")


def test_signing_defaults_to_available(tmp_path: Path) -> None:
    adapter = load(tmp_path)
    assert adapter.supports_signed and adapter.supports_unsigned


def test_the_pre_manifest_shape_still_loads(tmp_path: Path) -> None:
    """The eleven capsules convert one at a time, and the gates stay green meanwhile."""
    adapter = load(tmp_path, header=LEGACY)
    assert adapter.mode_names == frozenset({"list", "shallow"})
    assert adapter.modes == {}
    assert adapter.fixed_command_prefix == ARGV
    assert adapter.executables == (Executable("fixture", ARGV),)
    assert adapter.effective_config("list", {}) == {"mode": "list"}


def test_a_converted_capsule_states_its_executable_once(tmp_path: Path) -> None:
    with pytest.raises(CommandAdapterError, match="EXECUTABLES replaces it"):
        load(tmp_path, '\nFIXED_COMMAND_PREFIX = ("/usr/local/bin/fixture",)\n')


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
    ["VALIDATE_ARTIFACT = 3", "VALIDATE_ARTIFACT = lambda: None", "def build_env(): return {}"],
)
def test_a_capsule_hook_the_harness_could_not_call_is_refused(
    tmp_path: Path, declaration: str
) -> None:
    with pytest.raises(CommandAdapterError, match="must be callable"):
        load(tmp_path, f"\n{declaration}\n")


def test_a_declared_artifact_validator_is_exposed_to_the_harness(tmp_path: Path) -> None:
    adapter = load(
        tmp_path,
        "\nfrom pathlib import Path\n\n\n"
        "def VALIDATE_ARTIFACT(path: Path) -> None:\n"
        "    if not path.read_text().strip():\n"
        '        raise ValueError("empty cut point")\n',
    )
    assert adapter.validate_artifact is not None
    artifact = tmp_path / "hints"
    artifact.write_text("\n")
    with pytest.raises(ValueError, match="empty cut point"):
        adapter.validate_artifact(artifact)
