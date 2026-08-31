"""Command-adapter and build-fact boundary tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from benchmark.adapters import compile_command
from benchmark.runtime.build_selection import (
    BuildSelectionError,
    adapter_bundle_sha256,
    load_registered_selection,
)
from benchmark.runtime.command_adapter import (
    HEAP_PERCENT,
    Ceiling,
    CommandAdapterError,
    CommandRequest,
    Default,
    Fixed,
    Inert,
    LoadedCommandAdapter,
    Mode,
    Stated,
    load_command_adapter,
    shared_axis_values,
)

PRODUCT = {"listing": "listing.txt"}
LISTING = "listing"
"""What a fixture mode publishes its measured product as."""

ROOT = Path(__file__).parents[2]
TOOLS = (
    "aws-cli",
    "minio-mc",
    "ps3",
    "rclone",
    "s3-fast-list",
    "s3kor",
    "s3p",
    "s4cmd",
    "s5cmd",
    "s7cmd",
    "swath",
)
BUCKET = "bucket-x"
REGION = "region-y"
SINK = "/sink"
ARTIFACT = "/staged/artifact"
ENDPOINT = "http://127.0.0.1:19090"
"""Where the engine staged the artifact a consuming mode reads — the inbound
counterpart of :data:`SINK`. A consuming capsule refuses an empty one rather
than inventing a path, so every consuming mode is driven with this."""

REQUIRED_CONFIG: dict[tuple[str, str], dict[str, object]] = {
    # `-c` on `ks-tool split` is the segment count, not the listing width, so it
    # is no axis and carries no declared default: the plan states it or the
    # preparation refuses.
    ("s3-fast-list", "ks-split"): {"segments": 1000},
    ("s5cmd", "fanout-with-dirs"): {"shard_initials": ".0"},
}
"""Config a mode structurally cannot compile without, stated here rather than
defaulted in a capsule."""

MODE_EXECUTABLES: dict[tuple[str, str], tuple[str, ...]] = {
    ("s3-fast-list", "ks-split"): ("/usr/bin/ks-tool",),
    ("s5cmd", "fanout-with-dirs"): (
        "/usr/bin/python3",
        "/opt/benchmark/tools/s5cmd/adapter/fanout.py",
    ),
    ("s5cmd", "fanout-fixture-with-dirs"): (
        "/usr/bin/python3",
        "/opt/benchmark/tools/s5cmd/adapter/fanout.py",
    ),
}
"""Modes that run a capsule's *second* executable. Written out here, not read
from the capsule, so the binary a mode runs stays an independent expectation --
`build/image.json` registers the primary only and covers no other."""
REGIONS = (REGION, "eu-west-3")
PREFIXES = ("", "p x/雪/")
Q_CONTENTS = "Contents[].[Key,Size,ETag,LastModified,StorageClass]"
Q_VERSIONS = "Versions[].[Key,Size,ETag,LastModified,StorageClass]"


def adapter_path(tool: str) -> Path:
    return ROOT / "tools" / tool / "adapter" / "command.py"


def load_module(tool: str) -> ModuleType:
    path = adapter_path(tool)
    spec = importlib.util.spec_from_file_location(f"_command_test_{tool.replace('-', '_')}", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _aws(mode: str, prefix: str) -> tuple[str, ...]:
    if mode == "s3api-v2-remainder":
        return (
            "s3api",
            "list-objects-v2",
            "--bucket",
            BUCKET,
            "--region",
            REGION,
            "--no-sign-request",
            "--delimiter",
            "/",
            "--query",
            Q_CONTENTS,
            "--output",
            "text",
        )
    if mode.startswith("s3-ls-"):
        recursive = ("--recursive",) if mode == "s3-ls-recursive" else ()
        return (
            "s3",
            "ls",
            f"s3://{BUCKET}/{prefix}",
            *recursive,
            "--region",
            REGION,
            "--no-sign-request",
        )
    operation, query, output = {
        "s3api-v2-text": ("list-objects-v2", Q_CONTENTS, "text"),
        "s3api-v2-json": ("list-objects-v2", None, "json"),
        "s3api-v2-yamlstream": ("list-objects-v2", None, "yaml-stream"),
        "s3api-v1-text": ("list-objects", Q_CONTENTS, "text"),
        "s3api-versions-text": ("list-object-versions", Q_VERSIONS, "text"),
        "s3api-v2-delimiter": ("list-objects-v2", None, "json"),
    }[mode]
    argv = ["s3api", operation, "--bucket", BUCKET, "--region", REGION, "--no-sign-request"]
    if mode == "s3api-v2-delimiter":
        argv.extend(("--delimiter", "/"))
    if prefix:
        argv.extend(("--prefix", prefix))
    if query:
        argv.extend(("--query", query))
    return *argv, "--output", output


def _minio(mode: str, prefix: str) -> tuple[str, ...]:
    target = f"s3/{BUCKET}/{prefix}"
    return {
        "recursive": ("ls", "--recursive", target),
        "recursive-json": ("--json", "ls", "--recursive", target),
        "shallow": ("ls", target),
        "shallow-json": ("--json", "ls", target),
        "versions-json": ("--json", "ls", "--versions", "--recursive", target),
        "find": ("find", target),
        "find-json": ("--json", "find", target),
    }[mode]


def _ps3(mode: str, prefix: str) -> tuple[str, ...]:
    if prefix:
        raise CommandAdapterError("pS3 has no prefix")
    operation = {
        "list": "list-objects-v2",
        "list-versions": "list-object-versions",
        "head": "head-objects",
    }[mode]
    return operation, "--bucket", BUCKET, "--region", REGION


def _rclone(mode: str, prefix: str) -> tuple[str, ...]:
    backend = f"s3,provider=AWS,region={REGION}" + (",list_version=1" if mode == "listv1" else "")
    remote = f":{backend}:{BUCKET}" + (f"/{prefix}" if prefix else "")
    standard = ("--files-only", "--use-server-modtime", "--no-mimetype")
    return {
        "recursive-fastlist": ("lsjson", "--fast-list", *standard, "-R", remote),
        "recursive-hierarchical": ("lsjson", *standard, "-R", remote),
        "recursive-walk": (
            "lsjson",
            *standard,
            "--disable",
            "ListR",
            "--checkers",
            "8",
            "-R",
            remote,
        ),
        "recursive-walk-with-dirs": (
            "lsjson",
            "--use-server-modtime",
            "--no-mimetype",
            "--disable",
            "ListR",
            "--checkers",
            "8",
            "-R",
            remote,
        ),
        "delimiter-shallow": ("lsjson", "--use-server-modtime", "--no-mimetype", remote),
        "listv1": ("lsjson", "--fast-list", *standard, "-R", remote),
        "lsf": (
            "lsf",
            "--fast-list",
            "--files-only",
            "--format",
            "ps",
            "--separator",
            ";",
            "-R",
            remote,
        ),
        "debug": ("lsjson", "--fast-list", *standard, "-R", "-vv", "--dump", "headers", remote),
        "walk-debug": (
            "lsjson",
            *standard,
            "--disable",
            "ListR",
            "--checkers",
            "8",
            "-R",
            "-vv",
            "--dump",
            "headers",
            remote,
        ),
    }[mode]


def _s3_fast_list(mode: str, prefix: str) -> tuple[str, ...]:
    if mode == "ks-split":
        return ("split", "-k", ARTIFACT, "-c", "1000", "-o", f"{SINK}/hints.input")
    # The hinted mode's whole point: cut points from the staged hints file, and
    # the width the capsule declares as the subject's own.
    hinted_paths = {
        "list-hinted": ARTIFACT,
        "list-hinted-fixture": "/fixtures/source/s3-fast-list-hints.input",
    }
    hinted = ("-c", "100", "-k", hinted_paths[mode]) if mode in hinted_paths else ()
    scoped = ("--prefix", prefix) if prefix else ()
    return (
        "--no-sign-request",
        # The listing is the measured product and lands in the sink under its own
        # name; it travelled as `stdout.log.gz` for as long as this said
        # `/dev/stdout`, gzipping Parquet under a name that called it a log.
        "--output-parquet-file",
        f"{SINK}/listing.parquet",
        # A sink is offered here, so the key distribution is published rather
        # than discarded — it is what `ks-split` consumes.
        "--output-ks-file",
        f"{SINK}/keyspace.ks",
        *hinted,
        *scoped,
        "list",
        "--region",
        REGION,
        "--bucket",
        BUCKET,
    )


def _s3kor(mode: str, prefix: str) -> tuple[str, ...]:
    uri = f"s3://{BUCKET}" + (f"/{prefix}" if prefix else "")
    versions = ("--all-versions",) if mode == "list-versions" else ()
    return "ls", *versions, "--region", REGION, uri


def _s3p(mode: str, prefix: str) -> tuple[str, ...]:
    head = {
        "ls": ("ls",),
        "ls-long": ("ls", "--long"),
        "ls-raw": ("ls", "--raw"),
        "summarize": ("summarize",),
    }[mode]
    scoped = ("--prefix", prefix) if prefix else ()
    return *head, "--bucket", BUCKET, "--region", REGION, "--list-concurrency", "100", *scoped


def _s4cmd(mode: str, prefix: str) -> tuple[str, ...]:
    url = f"s3://{BUCKET}/{prefix}"
    return {
        "recursive": ("ls", "-r", "-c", "32", url),
        "shallow": ("ls", "-c", "32", url),
        "show-directory": ("ls", "-d", "-c", "32", url),
        "du": ("du", "-r", "-c", "32", url),
    }[mode]


def _s5cmd(mode: str, prefix: str) -> tuple[str, ...]:
    target, recursive = f"s3://{BUCKET}/{prefix}", f"s3://{BUCKET}/{prefix}*"
    return {
        "recursive": ("--no-sign-request", "ls", "-e", "-s", recursive),
        "recursive-with-dirs": ("--no-sign-request", "ls", "-e", "-s", recursive),
        "fanout-with-dirs": (
            "--s5cmd",
            "/s5cmd",
            "--bucket",
            BUCKET,
            "--prefix",
            prefix,
            "--shard",
            ".",
            "--shard",
            "0",
            "--numworkers",
            "256",
            "--unsigned",
        ),
        "fanout-fixture-with-dirs": (
            "--s5cmd",
            "/s5cmd",
            "--bucket",
            BUCKET,
            "--prefix",
            prefix,
            "--shard-file",
            "/fixtures/source/s5cmd-shards.input",
            "--numworkers",
            "256",
            "--unsigned",
        ),
        "delimiter": ("--no-sign-request", "ls", "-e", "-s", target),
        "rootkeys": ("--no-sign-request", "ls", "-e", "-s", target),
        "json": ("--json", "--no-sign-request", "ls", recursive),
        "listv1": ("--no-sign-request", "--use-list-objects-v1", "ls", "-e", "-s", recursive),
        "allversions": ("--no-sign-request", "ls", "--all-versions", "-e", "-s", recursive),
        "fullpath": ("--no-sign-request", "ls", "--show-fullpath", recursive),
    }[mode]


def _s7cmd(mode: str, prefix: str) -> tuple[str, ...]:
    target = f"s3://{BUCKET}/{prefix}"
    obs = ("-vv", "--disable-color-tracing")
    parallel = (
        "--max-parallel-listings",
        "64",
        "--max-parallel-listing-max-depth",
        "2",
    )
    anon = (
        "--target-no-sign-request",
        "--target-region",
        REGION,
        "--connect-timeout-milliseconds",
        "15000",
    )
    fields = ("--tsv", "--show-storage-class", "--show-etag")
    return {
        "recursive-tsv": ("ls", "-r", *obs, *fields, *parallel, *anon, target),
        "recursive-tsv-nosort": ("ls", "-r", *obs, "--no-sort", *fields, *parallel, *anon, target),
        "recursive-aligned": ("ls", "-r", *obs, *parallel, *anon, target),
        "recursive-json": ("ls", "-r", *obs, "--json", *parallel, *anon, target),
        "recursive-one": ("ls", "-r", *obs, "-1", *parallel, *anon, target),
        "recursive-one-nosort": (
            "ls",
            "-r",
            *obs,
            "--no-sort",
            "-1",
            *parallel,
            *anon,
            target,
        ),
        "all-versions": ("ls", "-r", *obs, "--all-versions", *fields, *parallel, *anon, target),
        "max-depth": ("ls", "-r", *obs, "--max-depth", "1", *fields, *parallel, *anon, target),
        "shallow-tsv": ("ls", *obs, *fields, *anon, target),
        "bucket-list": ("ls", *obs, *anon),
    }[mode]


def _swath(mode: str, prefix: str) -> tuple[str, ...]:
    uri = f"s3://{BUCKET}" + (f"/{prefix}" if prefix else "")
    head = (
        "-v",
        "--color",
        "never",
        "list",
        uri,
        "--region",
        REGION,
        "--no-sign-request",
        # No row states one here, so the capsule renders swath's own declared 64.
        "--concurrency",
        "64",
    )
    common = (*head, "--checkpoint", "none")
    dataset = f"{SINK}/listing"
    return {
        "recursive-tsv": (*common, "--format", "tsv"),
        "recursive-jsonl": (*common, "--format", "jsonl"),
        "recursive-tsv-dataset": (
            *common,
            "--format",
            "tsv",
            "--output-type",
            "dir",
            "-o",
            dataset,
            "--text-writers",
            "3",
            "--compression",
            "none",
        ),
        "recursive-tsv-zstd": (
            *common,
            "--format",
            "tsv",
            "--output-type",
            "dir",
            "-o",
            dataset,
            "--text-writers",
            "3",
            "--compression",
            "zstd",
        ),
        "recursive-table": (*common, "--format", "table"),
        "seed-none": (*common, "--format", "tsv", "--tune", "seed.mode=none"),
        "recursive-parquet": (*common, "--format", "parquet", "-o", dataset),
        "recursive-parquet-sorted": (
            *head,
            "--checkpoint",
            "auto",
            "--format",
            "parquet",
            "-o",
            dataset,
            "--sort",
            "--tune",
            "sort.ignore-disk-check=on",
        ),
    }[mode]


EXPECTED: dict[str, Callable[[str, str], tuple[str, ...]]] = {
    "aws-cli": _aws,
    "minio-mc": _minio,
    "ps3": _ps3,
    "rclone": _rclone,
    "s3-fast-list": _s3_fast_list,
    "s3kor": _s3kor,
    "s3p": _s3p,
    "s4cmd": _s4cmd,
    "s5cmd": _s5cmd,
    "s7cmd": _s7cmd,
    "swath": _swath,
}
FIXED_PREFIXES = {
    "aws-cli": ("/usr/local/bin/aws",),
    "minio-mc": ("/usr/bin/mc",),
    "ps3": ("/usr/local/bin/pS3",),
    "rclone": ("/usr/local/bin/rclone",),
    "s3-fast-list": ("/usr/bin/s3-fast-list",),
    "s3kor": ("/usr/local/bin/s3kor",),
    "s3p": ("/usr/local/bin/s3p",),
    "s4cmd": ("/usr/local/bin/s4cmd",),
    "s5cmd": ("/s5cmd",),
    "s7cmd": ("/usr/local/bin/s7cmd",),
    "swath": ("/opt/java/openjdk/bin/java", "-jar", "/opt/swath/swath.jar"),
}
EXPECTED_MODES = {
    "aws-cli": {
        "s3api-v2-text",
        "s3api-v2-json",
        "s3api-v2-yamlstream",
        "s3api-v1-text",
        "s3api-versions-text",
        "s3api-v2-delimiter",
        "s3api-v2-remainder",
        "s3-ls-recursive",
        "s3-ls-delimiter",
    },
    "minio-mc": {
        "recursive",
        "recursive-json",
        "shallow",
        "shallow-json",
        "versions-json",
        "find",
        "find-json",
    },
    "ps3": {"list", "list-versions", "head"},
    "rclone": {
        "recursive-fastlist",
        "recursive-hierarchical",
        "recursive-walk",
        "recursive-walk-with-dirs",
        "delimiter-shallow",
        "listv1",
        "lsf",
        "debug",
        "walk-debug",
    },
    "s3-fast-list": {"list", "ks-split", "list-hinted", "list-hinted-fixture"},
    "s3kor": {"list", "list-versions"},
    "s3p": {"ls", "ls-long", "ls-raw", "summarize"},
    "s4cmd": {"recursive", "shallow", "show-directory", "du"},
    "s5cmd": {
        "recursive",
        "recursive-with-dirs",
        "fanout-with-dirs",
        "fanout-fixture-with-dirs",
        "delimiter",
        "rootkeys",
        "json",
        "listv1",
        "allversions",
        "fullpath",
    },
    "s7cmd": {
        "recursive-tsv",
        "recursive-tsv-nosort",
        "recursive-aligned",
        "recursive-json",
        "recursive-one",
        "recursive-one-nosort",
        "all-versions",
        "max-depth",
        "shallow-tsv",
        "bucket-list",
    },
    "swath": {
        "recursive-tsv",
        "recursive-jsonl",
        "recursive-tsv-dataset",
        "recursive-tsv-zstd",
        "recursive-table",
        "seed-none",
        "recursive-parquet",
        "recursive-parquet-sorted",
    },
}


@pytest.mark.parametrize("tool", TOOLS)
def test_registered_adapter_and_build_facts_agree(tool: str) -> None:
    selected = load_registered_selection(ROOT, tool)
    adapter = load_command_adapter(ROOT / "tools" / tool / "adapter/command.py", expected_tool=tool)
    assert adapter.fixed_command_prefix == selected.executable
    assert selected.adapter_bundle_sha256 == adapter_bundle_sha256(
        ROOT / "tools" / tool / "adapter"
    )


@pytest.mark.parametrize("tool", TOOLS)
def test_every_mode_matches_the_frozen_subject_argv_contract(tool: str) -> None:
    """Vary every live mode, prefix, and region against independent expected argv."""
    module = load_module(tool)
    adapter = load_command_adapter(adapter_path(tool))
    assert set(module.MODES) == EXPECTED_MODES[tool]
    for mode in EXPECTED_MODES[tool]:
        for prefix in PREFIXES:
            for region in REGIONS:
                request = CommandRequest(
                    mode,
                    BUCKET,
                    region,
                    prefix,
                    tool=tool,
                    # The stratum the planner would pick: unsigned wherever the
                    # capsule can issue it, since signing ~1,000 requests is a
                    # different measurement and the cheaper one is the default.
                    signed=not adapter.supports_unsigned,
                    config=REQUIRED_CONFIG.get((tool, mode), {}),
                    sink_dir=SINK,
                    artifact_path=ARTIFACT if mode in consuming_modes(adapter) else "",
                )
                try:
                    expected = EXPECTED[tool](mode, prefix)
                except CommandAdapterError:
                    with pytest.raises(CommandAdapterError):
                        adapter.compile(request)
                else:
                    expected = tuple(
                        region
                        if item == REGION
                        else item.replace(f"region={REGION}", f"region={region}")
                        for item in expected
                    )
                    executable = MODE_EXECUTABLES.get((tool, mode), FIXED_PREFIXES[tool])
                    assert adapter.compile(request) == (*executable, *expected)


@pytest.mark.parametrize("tool", TOOLS)
def test_an_empty_endpoint_is_byte_identical_for_every_capsule_mode(tool: str) -> None:
    """Adding the request field must not perturb ordinary S3 attempts."""
    adapter = load_command_adapter(adapter_path(tool))
    for mode in sorted(adapter.modes):
        common = {
            "tool": tool,
            "signed": not adapter.supports_unsigned,
            "config": REQUIRED_CONFIG.get((tool, mode), {}),
            "sink_dir": SINK,
            "artifact_path": ARTIFACT if mode in consuming_modes(adapter) else "",
            "visible_memory_gb": 2.0,
        }
        ordinary = CommandRequest(mode, BUCKET, REGION, **common)  # type: ignore[arg-type]
        explicit_empty = CommandRequest(
            mode,
            BUCKET,
            REGION,
            endpoint_url="",
            **common,  # type: ignore[arg-type]
        )
        assert adapter.compile(explicit_empty) == adapter.compile(ordinary)
        assert adapter.build_env(explicit_empty) == adapter.build_env(ordinary)


@pytest.mark.parametrize(
    ("tool", "mode", "prefix", "argv_fragment", "expected_env"),
    [
        ("aws-cli", "s3api-v2-text", "p/", ("--endpoint-url", ENDPOINT), {}),
        (
            "minio-mc",
            "recursive",
            "p/",
            (),
            {"MC_HOST_s3": ENDPOINT, "MC_REGION": REGION},
        ),
        ("ps3", "list", "", ("--endpoint-url", ENDPOINT), {}),
        (
            "rclone",
            "recursive-fastlist",
            "p/",
            (
                f':s3,provider=AWS,region={REGION},endpoint="{ENDPOINT}",'
                f"force_path_style=true:{BUCKET}/p/",
            ),
            {},
        ),
        ("s3-fast-list", "list", "p/", ("--endpoint-url", ENDPOINT), {}),
        ("s3kor", "list", "p/", ("--custom-endpoint-url", ENDPOINT), {}),
        (
            "s3p",
            "ls",
            "p/",
            (),
            {"NODE_OPTIONS": "--max-old-space-size=1536", "S3_ENDPOINT": ENDPOINT},
        ),
        ("s5cmd", "recursive", "p/", ("--endpoint-url", ENDPOINT), {}),
        (
            "s7cmd",
            "recursive-tsv",
            "p/",
            ("--target-endpoint-url", ENDPOINT, "--target-force-path-style"),
            {},
        ),
        (
            "swath",
            "recursive-tsv",
            "p/",
            ("--endpoint-url", ENDPOINT),
            {"JAVA_TOOL_OPTIONS": f"-XX:MaxRAMPercentage={HEAP_PERCENT}"},
        ),
    ],
)
def test_compatible_capsules_render_the_receipted_endpoint_mechanism(
    tool: str,
    mode: str,
    prefix: str,
    argv_fragment: tuple[str, ...],
    expected_env: dict[str, str],
) -> None:
    adapter = load_command_adapter(adapter_path(tool))
    request = CommandRequest(
        mode,
        BUCKET,
        REGION,
        prefix,
        tool=tool,
        signed=not adapter.supports_unsigned,
        sink_dir=SINK,
        visible_memory_gb=2.0,
        endpoint_url=ENDPOINT,
    )
    argv = adapter.compile(request)
    assert not argv_fragment or any(
        argv[index : index + len(argv_fragment)] == argv_fragment
        for index in range(len(argv) - len(argv_fragment) + 1)
    )
    assert adapter.build_env(request) == expected_env


@pytest.mark.parametrize(
    ("tool", "mode", "prefix"),
    [
        ("aws-cli", "s3api-v1-text", "prefix/"),
        ("aws-cli", "s3api-versions-text", "prefix/"),
        ("minio-mc", "versions-json", "prefix/"),
        ("minio-mc", "find", ""),
        ("ps3", "list-versions", ""),
        ("ps3", "head", ""),
        ("rclone", "listv1", "prefix/"),
        ("s3kor", "list-versions", "prefix/"),
        ("s4cmd", "recursive", "prefix/"),
        ("s5cmd", "listv1", "prefix/"),
        ("s5cmd", "allversions", "prefix/"),
        ("s7cmd", "all-versions", "prefix/"),
        ("s7cmd", "bucket-list", ""),
    ],
)
def test_a_non_v2_or_proven_incompatible_mode_refuses_the_replay_endpoint(
    tool: str, mode: str, prefix: str
) -> None:
    adapter = load_command_adapter(adapter_path(tool))
    request = CommandRequest(
        mode,
        BUCKET,
        REGION,
        prefix,
        tool=tool,
        signed=not adapter.supports_unsigned,
        sink_dir=SINK,
        visible_memory_gb=2.0,
        endpoint_url=ENDPOINT,
    )
    with pytest.raises(CommandAdapterError, match="replay endpoint"):
        adapter.compile(request)


def test_aws_custom_endpoint_stays_inside_the_receipted_ip_literal_boundary() -> None:
    adapter = load_command_adapter(adapter_path("aws-cli"))
    with pytest.raises(CommandAdapterError, match="IP-literal"):
        adapter.compile(
            CommandRequest(
                "s3api-v2-text",
                BUCKET,
                REGION,
                tool="aws-cli",
                endpoint_url="http://replay.internal:19090",
            )
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        'http://127.0.0.1:19090/"quoted',
        "http://127.0.0.1:19090/back\\slash",
        "http://127.0.0.1:19090/path,field",
    ],
)
def test_rclone_refuses_an_endpoint_that_breaks_its_quoted_connection_field(
    endpoint: str,
) -> None:
    adapter = load_command_adapter(adapter_path("rclone"))
    with pytest.raises(CommandAdapterError, match="quote, backslash, or comma"):
        adapter.compile(
            CommandRequest(
                "recursive-fastlist",
                BUCKET,
                REGION,
                tool="rclone",
                endpoint_url=endpoint,
            )
        )


def test_s3_fast_list_fixture_hints_digest_is_a_declared_config_key() -> None:
    """``hints_file_sha256`` binds the staged hints companion (gcp_batch), the
    same way s5cmd declares ``shard_file_sha256`` for its fixture fanout: this
    build never reads it, but a plan setting it must not be refused as an
    unknown config key.
    """
    adapter = load_command_adapter(adapter_path("s3-fast-list"))
    command = adapter.compile(
        CommandRequest(
            "list-hinted-fixture",
            BUCKET,
            REGION,
            tool="s3-fast-list",
            config={"concurrency": 100, "segments": 1000, "hints_file_sha256": "a" * 64},
            sink_dir=SINK,
        )
    )
    assert command == adapter.compile(
        CommandRequest(
            "list-hinted-fixture",
            BUCKET,
            REGION,
            tool="s3-fast-list",
            config={"concurrency": 100, "segments": 1000},
            sink_dir=SINK,
        )
    )


def test_s3_fast_list_local_split_does_not_receive_the_remote_endpoint() -> None:
    adapter = load_command_adapter(adapter_path("s3-fast-list"))
    ordinary = adapter.compile(
        CommandRequest(
            "ks-split",
            BUCKET,
            REGION,
            tool="s3-fast-list",
            config={"segments": 8},
            sink_dir=SINK,
            artifact_path=ARTIFACT,
        )
    )
    replay = adapter.compile(
        CommandRequest(
            "ks-split",
            BUCKET,
            REGION,
            tool="s3-fast-list",
            config={"segments": 8},
            sink_dir=SINK,
            artifact_path=ARTIFACT,
            endpoint_url=ENDPOINT,
        )
    )
    assert replay == ordinary
    assert ENDPOINT not in replay


def test_s5cmd_fanout_accepts_disjoint_complete_prefixes() -> None:
    adapter = load_command_adapter(adapter_path("s5cmd"))
    command = adapter.compile(
        CommandRequest(
            "fanout-with-dirs",
            BUCKET,
            REGION,
            tool="s5cmd",
            config={
                "concurrency": 8,
                "shard_prefixes": "blend.2020,blend.2021,index.html",
            },
        )
    )
    assert command.count("--shard") == 3
    assert "blend.2020" in command
    assert "blend.2021" in command
    assert "index.html" in command
    assert command[command.index("--numworkers") + 1] == "8"


def test_s5cmd_fanout_shard_prefixes_can_contain_underscores() -> None:
    # SAFE_PREFIX admits '_', so the separator must not: a comma-separated
    # list is the only encoding that keeps an underscore-bearing S3 key
    # prefix (e.g. "index_blend") expressible as a single shard.
    adapter = load_command_adapter(adapter_path("s5cmd"))
    command = adapter.compile(
        CommandRequest(
            "fanout-with-dirs",
            BUCKET,
            REGION,
            tool="s5cmd",
            config={"shard_prefixes": "index_blend,blend.2020"},
        )
    )
    assert command.count("--shard") == 2
    assert "index_blend" in command
    assert "blend.2020" in command


def test_ps3_renders_the_prefix_discovery_cutoff() -> None:
    adapter = load_command_adapter(adapter_path("ps3"))
    command = adapter.compile(
        CommandRequest(
            "list",
            BUCKET,
            REGION,
            tool="ps3",
            signed=True,
            config={"prefix_count": 5000},
        )
    )
    assert command[command.index("--prefix-count") + 1] == "5000"


def test_rclone_endpoint_preserves_the_signed_credential_branch() -> None:
    adapter = load_command_adapter(adapter_path("rclone"))
    request = CommandRequest(
        "recursive-fastlist",
        BUCKET,
        REGION,
        tool="rclone",
        signed=True,
        endpoint_url=ENDPOINT,
    )
    remote = adapter.compile(request)[-1]
    assert "env_auth=true" in remote
    assert f'endpoint="{ENDPOINT}",force_path_style=true' in remote


def test_compile_command_forwards_endpoint_url_to_argv_and_environment() -> None:
    argv, env = compile_command(
        ROOT / "tools/s3p/adapter",
        "s3p",
        mode="ls",
        bucket=BUCKET,
        region=REGION,
        signed=True,
        visible_memory_gb=2.0,
        endpoint_url=ENDPOINT,
    )
    assert argv[:2] == ("/usr/local/bin/s3p", "ls")
    assert env == {"NODE_OPTIONS": "--max-old-space-size=1536", "S3_ENDPOINT": ENDPOINT}


def consuming_modes(adapter: LoadedCommandAdapter) -> frozenset[str]:
    """Modes fed an artifact one of this capsule's own steps produced.

    A mode declaring a chain consumes what the chain's last link published, and
    every link after the first consumes the link before it. An inline setup exec
    consumes what its consumer's chain staged, and hands the subject what it
    published — so the whole set falls out of the declarations and no roster has
    to restate it.
    """
    modes = set(adapter.requires)
    for chain in adapter.requires.values():
        modes.update(step.mode for step in chain[1:])
    modes.update(manifest.inline for manifest in adapter.modes.values() if manifest.inline)
    return frozenset(modes)


def _request(tool: str, adapter: LoadedCommandAdapter, **overrides: object) -> CommandRequest:
    """One live mode of a capsule, in the stratum the planner would pick for it."""
    return CommandRequest(
        sorted(EXPECTED_MODES[tool])[0],
        BUCKET,
        REGION,
        tool=tool,
        signed=not adapter.supports_unsigned,
        sink_dir=SINK,
        **overrides,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "4"])
def test_s4cmd_rejects_invalid_concurrency_override(value: object) -> None:
    adapter = load_command_adapter(adapter_path("s4cmd"))
    with pytest.raises(CommandAdapterError):
        adapter.compile(
            CommandRequest(
                "recursive",
                BUCKET,
                REGION,
                tool="s4cmd",
                signed=True,
                config={"concurrency": value},
            )
        )


def test_s4cmd_accepts_every_registered_concurrency_in_the_full_matrix() -> None:
    adapter = load_command_adapter(adapter_path("s4cmd"))
    for mode in EXPECTED_MODES["s4cmd"]:
        for prefix in PREFIXES:
            for region in REGIONS:
                for concurrency in range(1, 9):
                    argv = adapter.compile(
                        CommandRequest(
                            mode,
                            BUCKET,
                            region,
                            prefix,
                            tool="s4cmd",
                            signed=True,
                            config={"concurrency": concurrency},
                        )
                    )
                    index = argv.index("-c")
                    assert argv[index + 1] == str(concurrency)
                    assert argv[0] == "/usr/local/bin/s4cmd"
                    assert argv[-1] == f"s3://{BUCKET}/{prefix}"


@pytest.mark.parametrize("tool", TOOLS)
def test_a_capsule_refuses_a_config_key_it_never_declared(tool: str) -> None:
    """A misspelled knob is an error, not a sweep whose cells are all identical."""
    adapter = load_command_adapter(adapter_path(tool))
    with pytest.raises(CommandAdapterError, match="does not accept config key"):
        adapter.compile(_request(tool, adapter, config={"concurency": 1}))


@pytest.mark.parametrize("tool", TOOLS)
def test_a_concurrency_reaches_argv_only_where_the_capsule_declares_the_axis(tool: str) -> None:
    """Declared, never pinned: a mode with a settable axis must render the value.

    Axes are per mode, so the assertion is too: a settable (Default/Ceiling)
    axis must put the value in argv; a capsule that never accepts the key must
    refuse it; an Inert or undeclared mode makes no promise either way — the
    flag may exist upstream and bound nothing, which is the capsule's business.
    Read from the capsule's own declaration rather than a roster here.
    """
    adapter = load_command_adapter(adapter_path(tool))
    if "concurrency" not in adapter.accepted_config_keys:
        with pytest.raises(CommandAdapterError, match="does not accept config key"):
            adapter.compile(_request(tool, adapter, config={"concurrency": 2}))
        return
    for mode in sorted(adapter.modes):
        axis = adapter.modes[mode].axes.get("concurrency")
        if not isinstance(axis, Default | Ceiling):
            continue
        request = CommandRequest(
            mode,
            BUCKET,
            REGION,
            tool=tool,
            signed=not adapter.supports_unsigned,
            sink_dir=SINK,
            artifact_path=ARTIFACT if mode in consuming_modes(adapter) else "",
            config={"concurrency": 2, **REQUIRED_CONFIG.get((tool, mode), {})},
        )
        assert "2" in adapter.compile(request), f"{tool} {mode} accepted 2 and dropped it"


def test_swath_declares_what_each_mode_produces_and_what_it_asked_for() -> None:
    """The JVM subject's manifest: product per mode, the asked-for width, the heap share."""
    adapter = load_command_adapter(adapter_path("swath"))
    assert {mode: manifest.product for mode, manifest in adapter.modes.items()} == {
        "recursive-tsv": "text",
        "recursive-jsonl": "text",
        "recursive-tsv-dataset": "text",
        "recursive-tsv-zstd": "text",
        "recursive-table": "text",
        "seed-none": "text",
        "recursive-parquet": "parquet",
        "recursive-parquet-sorted": "parquet-sorted",
    }
    # The aligned sink discards etag and storage_class, so it cannot be verified
    # on the same fields as the modes it would otherwise be ranked against.
    assert adapter.modes["recursive-table"].fields == ("key", "size", "mtime")
    assert not adapter.modes["recursive-table"].permits_purpose("measurement")
    # A silent plan records swath's own width, not the study's cap: 64 from
    # upstream source. What a campaign asks for is a row field, and what the run
    # achieved is evidence — --concurrency is an AIMD limit starting at min(4, N).
    assert adapter.effective_config("recursive-tsv", {}) == {
        "concurrency": 64,
        "heap_percent": HEAP_PERCENT,
        "mode": "recursive-tsv",
    }
    assert adapter.effective_config("recursive-tsv", {"concurrency": 8})["concurrency"] == 8
    request = CommandRequest("recursive-tsv", BUCKET, REGION, tool="swath", visible_memory_gb=2.0)
    assert adapter.build_env(request) == {
        "JAVA_TOOL_OPTIONS": f"-XX:MaxRAMPercentage={HEAP_PERCENT}"
    }


def test_s7cmd_renders_fixture_specific_discovery_and_retry_controls() -> None:
    adapter = load_command_adapter(adapter_path("s7cmd"))

    argv = adapter.compile(
        CommandRequest(
            "recursive-one-nosort",
            BUCKET,
            REGION,
            tool="s7cmd",
            config={"aws_max_attempts": 1, "concurrency": 64, "parallel_depth": 1},
        )
    )

    assert argv[argv.index("--max-parallel-listing-max-depth") + 1] == "1"
    assert argv[argv.index("--aws-max-attempts") + 1] == "1"


def test_swath_renders_retained_output_controls_and_refuses_the_wrong_mode() -> None:
    adapter = load_command_adapter(adapter_path("swath"))
    request = CommandRequest(
        "recursive-tsv-dataset",
        BUCKET,
        REGION,
        tool="swath",
        sink_dir=SINK,
        config={
            "concurrency": 16,
            "jvm_max_heap": "4g",
            "text_writers": 3,
            "text_part_size": "1gb",
            "writeback_size": "32mb",
            "part_rotation_interval": "0",
            "part_rotation_max_rows": 0,
        },
    )
    argv = adapter.compile(request)
    assert argv[-12:] == (
        "--text-writers",
        "3",
        "--compression",
        "none",
        "--text-part-size",
        "1gb",
        "--writeback-size",
        "32mb",
        "--part-rotation-interval",
        "0",
        "--part-rotation-max-rows",
        "0",
    )
    assert adapter.build_env(request) == {
        "JAVA_TOOL_OPTIONS": f"-XX:MaxRAMPercentage={HEAP_PERCENT} -Xmx4g"
    }

    # Heap is an environment control, not a formatter flag. Every Swath mode
    # consumes it even when that mode's argv-specific allowlist is empty.
    for mode in ("recursive-tsv", "recursive-parquet", "recursive-parquet-sorted"):
        heap_only = CommandRequest(
            mode,
            BUCKET,
            REGION,
            tool="swath",
            sink_dir=SINK,
            config={"jvm_max_heap": "4g"},
        )
        adapter.compile(heap_only)
        assert adapter.build_env(heap_only) == {
            "JAVA_TOOL_OPTIONS": f"-XX:MaxRAMPercentage={HEAP_PERCENT} -Xmx4g"
        }

    with pytest.raises(CommandAdapterError, match=r"does not use config key.*text_writers"):
        adapter.compile(
            CommandRequest(
                "recursive-parquet",
                BUCKET,
                REGION,
                tool="swath",
                sink_dir=SINK,
                config={"text_writers": 3},
            )
        )


def test_one_rule_decides_what_a_producing_mode_inherits() -> None:
    """A chain link and an inline setup exec inherit by the same rule.

    The resolver expands the one and the worker runs the other, and two copies
    of this filter are two chances to disagree about what a sweep varied.
    """
    consumer = {"mode": "hinted", "concurrency": 8, "segments": 16, "heap_percent": HEAP_PERCENT}
    settable = Mode(
        product="text",
        fields=("key",),
        axes={
            "concurrency": Ceiling(64, "unverified"),
            "segments": Stated(),
            # The harness's own share, which no row and no producer may restate.
            "heap_percent": Fixed(HEAP_PERCENT),
        },
        artifacts=PRODUCT,
        product_artifact=LISTING,
    )
    assert shared_axis_values(settable, consumer) == {"concurrency": 8, "segments": 16}
    # A knob that does nothing here would split shared preparations over nothing.
    inert = Mode(
        product="text",
        fields=("key",),
        axes={"concurrency": Inert()},
        artifacts=PRODUCT,
        product_artifact=LISTING,
    )
    assert shared_axis_values(inert, consumer) == {}
    # An axis the producer never declares is not its business at all.
    assert (
        shared_axis_values(
            Mode(product="text", fields=("key",), artifacts=PRODUCT, product_artifact=LISTING),
            consumer,
        )
        == {}
    )


def test_commands_compile_exact_subject_argv() -> None:
    aws = load_command_adapter(ROOT / "tools/aws-cli/adapter/command.py")
    argv = aws.compile(CommandRequest("s3api-v2-json", "bucket", "us-east-1", tool="aws-cli"))
    assert argv[:2] == ("/usr/local/bin/aws", "s3api")
    assert "--no-sign-request" in argv

    swath = load_command_adapter(ROOT / "tools/swath/adapter/command.py")
    argv = swath.compile(CommandRequest("recursive-tsv", "bucket", "us-east-1", tool="swath"))
    assert argv[:3] == ("/opt/java/openjdk/bin/java", "-jar", "/opt/swath/swath.jar")


def test_s5cmd_fanout_execs_with_an_inherited_commands_file(tmp_path: Path) -> None:
    """Exercise the real Linux memfd/exec boundary without making an S3 request."""
    fake = tmp_path / "fake-s5cmd"
    fake.write_text(
        "#!/usr/bin/python3\n"
        "import json, sys\n"
        "with open(sys.argv[-1], encoding='utf-8') as source:\n"
        "    print(json.dumps({'argv': sys.argv[1:-1], 'commands': source.read()}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    wrapper = ROOT / "tools/s5cmd/adapter/fanout.py"
    done = subprocess.run(
        [
            "/usr/bin/python3",
            str(wrapper),
            "--s5cmd",
            str(fake),
            "--bucket",
            BUCKET,
            "--prefix",
            "p x/雪/",
            "--shard",
            ".",
            "--shard",
            "0",
            "--numworkers",
            "8",
            "--endpoint-url",
            ENDPOINT,
            "--unsigned",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    observed = json.loads(done.stdout)
    assert observed["argv"] == [
        "--endpoint-url",
        ENDPOINT,
        "--no-sign-request",
        "--numworkers",
        "8",
        "run",
    ]
    assert observed["commands"].splitlines() == [
        "ls -e -s 's3://bucket-x/p x/雪/.*'",
        "ls -e -s 's3://bucket-x/p x/雪/0*'",
    ]


def test_s5cmd_fanout_reads_fixture_shards(tmp_path: Path) -> None:
    fake = tmp_path / "fake-s5cmd"
    fake.write_text(
        "#!/usr/bin/python3\n"
        "import json, sys\n"
        "with open(sys.argv[-1], encoding='utf-8') as source:\n"
        "    print(json.dumps({'commands': source.read()}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    shards = tmp_path / "s5cmd-shards.input"
    shards.write_text("blend.20200101\nindex.html\n", encoding="utf-8")
    wrapper = ROOT / "tools/s5cmd/adapter/fanout.py"
    done = subprocess.run(
        [
            "/usr/bin/python3",
            str(wrapper),
            "--s5cmd",
            str(fake),
            "--bucket",
            BUCKET,
            "--shard-file",
            str(shards),
            "--numworkers",
            "64",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["commands"].splitlines() == [
        "ls -e -s 's3://bucket-x/blend.20200101*'",
        "ls -e -s 's3://bucket-x/index.html*'",
    ]


def _run_fanout_shard_file(tmp_path: Path, contents: bytes) -> subprocess.CompletedProcess[str]:
    shards = tmp_path / "s5cmd-shards.input"
    shards.write_bytes(contents)
    wrapper = ROOT / "tools/s5cmd/adapter/fanout.py"
    return subprocess.run(
        [
            "/usr/bin/python3",
            str(wrapper),
            "--s5cmd",
            "/bin/true",
            "--bucket",
            BUCKET,
            "--shard-file",
            str(shards),
            "--numworkers",
            "64",
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def test_s5cmd_fanout_shard_file_refuses_duplicate_shards(tmp_path: Path) -> None:
    done = _run_fanout_shard_file(tmp_path, b"blend.2020\nindex.html\nblend.2020\n")
    assert done.returncode != 0
    assert "repeat" in done.stderr


def test_s5cmd_fanout_shard_file_tolerates_crlf_line_endings(tmp_path: Path) -> None:
    # Text-mode universal-newline reading normalizes CRLF to '\n' before
    # rstrip("\n") runs, so a CRLF-authored shard file compiles to clean
    # prefixes rather than a glob with a trailing '\r' that would silently
    # match nothing; SAFE_SHARD is the backstop for a '\r' that survives
    # regardless (e.g. a future change to the read mode).
    shards = tmp_path / "s5cmd-shards.input"
    shards.write_bytes(b"blend.2020\r\nindex.html\r\n")
    fake = tmp_path / "fake-s5cmd"
    fake.write_text(
        "#!/usr/bin/python3\n"
        "import json, sys\n"
        "with open(sys.argv[-1], encoding='utf-8') as source:\n"
        "    print(json.dumps({'commands': source.read()}))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    wrapper = ROOT / "tools/s5cmd/adapter/fanout.py"
    done = subprocess.run(
        [
            "/usr/bin/python3",
            str(wrapper),
            "--s5cmd",
            str(fake),
            "--bucket",
            BUCKET,
            "--shard-file",
            str(shards),
            "--numworkers",
            "64",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["commands"].splitlines() == [
        "ls -e -s 's3://bucket-x/blend.2020*'",
        "ls -e -s 's3://bucket-x/index.html*'",
    ]


def test_s5cmd_fanout_shard_file_refuses_unsafe_characters(tmp_path: Path) -> None:
    done = _run_fanout_shard_file(tmp_path, b"blend.2020\nin*dex\n")
    assert done.returncode != 0
    assert "safe prefixes" in done.stderr


@pytest.mark.parametrize("shards", ["00", "a,a", "a*", "", "雪"])
def test_s5cmd_fanout_refuses_ambiguous_shards(shards: str) -> None:
    adapter = load_command_adapter(adapter_path("s5cmd"))
    with pytest.raises(CommandAdapterError, match="shard_initials"):
        adapter.compile(
            CommandRequest(
                "fanout-with-dirs",
                BUCKET,
                REGION,
                tool="s5cmd",
                config={"shard_initials": shards},
            )
        )


@pytest.mark.parametrize("tool", TOOLS)
def test_capsule_metadata_has_exact_current_contract_and_no_legacy_parent_identity(
    tool: str,
) -> None:
    document = json.loads((ROOT / "tools" / tool / "build/image.json").read_text())
    assert set(document) == {
        "tool",
        "tool_version",
        "tool_build_sha256",
        "tool_artifact",
        "subject_workdir",
        "executable",
        "command",
        "normalizer",
        "adapter_bundle_sha256",
    }
    assert set(document["tool_artifact"]) == {"kind", "locator", "sha256"}
    assert document["tool"] == tool
    assert "shared_base_source_sha256" not in document
    assert "tool_parent_image" not in document


@pytest.mark.parametrize("slug", ["", "../aws-cli", "AWS-CLI", "aws_cli", "aws--cli"])
def test_registered_selection_rejects_invalid_slugs(slug: str) -> None:
    with pytest.raises(BuildSelectionError, match="invalid tool slug"):
        load_registered_selection(ROOT, slug)
