"""The orchestrator: read a plan, submit one GCP Batch job per (tool, mode,
repetition), poll them, and track state in campaign.db.

This is a SKETCH standing in for manager/campaign/ + manager/bench/plan.py
(~3,000 lines combined). It trusts `gcloud batch jobs describe`'s reported
state completely -- no fingerprinting, no attempt-directory reconciliation,
no case ID derivation scheme, no per-tool resource sizing beyond one machine
type for the whole plan.

State lives in one sqlite3 database (mirroring, in miniature, the real
manager/campaign/ledger.py's choice of a database over a flat file). This
REPLACES round 1's JSON campaign-state.json + temp-file/os.replace() dance:
sqlite's own journal now provides the crash-safety that hack approximated, so
there is no equivalent atomic-rewrite code left to write. The trade-off is
that campaign.db is no longer git-diffable or eyeballable in a text editor --
inspect it with `sqlite3 campaign.db "SELECT * FROM submissions"` instead.

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
        container_memory_gb: int          # optional; only swath/s3p heap-size off it
        credential_secret: {str: str}     # optional; ENV_NAME -> Secret Manager version

Subcommands: submit, poll, status, cancel, retry.

Submission vs. physical execution, in miniature: a job id here identifies a
*submission*. verify.py's one-leaf-per-destination rule only ever disambiguates
*physical executions* Batch ran under that one submission (e.g. a silent Batch
retry re-running the same task) -- it says nothing about, and does not dedupe,
an operator or this module resubmitting the same case on purpose. `retry`
exists for exactly that: every resubmission gets its own row, keyed by
(base_job_id, submission), so a retried case's history is several
honestly-numbered submissions in the ledger, never a mutated original. The
real study keeps the same split at a different layer -- `Case.fingerprint`
identifies the submission, TwinStamp's execution unit the physical run
underneath it.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime

import yaml

from tools import TOOLS, heap_env_for

STATE_FILENAME = "campaign.db"
TERMINAL_STATES = {"SUCCEEDED", "FAILED"}
REQUIRED_PLAN_KEYS = (
    "bucket", "location", "results_bucket", "image", "machine_type", "tools",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    base_job_id TEXT NOT NULL,
    submission INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    mode TEXT NOT NULL,
    rep INTEGER NOT NULL,
    container_memory_gb INTEGER,
    credential_secret TEXT,
    destination TEXT NOT NULL,
    state TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (base_job_id, submission)
)
"""


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
    letting a KeyError surface mid-submit with a half-tracked ledger.

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
        secret = entry.get("credential_secret")
        if secret is not None and not all(isinstance(k, str) and isinstance(v, str) for k, v in secret.items()):
            raise ValueError(f"credential_secret must map str env names to str secret versions: {secret!r}")


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
                    "credential_secret": tool_entry.get("credential_secret"),
                    "job_id": job_id_for(
                        plan["bucket"], tool_entry["name"], tool_entry["mode"], rep, container_memory_gb
                    ),
                }
            )
    return cases


def render_batch_job(plan: dict, case: dict) -> dict:
    """Minimal Batch v1 job body: one task running measure.py in a container.

    Compare manager/campaign/batch.py's render_job(), which additionally
    handles N4 boot disks, network/subnet pinning, provisioning model, and
    validates every field before rendering.
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

    # Batch injects each secret as an ordinary env var on the task; --pass-env
    # tells measure.py which of ITS OWN env vars to copy into the subject's
    # env. Naming them here, not baking them into --case-env, keeps the
    # credential values themselves out of this rendered job body entirely.
    credential_secret = case.get("credential_secret") or {}
    for env_name in credential_secret:
        commands.extend(("--pass-env", env_name))

    task_spec = {
        "runnables": [{"container": {"imageUri": plan["image"], "commands": commands}}],
        "computeResource": {"cpuMilli": "2000", "memoryMib": str(memory_mib)},
        "maxRetryCount": 0,
    }
    if credential_secret:
        task_spec["environment"] = {"secretVariables": credential_secret}

    return {
        "taskGroups": [{"taskCount": "1", "taskSpec": task_spec}],
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


def open_db(path: str, *, readonly: bool = False) -> sqlite3.Connection:
    """Open campaign.db. A writer creates the table if absent (no migration
    machinery beyond that); report.py's read-only callers use a `mode=ro`
    URI so an accidental write there fails loudly instead of silently
    succeeding against a file it has no business mutating.
    """
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path)
        con.execute(SCHEMA)
        con.commit()
    con.row_factory = sqlite3.Row
    return con


def insert_submission(con: sqlite3.Connection, row: dict) -> None:
    con.execute(
        """
        INSERT INTO submissions
            (base_job_id, submission, job_id, tool, mode, rep, container_memory_gb,
             credential_secret, destination, state, submitted_at, updated_at)
        VALUES (:base_job_id, :submission, :job_id, :tool, :mode, :rep, :container_memory_gb,
                :credential_secret, :destination, :state, :submitted_at, :updated_at)
        """,
        row,
    )
    con.commit()


def update_submission_state(con: sqlite3.Connection, base_job_id: str, submission: int, state: str) -> None:
    con.execute(
        "UPDATE submissions SET state = ?, updated_at = ? WHERE base_job_id = ? AND submission = ?",
        (state, datetime.now(UTC).isoformat(), base_job_id, submission),
    )
    con.commit()


def get_submission(con: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM submissions WHERE job_id = ?", (job_id,)).fetchone()


def all_submissions(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM submissions ORDER BY base_job_id, submission").fetchall()


def latest_submissions(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """One row per base_job_id (case): the one with the highest submission."""
    return con.execute(
        """
        SELECT s.* FROM submissions s
        JOIN (SELECT base_job_id, MAX(submission) AS submission FROM submissions GROUP BY base_job_id) latest
          ON s.base_job_id = latest.base_job_id AND s.submission = latest.submission
        ORDER BY s.base_job_id
        """
    ).fetchall()


def cmd_submit(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    con = open_db(args.state)
    try:
        for case in build_cases(plan):
            job_id = case["job_id"]
            if get_submission(con, job_id) is not None:
                print(f"campaign: skipping already-tracked job {job_id}")
                continue
            if args.dry_run:
                print(f"campaign: [dry-run] would submit {job_id}")
                print(json.dumps(render_batch_job(plan, case), indent=2))
                continue
            print(f"campaign: submitting {job_id}")
            submit_job(plan, case)
            now = datetime.now(UTC).isoformat()
            insert_submission(con, {
                "base_job_id": job_id,
                "submission": 1,
                "job_id": job_id,
                "tool": case["tool"],
                "mode": case["mode"],
                "rep": case["rep"],
                "container_memory_gb": case.get("container_memory_gb"),
                "credential_secret": json.dumps(case["credential_secret"]) if case.get("credential_secret") else None,
                "destination": f"gs://{plan['results_bucket']}/{job_id}/",
                "state": "SUBMITTED",
                "submitted_at": now,
                "updated_at": now,
            })
    finally:
        con.close()
    return 0


def poll_once(plan: dict, con: sqlite3.Connection, rows: list[sqlite3.Row]) -> bool:
    """One describe-and-record pass over `rows`. Returns True once every row
    in it is terminal.
    """
    all_terminal = True
    for row in rows:
        if row["state"] in TERMINAL_STATES:
            continue
        try:
            described = describe_job(plan, row["job_id"])
        except subprocess.CalledProcessError as exc:
            print(f"campaign: describe failed for {row['job_id']}: {exc}", file=sys.stderr)
            all_terminal = False
            continue
        new_state = described.get("status", {}).get("state", row["state"])
        if new_state != row["state"]:
            print(f"campaign: {row['job_id']} {row['state']} -> {new_state}")
        update_submission_state(con, row["base_job_id"], row["submission"], new_state)
        if new_state not in TERMINAL_STATES:
            all_terminal = False
    return all_terminal


def cmd_poll(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    con = open_db(args.state)
    try:
        # Default: only the latest submission per case. --all also polls
        # superseded submissions (earlier retries) still sitting in flight.
        rows_for = all_submissions if args.all else latest_submissions
        if not args.watch:
            poll_once(plan, con, rows_for(con))
            return 0
        # --watch trusts gcloud's reported state completely and just loops
        # describing everything non-terminal until nothing is left to
        # describe; no exponential backoff, no jitter, no wall-time cap.
        while not poll_once(plan, con, rows_for(con)):
            time.sleep(args.interval)
        return 0
    finally:
        con.close()


def cmd_status(args: argparse.Namespace) -> int:
    con = open_db(args.state, readonly=True)
    try:
        rows = latest_submissions(con)
        counts: dict[str, int] = {}
        for row in rows:
            print(f"{row['job_id']:<40} {row['state']:<12} {row['tool']:<12} "
                  f"rep={row['rep']} submission={row['submission']}")
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        total = con.execute("SELECT count(*) FROM submissions").fetchone()[0]
        summary = " ".join(f"{state_name}={count}" for state_name, count in sorted(counts.items()))
        print(f"-- {len(rows)} case(s), {total} total submission(s): {summary}")
    finally:
        con.close()
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    con = open_db(args.state)
    try:
        for row in latest_submissions(con):
            if args.job_id:
                if row["job_id"] != args.job_id:
                    continue
            elif row["state"] != "FAILED":
                continue

            next_submission = row["submission"] + 1
            base = row["base_job_id"]
            retry_job_id = f"{base}-r{next_submission}"
            case = {
                "tool": row["tool"],
                "mode": row["mode"],
                "rep": row["rep"],
                "container_memory_gb": row["container_memory_gb"],
                "credential_secret": json.loads(row["credential_secret"]) if row["credential_secret"] else None,
                "job_id": retry_job_id,
            }
            print(f"campaign: retrying {row['job_id']} (submission {row['submission']}) as {retry_job_id}")
            submit_job(plan, case)
            now = datetime.now(UTC).isoformat()
            insert_submission(con, {
                "base_job_id": base,
                "submission": next_submission,
                "job_id": retry_job_id,
                "tool": row["tool"],
                "mode": row["mode"],
                "rep": row["rep"],
                "container_memory_gb": row["container_memory_gb"],
                "credential_secret": row["credential_secret"],
                "destination": f"gs://{plan['results_bucket']}/{retry_job_id}/",
                "state": "SUBMITTED",
                "submitted_at": now,
                "updated_at": now,
            })
    finally:
        con.close()
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    con = open_db(args.state)
    try:
        for row in all_submissions(con):
            if row["state"] in TERMINAL_STATES:
                continue
            print(f"campaign: cancelling {row['job_id']}")
            try:
                cancel_job(plan, row["job_id"])
            except subprocess.CalledProcessError as exc:
                print(f"campaign: cancel failed for {row['job_id']}: {exc}", file=sys.stderr)
                continue
            update_submission_state(con, row["base_job_id"], row["submission"], "FAILED")
    finally:
        con.close()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit, poll, and report on a benchmark campaign.")
    parser.add_argument("--state", default=STATE_FILENAME, help="campaign.db path (sqlite3).")
    sub = parser.add_subparsers(dest="command", required=True)

    submit_p = sub.add_parser("submit")
    submit_p.add_argument("--plan", required=True)
    submit_p.add_argument("--dry-run", action="store_true", help="Render job bodies without submitting.")
    submit_p.set_defaults(func=cmd_submit)

    poll_p = sub.add_parser("poll")
    poll_p.add_argument("--plan", required=True)
    poll_p.add_argument("--watch", action="store_true", help="Loop until every polled row reaches a terminal state.")
    poll_p.add_argument("--interval", type=int, default=30, help="Seconds between --watch polls.")
    poll_p.add_argument("--all", action="store_true", help="Poll every submission, not just each case's latest.")
    poll_p.set_defaults(func=cmd_poll)

    status_p = sub.add_parser("status")
    status_p.set_defaults(func=cmd_status)

    cancel_p = sub.add_parser("cancel")
    cancel_p.add_argument("--plan", required=True)
    cancel_p.set_defaults(func=cmd_cancel)

    retry_p = sub.add_parser("retry")
    retry_p.add_argument("--plan", required=True)
    retry_p.add_argument(
        "--job-id", default=None,
        help="Retry only this one job id, regardless of its state. Default: every FAILED case's latest submission.",
    )
    retry_p.set_defaults(func=cmd_retry)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
