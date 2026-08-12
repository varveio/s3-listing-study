"""The orchestrator: read a plan, submit one GCP Batch job per (tool, mode,
repetition), poll them, and track state in one JSON file.

This is a SKETCH standing in for manager/campaign/ + manager/bench/plan.py
(~3,000 lines combined). It trusts `gcloud batch jobs describe`'s reported
state completely -- no fingerprinting, no duplicate-submission detection, no
attempt-directory reconciliation, no retry policy, no case ID derivation
scheme, no per-tool resource sizing beyond one machine type for the whole
plan. campaign-state.json IS written via temp-file + os.replace() (see
save_state) so a crash mid-write leaves the previous complete ledger rather
than a corrupt one -- there is still no lock, so two concurrent campaign.py
processes can race and one's update can be lost.

Plan schema (see plan-example.yaml), deliberately smaller than
bench/buckets/*.yaml + bench/tools.yaml:

    bucket: str
    region: str            # informational; not passed to Batch
    location: str           # GCP Batch location, e.g. us-central1
    prefix: str
    results_bucket: str     # gs:// bucket attempts are uploaded to
    image: str               # container image URI measure.py runs in
    machine_type: str
    repetitions: int
    tools:
      - name: str
        mode: str
        container_memory_gb: int   # optional; only swath/s3p heap-size off it

Subcommands: submit, poll, status, cancel.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

from tools import TOOLS, heap_env_for

STATE_FILENAME = "campaign-state.json"
TERMINAL_STATES = {"SUCCEEDED", "FAILED"}
REQUIRED_PLAN_KEYS = (
    "bucket", "location", "results_bucket", "image", "machine_type", "tools",
)


def load_plan(path: str) -> dict:
    with open(path) as f:
        if path.endswith((".yaml", ".yml")):
            plan = yaml.safe_load(f)
        else:
            plan = json.load(f)
    validate_plan(plan)
    return plan


def validate_plan(plan: dict) -> None:
    """Fail loudly on a plan missing what campaign.py needs, rather than
    letting a KeyError surface mid-submit with a half-tracked state file.

    Compare manager/bench/plan.py, which additionally validates a fingerprint
    scheme, inheritance across defaults/rows, and a product/zip case
    generator -- none of which this sketch's flat tool list has to resolve.
    """
    missing = [key for key in REQUIRED_PLAN_KEYS if key not in plan]
    if missing:
        raise ValueError(f"plan is missing required key(s): {', '.join(missing)}")
    if not plan["tools"]:
        raise ValueError("plan must list at least one tool")
    for entry in plan["tools"]:
        if "name" not in entry or "mode" not in entry:
            raise ValueError(f"tool entry must have name and mode: {entry!r}")
        if entry["name"] not in TOOLS:
            raise ValueError(f"unknown tool {entry['name']!r}; known tools: {sorted(TOOLS)}")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")


def job_id_for(bucket: str, tool: str, mode: str, rep: int, container_memory_gb: int | None) -> str:
    """Deterministic Batch job id: stable across re-submission of the same case.

    container_memory_gb is folded in so two rows for the same tool/mode that
    differ only in the memory sweep (see plan-example.yaml's swath entries)
    render distinct ids instead of colliding.
    """
    memory_suffix = f"-{container_memory_gb}gb" if container_memory_gb else ""
    return _slug(f"{bucket}-{tool}-{mode}{memory_suffix}-{rep}")[:63]


def build_cases(plan: dict) -> list[dict]:
    cases = []
    for tool_entry in plan["tools"]:
        container_memory_gb = tool_entry.get("container_memory_gb")
        for rep in range(1, plan.get("repetitions", 1) + 1):
            cases.append(
                {
                    "tool": tool_entry["name"],
                    "mode": tool_entry["mode"],
                    "rep": rep,
                    "container_memory_gb": container_memory_gb,
                    "job_id": job_id_for(
                        plan["bucket"], tool_entry["name"], tool_entry["mode"], rep, container_memory_gb
                    ),
                }
            )
    return cases


def render_batch_job(plan: dict, case: dict) -> dict:
    """Minimal Batch v1 job body: one task running measure.py in a container.

    Compare manager/campaign/batch.py's render_job(), which additionally
    handles authenticated-credential secrets, N4 boot disks, network/subnet
    pinning, provisioning model, and validates every field before rendering.
    """
    destination = f"gs://{plan['results_bucket']}/{case['job_id']}/"
    container_memory_gb = case.get("container_memory_gb")
    memory_mib = int(container_memory_gb * 1024) if container_memory_gb else 4096

    commands = [
        "--tool", case["tool"],
        "--mode", case["mode"],
        "--bucket", plan["bucket"],
        "--prefix", plan.get("prefix", ""),
        "--output", "/tmp/attempt",
        "--destination", destination,
    ]
    # Mirrors manager/campaign/batch.py's --case-env pairs: the two tools
    # with a managed runtime get told the heap share of THIS case's memory
    # ceiling, not a value baked into the image.
    heap_env = heap_env_for(case["tool"], memory_mib)
    if heap_env is not None:
        commands.extend(("--case-env", f"{heap_env[0]}={heap_env[1]}"))

    return {
        "taskGroups": [
            {
                "taskCount": "1",
                "taskSpec": {
                    "runnables": [{"container": {"imageUri": plan["image"], "commands": commands}}],
                    "computeResource": {"cpuMilli": "2000", "memoryMib": str(memory_mib)},
                    "maxRetryCount": 0,
                },
            }
        ],
        "allocationPolicy": {
            "instances": [{"policy": {"machineType": plan["machine_type"]}}],
        },
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }


def submit_job(plan: dict, case: dict) -> None:
    body = render_batch_job(plan, case)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
        config_path = f.name
    subprocess.run(
        [
            "gcloud", "batch", "jobs", "submit", case["job_id"],
            f"--location={plan['location']}",
            f"--config={config_path}",
        ],
        check=True,
    )


def describe_job(plan: dict, job_id: str) -> dict:
    result = subprocess.run(
        [
            "gcloud", "batch", "jobs", "describe", job_id,
            f"--location={plan['location']}",
            "--format=json",
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def cancel_job(plan: dict, job_id: str) -> None:
    subprocess.run(
        ["gcloud", "batch", "jobs", "delete", job_id, f"--location={plan['location']}", "--quiet"],
        check=True,
    )


def load_state(state_path: Path) -> dict:
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {"jobs": {}}


def save_state(state_path: Path, state: dict) -> None:
    # Temp file in the same directory + os.replace(): a reader never sees a
    # half-written ledger, only the previous complete one or the new one.
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp_path, state_path)


def cmd_submit(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    state_path = Path(args.state)
    state = load_state(state_path)

    for case in build_cases(plan):
        job_id = case["job_id"]
        if job_id in state["jobs"]:
            print(f"campaign: skipping already-tracked job {job_id}")
            continue
        if args.dry_run:
            print(f"campaign: [dry-run] would submit {job_id}")
            print(json.dumps(render_batch_job(plan, case), indent=2))
            continue
        print(f"campaign: submitting {job_id}")
        submit_job(plan, case)
        now = datetime.now(UTC).isoformat()
        state["jobs"][job_id] = {
            "tool": case["tool"],
            "mode": case["mode"],
            "rep": case["rep"],
            "destination": f"gs://{plan['results_bucket']}/{job_id}/",
            "state": "SUBMITTED",
            "submitted_at": now,
            "updated_at": now,
        }
        save_state(state_path, state)
    return 0


def poll_once(plan: dict, state_path: Path, state: dict) -> bool:
    """One describe-and-record pass. Returns True once every job is terminal."""
    all_terminal = True
    for job_id, record in state["jobs"].items():
        if record["state"] in TERMINAL_STATES:
            continue
        try:
            described = describe_job(plan, job_id)
        except subprocess.CalledProcessError as exc:
            print(f"campaign: describe failed for {job_id}: {exc}", file=sys.stderr)
            all_terminal = False
            continue
        new_state = described.get("status", {}).get("state", record["state"])
        if new_state != record["state"]:
            print(f"campaign: {job_id} {record['state']} -> {new_state}")
        record["state"] = new_state
        record["updated_at"] = datetime.now(UTC).isoformat()
        save_state(state_path, state)
        if new_state not in TERMINAL_STATES:
            all_terminal = False
    return all_terminal


def cmd_poll(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    state_path = Path(args.state)
    state = load_state(state_path)

    if not args.watch:
        poll_once(plan, state_path, state)
        return 0

    # --watch trusts gcloud's reported state completely and just loops
    # describing everything non-terminal until nothing is left to describe;
    # no exponential backoff, no jitter, no cap on total wall time.
    while not poll_once(plan, state_path, state):
        time.sleep(args.interval)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state(Path(args.state))
    counts: dict[str, int] = {}
    for job_id, record in sorted(state["jobs"].items()):
        print(f"{job_id:<40} {record['state']:<12} {record['tool']:<12} rep={record['rep']}")
        counts[record["state"]] = counts.get(record["state"], 0) + 1
    summary = " ".join(f"{state_name}={count}" for state_name, count in sorted(counts.items()))
    print(f"-- {len(state['jobs'])} job(s): {summary}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    state_path = Path(args.state)
    state = load_state(state_path)

    for job_id, record in state["jobs"].items():
        if record["state"] in TERMINAL_STATES:
            continue
        print(f"campaign: cancelling {job_id}")
        try:
            cancel_job(plan, job_id)
        except subprocess.CalledProcessError as exc:
            print(f"campaign: cancel failed for {job_id}: {exc}", file=sys.stderr)
            continue
        record["state"] = "FAILED"
        record["updated_at"] = datetime.now(UTC).isoformat()
        save_state(state_path, state)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit, poll, and report on a benchmark campaign.")
    parser.add_argument("--state", default=STATE_FILENAME, help="campaign-state.json path")
    sub = parser.add_subparsers(dest="command", required=True)

    submit_p = sub.add_parser("submit")
    submit_p.add_argument("--plan", required=True)
    submit_p.add_argument("--dry-run", action="store_true", help="Render job bodies without submitting.")
    submit_p.set_defaults(func=cmd_submit)

    poll_p = sub.add_parser("poll")
    poll_p.add_argument("--plan", required=True)
    poll_p.add_argument("--watch", action="store_true", help="Loop until every job reaches a terminal state.")
    poll_p.add_argument("--interval", type=int, default=30, help="Seconds between --watch polls.")
    poll_p.set_defaults(func=cmd_poll)

    status_p = sub.add_parser("status")
    status_p.set_defaults(func=cmd_status)

    cancel_p = sub.add_parser("cancel")
    cancel_p.add_argument("--plan", required=True)
    cancel_p.set_defaults(func=cmd_cancel)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
