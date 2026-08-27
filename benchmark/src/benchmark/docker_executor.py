"""Docker executor for :mod:`benchmark.campaign`.

The campaign owns plans, identities, the ledger, and evidence.  This module only
turns those attempts into serial ``docker run`` calls on the current host.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark import campaign, identity, measure
from benchmark import plan as bench
from benchmark.contract import CREDENTIAL_ENV_VAR, TOOL_IMAGE_FIELDS, canonical_json
from benchmark.ledger import (
    STATE_FILENAME,
    TERMINAL_STATES,
    Attempt,
    CampaignError,
    attempt_rows,
    journal_intent,
    mint_group_id,
    open_ledger,
    set_state,
    validate_suite,
)

EXECUTOR = "docker"
WORKER = (10001, 10001)
INTERRUPTED_ROW_STATE = "FAILED"
UNSTARTED_ROW_STATE = "NOT_CREATED"
LOCAL_INTERRUPTION_POLICY = {
    "interrupted_row_state": INTERRUPTED_ROW_STATE,
    "unstarted_row_state": UNSTARTED_ROW_STATE,
    "replace_within_group": False,
    "replacement": "new group with a new seed",
}


@dataclass(frozen=True)
class DockerImage:
    image_set: campaign.ImageSet
    image_id: str


@dataclass(frozen=True)
class Host:
    cores: tuple[tuple[int, ...], ...]
    memory_gb: int
    family: str
    facts: Mapping[str, object]

    def cpuset(self, vcpus: int) -> str:
        selected: list[int] = []
        for core in reversed(self.cores):
            if len(selected) + len(core) > vcpus:
                continue
            selected.extend(core)
            if len(selected) == vcpus:
                return ",".join(str(cpu) for cpu in sorted(selected))
        raise CampaignError(
            f"{vcpus} vCPUs cannot be allocated as whole physical cores on this host"
        )

    def instances(self) -> dict[tuple[int, int], str]:
        logical = sum(len(core) for core in self.cores)
        allocatable: list[int] = []
        for vcpus in range(1, logical + 1):
            try:
                self.cpuset(vcpus)
            except CampaignError:
                continue
            allocatable.append(vcpus)
        return {
            (vcpus, memory): f"{self.family}-{vcpus}vcpu-{memory}gb"
            for vcpus in allocatable
            for memory in range(1, self.memory_gb + 1)
        }


@dataclass(frozen=True)
class Scheduled:
    index: int
    block: int
    case: bench.Case


def _command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, check=True, text=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise CampaignError(f"command failed: {' '.join(argv)}: {detail}") from None


def inspect_host(results: Path) -> Host:
    allowed = sorted(os.sched_getaffinity(0))
    by_core: dict[tuple[int, int], list[int]] = {}
    for cpu in allowed:
        root = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        try:
            key = (
                int((root / "physical_package_id").read_text()),
                int((root / "core_id").read_text()),
            )
        except (OSError, ValueError) as exc:
            raise CampaignError(f"cannot read CPU topology for logical CPU {cpu}: {exc}") from None
        by_core.setdefault(key, []).append(cpu)
    memory_bytes = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    memory_gb = memory_bytes // (1024**3)
    cpu_model = next(
        (
            line.partition(":")[2].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        "",
    )
    if not cpu_model:
        raise CampaignError("cannot identify the host CPU model")
    mount_target = results.resolve()
    while not mount_target.exists() and mount_target != mount_target.parent:
        mount_target = mount_target.parent
    filesystem = _command(
        ("findmnt", "--noheadings", "--output", "SOURCE,FSTYPE", "--target", str(mount_target))
    ).stdout.split()
    if len(filesystem) != 2:
        raise CampaignError("cannot identify the results filesystem")
    facts = {
        "architecture": platform.machine(),
        "allowed_cpus": allowed,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "cpu_model": cpu_model,
        "docker_server_version": _command(
            ("docker", "version", "--format", "{{.Server.Version}}")
        ).stdout.strip(),
        "docker_storage_driver": _command(
            ("docker", "info", "--format", "{{.Driver}}")
        ).stdout.strip(),
        "filesystem_source": filesystem[0],
        "filesystem_type": filesystem[1],
        "kernel": platform.release(),
        "physical_cores": [sorted(cpus) for _key, cpus in sorted(by_core.items())],
        "python_version": platform.python_version(),
        "memory_bytes": memory_bytes,
    }
    # This label identifies the local hardware treatment. Keep the fuller,
    # transient environment (boot, kernel, Docker, filesystem source) in the
    # attempt evidence without making a reboot mint a different machine type.
    hardware = {
        key: facts[key]
        for key in ("architecture", "cpu_model", "filesystem_type", "physical_cores")
    }
    hardware["memory_gb"] = memory_gb
    signature = hashlib.sha256(canonical_json(hardware).encode()).hexdigest()[:12]
    return Host(
        tuple(tuple(sorted(cpus)) for _key, cpus in sorted(by_core.items())),
        memory_gb,
        f"docker-{platform.machine().lower()}-{signature}",
        facts,
    )


def load_image(reference: str, tools: set[str]) -> DockerImage:
    try:
        inspected = json.loads(_command(("docker", "image", "inspect", reference)).stdout)[0]
        image_id = inspected["Id"]
        if inspected["Config"]["User"] != f"{WORKER[0]}:{WORKER[1]}":
            raise ValueError
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise CampaignError("Docker image is not the expected non-root toolbox image") from None
    metadata = json.loads(
        _command(
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
    )
    document = {
        **metadata,
        "image_uri": f"docker@{image_id}",
        "tools": {
            tool: {field: facts[field] for field in TOOL_IMAGE_FIELDS}
            for tool, facts in metadata["tools"].items()
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json") as source:
        json.dump(document, source)
        source.flush()
        image_set = campaign.load_image_set(source.name, tools)
    return DockerImage(image_set, image_id)


def load_plan(path: Path, host: Host, *, allow_s4cmd: bool) -> bench.Plan:
    loaded = campaign.load_campaign_plan(
        path, allow_s4cmd_canary=allow_s4cmd, instances=host.instances()
    )
    if loaded.replay is not None:
        raise CampaignError("the Docker executor does not run replay sidecars yet")
    steps = campaign.expand_launch(loaded.cases, loaded.adapters)
    if any(step.waits_for is not None for step in steps):
        raise CampaignError("the Docker executor does not run prerequisite chains yet")
    return loaded


def schedule(cases: Sequence[bench.Case], seed: int) -> tuple[Scheduled, ...]:
    reps = {case.reps for case in cases}
    if len(reps) != 1:
        raise CampaignError("randomized complete blocks require equal reps on every selected row")
    result: list[Scheduled] = []
    for block in range(1, next(iter(reps), 0) + 1):
        block_seed = hashlib.sha256(f"{seed}:{block}".encode()).digest()
        rng = random.Random(int.from_bytes(block_seed))
        shuffled = list(cases)
        rng.shuffle(shuffled)
        for case in shuffled:
            result.append(Scheduled(len(result) + 1, block, case))
    scheduled = tuple(result)
    expected = [(case.tool, case.label) for case in cases]
    for block in range(1, next(iter(reps), 0) + 1):
        actual = [(item.case.tool, item.case.label) for item in scheduled if item.block == block]
        if len(actual) != len(expected) or sorted(actual) != sorted(expected):
            raise CampaignError(f"randomized block {block} is not a complete case permutation")
    return scheduled


def attest_container_cpuset(image: str, cpuset: str) -> tuple[int, ...]:
    intended = sorted(int(cpu) for cpu in cpuset.split(","))
    code = "import json,os; print(json.dumps(sorted(os.sched_getaffinity(0))))"
    try:
        observed = json.loads(
            _command(
                (
                    "docker",
                    "run",
                    "--rm",
                    "--network=none",
                    f"--cpuset-cpus={cpuset}",
                    "--entrypoint=/usr/bin/python3",
                    image,
                    "-c",
                    code,
                )
            ).stdout
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        raise CampaignError("cannot read the effective Docker cpuset") from None
    if observed != intended:
        raise CampaignError(f"Docker cpuset mismatch: intended {intended}, observed {observed}")
    return tuple(observed)


def _attempt(
    ordinal: int,
    item: Scheduled,
    *,
    suite: str,
    group: str,
    location: str,
    plan: bench.Plan,
    image: DockerImage,
    host: Host,
    results: Path,
) -> tuple[Attempt, str]:
    case = item.case
    tool_image = image.image_set.image_for(case.tool)
    case_id, case_inputs = campaign.case_identity(
        case,
        auth_role=case.auth_role,
        target_bucket=plan.bucket,
        target_region=plan.region,
        location=location,
        tool_slice=tool_image["tool_slice_sha256"],
        platform=tool_image["platform_sha256"],
    )
    attempt_id = identity.attempt_id(case_id, ordinal)
    destination = results / suite / plan.bucket / attempt_id
    scratch = results / ".work" / group / attempt_id
    publication = results / ".publish" / group / attempt_id
    cpuset = host.cpuset(case.resources.vcpus)
    record = Attempt(
        case_id=case_id,
        attempt=ordinal,
        case_inputs=case_inputs,
        group_id=group,
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
        target_bucket=plan.bucket,
        target_region=plan.region,
        target_prefix="",
        config=canonical_json(dict(case.config)),
        input_artifact_sha256=None,
        produced_by=None,
        tool_slice_sha256=tool_image["tool_slice_sha256"],
        platform_sha256=tool_image["platform_sha256"],
        image_uri=tool_image["image_uri"],
        image_set_sha256=image.image_set.sha256,
        executor_env=canonical_json(
            {
                **host.facts,
                "cpuset": cpuset,
                "image_id": image.image_id,
                "network_mode": "default",
            }
        ),
        service_account="docker-host-environment" if case.auth_role else "anonymous",
        secret_resource=None,
        job_name=campaign.job_name_for(suite, case_id, ordinal),
        result_prefix=str(destination),
        purpose=case.purpose,
        statistic=case.statistic,
        origin="planned",
    )
    pairs = campaign.worker_argument_pairs(
        record,
        tool_image,
        output=str(scratch),
        destination=str(destination),
        term_grace=5.0,
    )
    docker = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        f"--name={record.job_name}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        f"--cpuset-cpus={cpuset}",
        f"--volume={scratch}:{scratch}",
        f"--volume={publication}:{destination.parent}",
    ]
    if case.resources.container_memory_gb is not None:
        memory = case.resources.container_memory_gb
        docker.extend((f"--memory={memory}g", f"--memory-swap={memory}g"))
    if case.auth_role is not None:
        docker.append(f"--env={CREDENTIAL_ENV_VAR}")
    docker.extend((image.image_id, *(token for pair in pairs for token in pair)))
    request = {
        "schema_version": 1,
        "executor": EXECUTOR,
        "schedule_index": item.index,
        "block": item.block,
        "docker_argv": docker,
    }
    return record, canonical_json(request)


def _chown(
    image: str,
    results: Path,
    owner: tuple[int, int],
    paths: Sequence[Path],
    *,
    recursive: bool,
) -> None:
    root = results.resolve()
    resolved = [path.resolve() for path in paths]
    if any(path != root and root not in path.parents for path in resolved):
        raise CampaignError("refusing an ownership change outside the results directory")
    argv = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--user=0:0",
        "--entrypoint=/bin/chown",
        f"--volume={root}:{root}",
        image,
    ]
    if recursive:
        argv.append("-R")
    argv.extend((f"{owner[0]}:{owner[1]}", *(str(path) for path in resolved)))
    _command(argv)


def _run(row: Any, image: str, results: Path) -> tuple[bool, str]:
    request = json.loads(row["request_json"])
    argv = request["docker_argv"]
    output = Path(argv[argv.index("--output") + 1])
    destination = Path(row["result_prefix"])
    publication = results / ".publish" / row["group_id"] / row["attempt_id"]
    output.mkdir(parents=True, exist_ok=False)
    publication.mkdir(parents=True, exist_ok=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise CampaignError(f"refusing an existing evidence leaf: {destination}")
    _chown(image, results, WORKER, (output, publication), recursive=False)
    logs = results / "logs" / row["group_id"]
    logs.mkdir(parents=True, exist_ok=True)
    completed: subprocess.CompletedProcess[bytes] | None = None
    timed_out = False
    interrupted = False
    try:
        with (
            (logs / f"{row['attempt_id']}.stdout.log").open("xb") as stdout,
            (logs / f"{row['attempt_id']}.stderr.log").open("xb") as stderr,
        ):
            completed = subprocess.run(
                argv,
                stdout=stdout,
                stderr=stderr,
                timeout=int(row["timeout_s"]) + 900,
            )
    except subprocess.TimeoutExpired:
        subprocess.run(("docker", "stop", "--time=0", row["job_name"]), check=False)
        timed_out = True
    except KeyboardInterrupt:
        subprocess.run(("docker", "stop", "--time=0", row["job_name"]), check=False)
        interrupted = True
    _chown(
        image,
        results,
        (os.getuid(), os.getgid()),
        (output, publication),
        recursive=True,
    )
    staged = publication / destination.name
    if staged.exists():
        staged.replace(destination)
    publication.rmdir()
    if interrupted:
        raise KeyboardInterrupt
    if timed_out:
        return False, "Docker executor exceeded the worker deadline"
    assert completed is not None
    marker = destination / "result.json"
    success = completed.returncode == 0 and marker.is_file()
    detail = "" if success else f"Docker exit={completed.returncode}; marker={marker.is_file()}"
    if success:
        shutil.rmtree(output)
    return success, detail


def cmd_submit(args: Any) -> int:
    if not args.image or not args.results_root or not args.location or args.seed is None:
        raise CampaignError(
            "the Docker executor requires --image, --results-root, --location, and --seed"
        )
    batch_only = [
        name
        for name in (
            "project",
            "results_bucket",
            "image_set",
            "anonymous_worker_sa",
            "authenticated_worker_sa",
            "secret_resource",
            "network",
            "subnetwork",
            "zone",
        )
        if getattr(args, name, None) is not None
    ]
    if batch_only:
        raise CampaignError(
            "the Docker executor does not accept "
            + ", ".join(f"--{name.replace('_', '-')}" for name in batch_only)
        )
    unsupported = [
        flag
        for enabled, flag in (
            (args.reuse_preparations, "--reuse-preparations"),
            (args.skip_measured, "--skip-measured"),
            (args.stagger_seconds != 0.0, "--stagger-seconds"),
        )
        if enabled
    ]
    if unsupported:
        raise CampaignError(
            "the synchronous Docker session does not support " + ", ".join(unsupported)
        )
    suite = validate_suite(args.suite)
    results = Path(args.results_root).resolve()
    host = inspect_host(results)
    loaded = load_plan(Path(args.plan), host, allow_s4cmd=args.allow_retired_s4cmd_s3_canary)
    cases = campaign.selected_cases(loaded.cases, args.case)
    ordered = schedule(cases, args.seed)
    image = load_image(args.image, set(loaded.tools()))
    cpuset_attestations = {
        cpuset: attest_container_cpuset(image.image_id, cpuset)
        for cpuset in sorted({host.cpuset(case.resources.vcpus) for case in cases})
    }
    if any(case.auth_role is not None for case in cases):
        credential = os.environ.get(CREDENTIAL_ENV_VAR)
        if not args.dry_run and credential is None:
            raise CampaignError(f"signed cases require {CREDENTIAL_ENV_VAR}")
        if credential is not None:
            measure.parse_credential_env(credential)
    if args.dry_run:
        ordinals: dict[str, int] = {}
        rendered = []
        for item in ordered:
            tool_image = image.image_set.image_for(item.case.tool)
            case_id, _ = campaign.case_identity(
                item.case,
                auth_role=item.case.auth_role,
                target_bucket=loaded.bucket,
                target_region=loaded.region,
                location=args.location,
                tool_slice=tool_image["tool_slice_sha256"],
                platform=tool_image["platform_sha256"],
            )
            ordinal = ordinals.get(case_id, 0) + 1
            ordinals[case_id] = ordinal
            attempt, request = _attempt(
                ordinal,
                item,
                suite=suite,
                group=args.group or "dry-run",
                location=args.location,
                plan=loaded,
                image=image,
                host=host,
                results=results,
            )
            rendered.append(
                {
                    "index": item.index,
                    "block": item.block,
                    "tool": item.case.tool,
                    "attempt_id": attempt.attempt_id,
                    "request": json.loads(request),
                }
            )
        print(
            json.dumps(
                {
                    "seed": args.seed,
                    "cpuset_attestations": cpuset_attestations,
                    "schedule": rendered,
                },
                indent=2,
            )
        )
        return 0

    results.mkdir(parents=True, exist_ok=True)
    state = results / STATE_FILENAME if args.state == STATE_FILENAME else Path(args.state).resolve()
    con = open_ledger(str(state), suite=suite)
    failed = False
    try:
        group = mint_group_id(con, args.group)
        seen: set[str] = set()
        scheduled: list[tuple[Scheduled, str]] = []
        for item in ordered:
            tool_image = image.image_set.image_for(item.case.tool)
            case_id, case_inputs = campaign.case_identity(
                item.case,
                auth_role=item.case.auth_role,
                target_bucket=loaded.bucket,
                target_region=loaded.region,
                location=args.location,
                tool_slice=tool_image["tool_slice_sha256"],
                platform=tool_image["platform_sha256"],
            )

            def build(ordinal: int, item: Scheduled = item) -> tuple[Attempt, str]:
                return _attempt(
                    ordinal,
                    item,
                    suite=suite,
                    group=group,
                    location=args.location,
                    plan=loaded,
                    image=image,
                    host=host,
                    results=results,
                )

            attempt, _ = journal_intent(
                con,
                case_id=case_id,
                case_inputs=case_inputs,
                build=build,
                repeat=args.repeat or case_id in seen,
            )
            seen.add(case_id)
            scheduled.append((item, attempt.attempt_id))
        rows = {row["attempt_id"]: row for row in attempt_rows(con, group_id=group)}
        schedule_path = results / "schedules" / f"{group}.json"
        schedule_path.parent.mkdir(parents=True, exist_ok=True)
        with schedule_path.open("x") as output:
            json.dump(
                {
                    "schema_version": 2,
                    "executor": EXECUTOR,
                    "executor_contract": "synchronous-serial-session-v1",
                    "group_id": group,
                    "seed": args.seed,
                    "interruption_policy": LOCAL_INTERRUPTION_POLICY,
                    "schedule_derivation": (
                        "block_seed=SHA256(f'{seed}:{block}'); Python random.Random"
                        "(int.from_bytes(block_seed)); shuffle resolved-plan case order"
                    ),
                    "plan_sha256": loaded.digest,
                    "image_id": image.image_id,
                    "host": host.facts,
                    "cpuset_attestations": cpuset_attestations,
                    "location_label": args.location,
                    "results_root": str(results),
                    "attempts": [
                        {
                            "index": item.index,
                            "block": item.block,
                            "tool": item.case.tool,
                            "label": item.case.label,
                            "attempt_id": attempt_id,
                            "request_sha256": hashlib.sha256(
                                rows[attempt_id]["request_json"].encode()
                            ).hexdigest(),
                        }
                        for item, attempt_id in scheduled
                    ],
                },
                output,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
        for _item, attempt_id in scheduled:
            row = rows[attempt_id]
            print(f"campaign: [{attempt_id}] {row['tool']} {row['mode']}")
            set_state(con, attempt_id, "SUBMITTED", "starting seeded serial invocation")
            set_state(con, attempt_id, "RUNNING")
            try:
                success, detail = _run(row, image.image_id, results)
            except KeyboardInterrupt:
                set_state(con, attempt_id, "CANCELLED", "local operator interrupted the run")
                raise
            except (CampaignError, OSError, subprocess.SubprocessError) as exc:
                success, detail = False, str(exc)
            set_state(con, attempt_id, "SUCCEEDED" if success else "FAILED", detail or None)
            failed |= not success
    finally:
        con.close()
    return int(failed)


def cmd_local_close(args: Any) -> int:
    """Settle the unfinished tail of one interrupted Docker session."""
    reason = args.reason.strip()
    if not reason:
        raise CampaignError("local-close requires a non-empty --reason")
    con = open_ledger(args.state)
    try:
        rows = attempt_rows(con, group_id=args.group)
        if not rows:
            raise CampaignError(f"no group {args.group} in this ledger")
        if any(row["executor"] != EXECUTOR for row in rows):
            raise CampaignError("local-close manages Docker groups only")
        live = [row for row in rows if row["state"] not in TERMINAL_STATES]
        live_containers: list[str] = []
        for row in live:
            names = _command(
                (
                    "docker",
                    "ps",
                    "--filter",
                    f"name={row['job_name']}",
                    "--format",
                    "{{.Names}}",
                )
            ).stdout.splitlines()
            if row["job_name"] in names:
                live_containers.append(row["job_name"])
        if live_containers:
            raise CampaignError(
                "local-close refuses while Docker containers are still running: "
                + ", ".join(live_containers)
            )
        complete = [
            row["attempt_id"]
            for row in live
            if row["state"] == "RUNNING"
            and (Path(row["result_prefix"]) / "result.json").is_file()
        ]
        if complete:
            raise CampaignError(
                "local-close refuses to mark complete evidence failed for "
                + ", ".join(complete)
                + "; evidence is complete; rerun submit is not needed; settle by re-running "
                "the session's own settlement or leave as-is"
            )
        for row in live:
            interrupted = row["state"] == "RUNNING"
            state = INTERRUPTED_ROW_STATE if interrupted else UNSTARTED_ROW_STATE
            circumstance = "interrupted while running" if interrupted else "container never started"
            detail = f"local session closed: {circumstance}: {reason}"[:500]
            set_state(con, row["attempt_id"], state, detail)
            print(f"campaign: {row['attempt_id']} settled {state} ({circumstance})")
        if not live:
            print(f"campaign: group {args.group} has no non-terminal Docker rows")
    finally:
        con.close()
    return 0
