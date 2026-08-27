"""Run a plan serially through the benchmark toolbox on the current Docker host.

This is a second executor for the existing plan, identity, ledger, worker,
report, and verifier contracts. It is intentionally not a shell loop around
tool binaries: every subject still enters through ``measure.py`` in the
attested toolbox image, and every completed attempt is retained locally with
``result.json`` published last.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark import campaign, identity, measure
from benchmark import plan as bench
from benchmark.contract import CREDENTIAL_ENV_VAR, TOOL_IMAGE_FIELDS, TOOLBOX_TOOLS
from benchmark.ledger import (
    Attempt,
    CampaignError,
    attempt_rows,
    journal_intent,
    mint_group_id,
    open_ledger,
    set_state,
    validate_suite,
)
from benchmark.verify import (
    expected_result_binding,
    identity_errors,
    result_binding_errors,
    result_semantic_errors,
)

EXECUTOR = "local-docker"
OUTPUT_TARGET = "file"
TARGET_PREFIX = ""
WORKER_UID = 10001
WORKER_GID = 10001
DEADLINE_SLACK_S = 600


@dataclass(frozen=True)
class LocalImage:
    image_set: campaign.ImageSet
    run_reference: str
    image_id: str


@dataclass(frozen=True)
class Host:
    allowed_cpus: tuple[int, ...]
    physical_cores: tuple[tuple[int, ...], ...]
    memory_gb: int
    machine_family: str
    document: Mapping[str, object]

    def cpuset(self, logical_cpus: int) -> str:
        selected: list[int] = []
        for siblings in self.physical_cores:
            if len(selected) + len(siblings) > logical_cpus:
                break
            selected.extend(siblings)
            if len(selected) == logical_cpus:
                return ",".join(str(cpu) for cpu in selected)
        raise CampaignError(
            f"{logical_cpus} vCPUs cannot be expressed as whole physical cores on this host; "
            f"available sibling groups are {[list(group) for group in self.physical_cores]}"
        )

    def instances(self) -> dict[tuple[int, int], str]:
        return {
            (vcpus, memory_gb): f"{self.machine_family}-{vcpus}vcpu-{memory_gb}gb"
            for vcpus in range(1, len(self.allowed_cpus) + 1)
            for memory_gb in range(1, self.memory_gb + 1)
        }


@dataclass(frozen=True)
class Scheduled:
    index: int
    block: int
    case: bench.Case


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _command(argv: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(argv), check=check, text=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise CampaignError(f"command failed: {' '.join(argv)}: {detail}") from None


def inspect_host() -> Host:
    """Resolve the CPUs Docker may use into whole physical-core sibling groups."""
    try:
        allowed = tuple(sorted(os.sched_getaffinity(0)))
    except AttributeError as exc:
        raise CampaignError("local Docker campaigns require Linux CPU affinity support") from exc
    cores: dict[tuple[int, int], list[int]] = {}
    for cpu in allowed:
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        try:
            package = int((topology / "physical_package_id").read_text().strip())
            core = int((topology / "core_id").read_text().strip())
        except (OSError, ValueError) as exc:
            raise CampaignError(f"cannot read physical topology for CPU {cpu}: {exc}") from None
        cores.setdefault((package, core), []).append(cpu)
    physical = tuple(tuple(sorted(cpus)) for _key, cpus in sorted(cores.items()))
    if not allowed or not physical:
        raise CampaignError("the local CPU allocation is empty")
    memory_bytes = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    memory_gb = memory_bytes // (1024**3)
    cpu_model = "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu_model = line.partition(":")[2].strip()
                break
    except OSError:
        pass
    base = {
        "architecture": platform.machine(),
        "cpu_model": cpu_model,
        "allowed_cpus": list(allowed),
        "physical_cores": [list(group) for group in physical],
        "memory_bytes": memory_bytes,
        "operating_system": platform.system(),
    }
    signature = hashlib.sha256(_canonical(base).encode()).hexdigest()[:12]
    return Host(
        allowed_cpus=allowed,
        physical_cores=physical,
        memory_gb=memory_gb,
        machine_family=f"local-{platform.machine().lower()}-{signature}",
        document=base,
    )


def load_local_image(reference: str, required_tools: set[str]) -> LocalImage:
    """Read immutable metadata from the exact local Docker image ID."""
    inspected = _command(("docker", "image", "inspect", reference))
    try:
        documents = json.loads(inspected.stdout)
        document = documents[0]
        image_id = document["Id"]
        config = document["Config"]
        if (
            len(documents) != 1
            or not isinstance(image_id, str)
            or not image_id.startswith("sha256:")
            or len(image_id) != 71
            or document.get("Architecture") != "amd64"
            or document.get("Os") != "linux"
            or not isinstance(config, dict)
            or config.get("User") != f"{WORKER_UID}:{WORKER_GID}"
        ):
            raise ValueError
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise CampaignError("docker returned an unusable linux/amd64 image identity") from None
    metadata_raw = _command(
        (
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--entrypoint=/bin/cat",
            image_id,
            "/opt/benchmark/image-metadata.json",
        )
    ).stdout
    try:
        metadata = json.loads(metadata_raw)
        if not isinstance(metadata, dict):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        raise CampaignError("the local image has malformed immutable metadata") from None
    image_uri = f"local-docker@{image_id}"
    image_set_document = {**metadata, "image_uri": image_uri}
    tools = metadata.get("tools")
    if not isinstance(tools, dict):
        raise CampaignError("the local image metadata has no tool roster")
    image_set_document["tools"] = {
        tool: {name: facts[name] for name in TOOL_IMAGE_FIELDS}
        for tool, facts in tools.items()
        if isinstance(tool, str) and isinstance(facts, dict)
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json") as scratch:
        json.dump(image_set_document, scratch)
        scratch.flush()
        loaded = campaign.load_image_set(scratch.name, required_tools)
    return LocalImage(loaded, image_id, image_id)


def load_local_plan(path: Path, host: Host, *, allow_s4cmd: bool) -> bench.Plan:
    exclusions = None
    if allow_s4cmd:
        exclusions = tuple(
            exclusion
            for exclusion in bench.load_default_exclusions(bench.bench_dir() / "tools.yaml")
            if exclusion.tool != "s4cmd"
        )
    loaded = bench.Plan.load(path, default_exclusions=exclusions, instances=host.instances())
    bench.check_roster(loaded, TOOLBOX_TOOLS)
    if loaded.replay is not None:
        raise CampaignError(
            "local replay orchestration is not implemented yet; this executor currently "
            "accepts only real-S3 plans"
        )
    s4cmd = loaded.cases_for("s4cmd")
    if allow_s4cmd:
        if len(s4cmd) != 1 or s4cmd[0].reps != 1 or s4cmd[0].purpose != "canary":
            raise CampaignError("the retired s4cmd exception is exactly one real-S3 canary attempt")
    elif s4cmd:
        raise CampaignError("s4cmd requires --allow-retired-s4cmd-s3-canary")
    expanded = campaign.expand_launch(loaded.cases, loaded.adapters)
    if len(expanded) != sum(case.reps for case in loaded.cases) or any(
        step.waits_for is not None for step in expanded
    ):
        raise CampaignError(
            "local campaigns do not yet execute capsule prerequisite chains; use modes "
            "whose attempts consume no prepared artifact"
        )
    for case in loaded.cases:
        if case.resources.vcpus > len(host.allowed_cpus):
            raise CampaignError(
                f"{case.tool} asks for {case.resources.vcpus} vCPUs; host exposes "
                f"{len(host.allowed_cpus)}"
            )
        if case.resources.memory_gb > host.memory_gb:
            raise CampaignError(
                f"{case.tool} asks for {case.resources.memory_gb} GiB; host has "
                f"{host.memory_gb} GiB"
            )
        host.cpuset(case.resources.vcpus)
    return loaded


def randomized_blocks(cases: Sequence[bench.Case], seed: int) -> tuple[Scheduled, ...]:
    """One independently shuffled complete block per declared repetition."""
    reps = {case.reps for case in cases}
    if len(reps) != 1:
        raise CampaignError(
            "a randomized complete-block campaign requires the same reps value on every row"
        )
    randomizer = random.Random(seed)
    scheduled: list[Scheduled] = []
    for block in range(1, next(iter(reps), 0) + 1):
        cells = list(cases)
        randomizer.shuffle(cells)
        first_index = len(scheduled) + 1
        scheduled.extend(
            Scheduled(index=first_index + offset, block=block, case=case)
            for offset, case in enumerate(cells)
        )
    return tuple(scheduled)


def _attempt_identity(
    case: bench.Case,
    *,
    location: str,
    target_bucket: str,
    target_region: str,
    image: Mapping[str, str],
) -> tuple[str, str]:
    return campaign.case_identity(
        case,
        executor=EXECUTOR,
        auth_role=case.auth_role,
        target_bucket=target_bucket,
        target_region=target_region,
        location=location,
        tool_slice=image["tool_slice_sha256"],
        platform=image["platform_sha256"],
    )


def _docker_request(
    attempt: Attempt,
    image: Mapping[str, str],
    *,
    image_id: str,
    results_root: Path,
    scratch: Path,
    cpuset: str,
    term_grace: float,
    schedule_index: int,
    block: int,
) -> dict[str, object]:
    pairs = campaign.worker_argument_pairs(
        attempt,
        image,
        output=str(scratch),
        destination=attempt.result_prefix,
        term_grace=term_grace,
    )
    worker = [item for pair in pairs for item in pair]
    docker = [
        "docker",
        "run",
        "--rm",
        f"--name={attempt.job_name}",
        "--network=host",
        f"--cpuset-cpus={cpuset}",
        f"--volume={results_root}:{results_root}",
    ]
    docker.extend(case_option for case_option in _memory_options(attempt.container_memory_gb))
    if attempt.auth_role is not None:
        docker.append(f"--env={CREDENTIAL_ENV_VAR}")
    docker.extend((image_id, *worker))
    return {
        "schema_version": 1,
        "executor": EXECUTOR,
        "schedule_index": schedule_index,
        "block": block,
        "image_id": image_id,
        "cpuset": cpuset,
        "docker_argv": docker,
    }


def _memory_options(memory_gb: int | None) -> tuple[str, ...]:
    if memory_gb is None:
        return ()
    return (f"--memory={memory_gb}g", f"--memory-swap={memory_gb}g")


def _build_attempt(
    ordinal: int,
    item: Scheduled,
    *,
    suite: str,
    group_id: str,
    location: str,
    loaded: bench.Plan,
    local_image: LocalImage,
    results_root: Path,
    host: Host,
    term_grace: float,
) -> tuple[Attempt, str]:
    case = item.case
    image = local_image.image_set.image_for(case.tool)
    case_id, case_inputs = _attempt_identity(
        case,
        location=location,
        target_bucket=loaded.bucket,
        target_region=loaded.region,
        image=image,
    )
    attempt_id = identity.attempt_id(case_id, ordinal)
    result_prefix = results_root / suite / loaded.bucket / attempt_id
    scratch = results_root / ".work" / group_id / attempt_id
    cpuset = host.cpuset(case.resources.vcpus)
    executor_env = _canonical(
        {
            **host.document,
            "cpuset": cpuset,
            "container_network": "host",
            "docker_image_id": local_image.image_id,
            "location": location,
        }
    )
    attempt = Attempt(
        case_id=case_id,
        attempt=ordinal,
        case_inputs=case_inputs,
        group_id=group_id,
        tool=case.tool,
        auth_role=case.auth_role,
        executor=EXECUTOR,
        location=location,
        machine_type=case.resources.machine_type,
        vcpus=case.resources.vcpus,
        memory_gb=case.resources.memory_gb,
        container_memory_gb=case.resources.container_memory_gb,
        heap_percent=case.heap_percent,
        timeout_s=case.timeout_s,
        target_bucket=loaded.bucket,
        target_region=loaded.region,
        target_prefix=TARGET_PREFIX,
        config=_canonical(dict(case.config)),
        input_artifact_sha256=None,
        produced_by=None,
        tool_slice_sha256=image["tool_slice_sha256"],
        platform_sha256=image["platform_sha256"],
        image_uri=image["image_uri"],
        image_set_sha256=local_image.image_set.sha256,
        executor_env=executor_env,
        service_account="local-environment" if case.auth_role is not None else "anonymous",
        secret_resource=None,
        job_name=campaign.job_name_for(suite, case_id, ordinal),
        result_prefix=str(result_prefix),
        purpose=case.purpose,
        statistic=case.statistic,
        origin="planned",
        replay=None,
    )
    request = _docker_request(
        attempt,
        image,
        image_id=local_image.image_id,
        results_root=results_root,
        scratch=scratch,
        cpuset=cpuset,
        term_grace=term_grace,
        schedule_index=item.index,
        block=item.block,
    )
    return attempt, _canonical(request)


def _write_create_only(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{os.getpid()}.pending")
    pending.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    try:
        os.link(pending, path)
    except FileExistsError:
        raise CampaignError(f"refusing to replace existing campaign record {path}") from None
    finally:
        pending.unlink(missing_ok=True)


def _prepare_empty(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)


def _chown(
    image_id: str,
    results_root: Path,
    owner: tuple[int, int],
    paths: Sequence[Path],
    *,
    recursive: bool,
) -> None:
    """Hand exact in-root paths between the controller and in-image worker."""
    resolved_root = results_root.resolve()
    resolved: list[Path] = []
    for path in paths:
        target = path.resolve()
        if target != resolved_root and resolved_root not in target.parents:
            raise CampaignError(f"refusing ownership change outside results root: {target}")
        resolved.append(target)
    argv = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--user=0:0",
        "--entrypoint=/bin/chown",
        f"--volume={resolved_root}:{resolved_root}",
        image_id,
    ]
    if recursive:
        argv.append("-R")
    argv.extend((f"{owner[0]}:{owner[1]}", *(str(path) for path in resolved)))
    _command(argv)


def _request(row: Any) -> dict[str, Any]:
    try:
        document = json.loads(row["request_json"])
        if (
            not isinstance(document, dict)
            or document.get("executor") != EXECUTOR
            or not isinstance(document.get("docker_argv"), list)
            or not all(isinstance(token, str) for token in document["docker_argv"])
        ):
            raise ValueError
        return document
    except (TypeError, ValueError, json.JSONDecodeError):
        raise CampaignError(f"{row['attempt_id']}: recorded local request is malformed") from None


def _flag_value(argv: Sequence[str], name: str) -> str:
    positions = [index for index, token in enumerate(argv) if token == name]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise CampaignError(f"recorded Docker request does not carry exactly one {name}")
    return argv[positions[0] + 1]


def _evidence_errors(row: Any) -> list[str]:
    marker = Path(row["result_prefix"]) / "result.json"
    try:
        result = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"result marker is unavailable: {exc}"]
    if not isinstance(result, dict):
        return ["result marker is not an object"]
    return [
        *identity_errors(
            result,
            attempt_id=str(row["attempt_id"]),
            case_id=str(row["case_id"]),
            result_prefix=str(row["result_prefix"]),
        ),
        *result_binding_errors(expected_result_binding(row), result, purpose=str(row["purpose"])),
        *result_semantic_errors(result, purpose=str(row["purpose"])),
    ]


def _run_attempt(
    con: Any,
    row: Any,
    *,
    results_root: Path,
    keep_scratch: bool,
) -> bool:
    request = _request(row)
    argv = request["docker_argv"]
    scratch = Path(_flag_value(argv, "--output"))
    destination = Path(row["result_prefix"])
    log_dir = results_root / "logs" / row["group_id"]
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{row['attempt_id']}.stdout.log"
    stderr_path = log_dir / f"{row['attempt_id']}.stderr.log"
    print(
        f"local-campaign: [{request['schedule_index']}] block={request['block']} "
        f"{row['tool']} {row['mode']} cpus={request['cpuset']}"
    )
    try:
        if destination.exists():
            raise CampaignError(f"{row['attempt_id']}: result destination already exists")
        _prepare_empty(scratch)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image_id = str(request["image_id"])
        _chown(
            image_id,
            results_root,
            (WORKER_UID, WORKER_GID),
            (scratch, destination.parent),
            recursive=False,
        )
        set_state(con, row["attempt_id"], "RUNNING", None)
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            process = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
            try:
                exit_code = process.wait(
                    timeout=(
                        int(row["timeout_s"])
                        + int(float(_flag_value(argv, "--term-grace")))
                        + DEADLINE_SLACK_S
                    )
                )
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ("docker", "stop", "--time=0", row["job_name"]),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                process.wait()
                exit_code = 124
            except KeyboardInterrupt:
                subprocess.run(
                    ("docker", "stop", "--time=0", row["job_name"]),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                set_state(con, row["attempt_id"], "FAILED", "local controller interrupted")
                raise
        owned = [scratch]
        if destination.exists():
            owned.append(destination)
        _chown(
            image_id,
            results_root,
            (os.getuid(), os.getgid()),
            owned,
            recursive=True,
        )
        errors = _evidence_errors(row)
        if exit_code != 0:
            errors.insert(0, f"Docker/worker exited {exit_code}")
        success = not errors
        set_state(
            con,
            row["attempt_id"],
            "SUCCEEDED" if success else "FAILED",
            None if success else "; ".join(errors)[:2000],
        )
        if success and not keep_scratch:
            expected_parent = (results_root / ".work" / row["group_id"]).resolve()
            if scratch.resolve().parent != expected_parent:
                raise CampaignError(f"refusing to remove unexpected scratch path {scratch}")
            shutil.rmtree(scratch)
    except (CampaignError, OSError, subprocess.SubprocessError) as exc:
        set_state(con, row["attempt_id"], "FAILED", f"local executor failed: {exc}"[:2000])
        print(f"local-campaign: {row['attempt_id']} FAILED: {exc}", file=sys.stderr)
        return False
    print(f"local-campaign: {row['attempt_id']} {'SUCCEEDED' if success else 'FAILED'}")
    return success


def _manifest(
    *,
    loaded: bench.Plan,
    local_image: LocalImage,
    host: Host,
    suite: str,
    group_id: str,
    location: str,
    seed: int,
    rows: Sequence[Any],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "executor": EXECUTOR,
        "suite": suite,
        "group_id": group_id,
        "plan": str(loaded.path.resolve()),
        "plan_sha256": loaded.digest,
        "target_bucket": loaded.bucket,
        "target_region": loaded.region,
        "location": location,
        "seed": seed,
        "image_id": local_image.image_id,
        "image_uri": local_image.image_set.image_uri,
        "image_set_sha256": local_image.image_set.sha256,
        "host": host.document,
        "schedule": [
            {
                "index": _request(row)["schedule_index"],
                "block": _request(row)["block"],
                "attempt_id": row["attempt_id"],
                "tool": row["tool"],
                "mode": row["mode"],
                "case_id": row["case_id"],
                "result_prefix": row["result_prefix"],
                "request_sha256": hashlib.sha256(row["request_json"].encode()).hexdigest(),
            }
            for row in rows
        ],
    }


def _resolve(
    args: argparse.Namespace,
) -> tuple[Host, bench.Plan, LocalImage, tuple[Scheduled, ...]]:
    host = inspect_host()
    loaded = load_local_plan(Path(args.plan), host, allow_s4cmd=args.allow_retired_s4cmd_s3_canary)
    local_image = load_local_image(args.image, set(loaded.tools()))
    return host, loaded, local_image, randomized_blocks(loaded.cases, args.seed)


def cmd_run(args: argparse.Namespace) -> int:
    suite = validate_suite(args.suite)
    host, loaded, local_image, schedule = _resolve(args)
    if any(case.resources.container_memory_gb is None for case in loaded.cases):
        raise CampaignError("every local case must declare container_memory_gb")
    if any(case.auth_role is not None for case in loaded.cases):
        blob = os.environ.get(CREDENTIAL_ENV_VAR)
        if not args.dry_run and blob is None:
            raise CampaignError(
                f"a signed case requires {CREDENTIAL_ENV_VAR} in the local environment"
            )
        if blob is not None:
            measure.parse_credential_env(blob)
    results_root = Path(args.results_root).resolve()
    if args.dry_run:
        counters: dict[str, int] = {}
        rendered: list[dict[str, object]] = []
        for item in schedule:
            image = local_image.image_set.image_for(item.case.tool)
            case_id, _inputs = _attempt_identity(
                item.case,
                location=args.location,
                target_bucket=loaded.bucket,
                target_region=loaded.region,
                image=image,
            )
            ordinal = counters.get(case_id, 0) + 1
            counters[case_id] = ordinal
            attempt, request = _build_attempt(
                ordinal,
                item,
                suite=suite,
                group_id=args.group or "dry-run",
                location=args.location,
                loaded=loaded,
                local_image=local_image,
                results_root=results_root,
                host=host,
                term_grace=args.term_grace,
            )
            rendered.append(
                {
                    "index": item.index,
                    "block": item.block,
                    "attempt_id": attempt.attempt_id,
                    "tool": attempt.tool,
                    "mode": item.case.mode,
                    "request": json.loads(request),
                }
            )
        print(
            json.dumps(
                {"plan_sha256": loaded.digest, "seed": args.seed, "schedule": rendered},
                indent=2,
            )
        )
        return 0
    results_root.mkdir(parents=True, exist_ok=True)
    state = Path(args.state).resolve() if args.state else results_root / "campaign.db"
    con = open_ledger(str(state), suite=suite)
    try:
        group_id = mint_group_id(con, args.group)
        seen: set[str] = set()
        for item in schedule:
            image = local_image.image_set.image_for(item.case.tool)
            case_id, case_inputs = _attempt_identity(
                item.case,
                location=args.location,
                target_bucket=loaded.bucket,
                target_region=loaded.region,
                image=image,
            )

            def build(ordinal: int, item: Scheduled = item) -> tuple[Attempt, str]:
                return _build_attempt(
                    ordinal,
                    item,
                    suite=suite,
                    group_id=group_id,
                    location=args.location,
                    loaded=loaded,
                    local_image=local_image,
                    results_root=results_root,
                    host=host,
                    term_grace=args.term_grace,
                )

            attempt, _request_json = journal_intent(
                con,
                case_id=case_id,
                case_inputs=case_inputs,
                build=build,
                repeat=case_id in seen or args.repeat_existing,
            )
            seen.add(case_id)
            set_state(con, attempt.attempt_id, "SUBMITTED", "queued by local randomized schedule")
        rows = sorted(
            attempt_rows(con, group_id=group_id),
            key=lambda row: int(_request(row)["schedule_index"]),
        )
        _write_create_only(
            results_root / "schedules" / f"{group_id}.json",
            _manifest(
                loaded=loaded,
                local_image=local_image,
                host=host,
                suite=suite,
                group_id=group_id,
                location=args.location,
                seed=args.seed,
                rows=rows,
            ),
        )
        blocks = {int(_request(row)["block"]) for row in rows}
        print(
            f"local-campaign: froze {len(rows)} attempts in {len(blocks)} "
            f"block(s) as group {group_id}"
        )
        succeeded = 0
        for row in rows:
            succeeded += _run_attempt(
                con, row, results_root=results_root, keep_scratch=args.keep_scratch
            )
        print(f"local-campaign: group {group_id}: {succeeded}/{len(rows)} succeeded")
        return 0 if succeeded == len(rows) else 1
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="freeze and execute one randomized local campaign")
    run.add_argument("--plan", required=True)
    run.add_argument("--image", required=True, help="local toolbox tag or immutable image ID")
    run.add_argument("--results-root", required=True)
    run.add_argument("--state", help="campaign.db path; defaults below results-root")
    run.add_argument("--suite", required=True)
    run.add_argument("--group")
    run.add_argument("--location", required=True, help="physical location of this Docker host")
    run.add_argument("--seed", required=True, type=int)
    run.add_argument("--term-grace", type=float, default=5.0)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--keep-scratch", action="store_true")
    run.add_argument(
        "--repeat-existing",
        action="store_true",
        help="allow a new group to repeat cases already successful in this ledger",
    )
    run.add_argument(
        "--allow-retired-s4cmd-s3-canary",
        action="store_true",
        help="override the shared s4cmd exclusion for exactly one real-S3 canary attempt",
    )
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except (CampaignError, bench.PlanError, OSError, ValueError) as exc:
        print(f"local-campaign: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
