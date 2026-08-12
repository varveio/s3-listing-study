"""The orchestrator: read the REAL plan resolver, submit one GCP Batch job
per case repetition, poll them, verify latest submissions, and track state
in campaign.db.

This is a SKETCH standing in for manager/campaign/*.py (~3,500 lines). It
trusts Batch's reported job state completely -- no fingerprinting beyond
recording Case.fingerprint for reference, no attempt-directory
reconciliation. Round 3 deletes round 1/2's flat, hand-rolled plan schema
outright: `from s3_listing_study.manager.bench.plan import Plan` reads the
SAME bench/buckets/<bucket>.yaml + bench/tools.yaml + bench/instances.yaml
the real study runs from (Plan.load() falls back to the repo's own bench/
when a table isn't beside the plan file). A case's tool/mode/resources/env/
reps/timeout_s/fingerprint all come from Plan.load(); this module renders
them into a Batch job body and nothing else.

State lives in one sqlite3 database, mirroring the real manager's choice of
a database over a flat file -- round 1's JSON + temp-file/os.replace() dance
is gone; sqlite's own journal provides the crash-safety it approximated.
Inspect with `sqlite3 campaign.db "SELECT * FROM submissions"`.

Batch and GCS are both the real SDKs now (owner correction):
google-cloud-batch's BatchServiceClient.create_job/get_job/delete_job, not
`gcloud batch jobs ...` subprocesses -- no tempfile --config dance, a typed
JobStatus.State instead of parsed JSON. A rendered job body is still a
plain dict (mirroring manager/campaign/batch.py's render_job() shape);
ParseDict feeds it straight into a batch_v1.Job, the same conversion
manager/campaign/provider.py uses for adoption comparison.

Subcommands: submit, poll, status, cancel, retry, verify.

A job id here identifies a *submission*; verify.py's one-leaf-per-destination
rule only ever disambiguates *physical executions* Batch ran under one
submission (e.g. a silent Batch retry) -- it says nothing about, and does
not dedupe, an operator resubmitting a case on purpose. `retry` is for
exactly that: every resubmission gets its own row, keyed by
(base_job_id, submission), so a retried case's history is several
honestly-numbered submissions, never a mutated original. The real study
keeps the same split at a different layer -- Case.fingerprint identifies
the submission, TwinStamp's execution unit the physical run underneath it.

Credential wiring drives off Case.auth ("anonymous"/"authenticated"; the
plan never carries a secret) plus a --secrets YAML mapping auth stratum ->
{ENV_NAME: Secret Manager version}, e.g.:

    authenticated:
      AWS_ACCESS_KEY_ID: projects/p/secrets/s3-study-aws-key-id/versions/1
"""

from __future__ import annotations

import argparse
import re
import shlex
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from google.api_core.exceptions import GoogleAPIError
from google.cloud import batch_v1
from google.protobuf.json_format import ParseDict

import verify
from s3_listing_study.manager.bench.plan import Case, Plan

STATE_FILENAME = "campaign.db"
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}

JOB_ID_MAX = 63
SUFFIX_HEADROOM = 5  # room for a later "-rN" retry suffix

# N4 supports Hyperdisk only; Batch otherwise defaults a boot disk to
# pd-balanced, which cannot provision an N4 VM. Mirrors
# manager/campaign/batch.py's N4_BOOT_DISK (~line 199); the short image name
# is a Batch-supported Container-Optimized OS image.
N4_BOOT_DISK = {"type": "hyperdisk-balanced", "image": "batch-cos"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    base_job_id TEXT NOT NULL,
    submission INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    mode TEXT NOT NULL,
    case_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    rep INTEGER NOT NULL,
    destination TEXT NOT NULL,
    state TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (base_job_id, submission)
)
"""


def load_secrets(path: str | None) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")


def valid_job_id(raw: str, *, reserve: int = 0) -> str:
    """A Batch-legal job id: starts with a letter, ends alphanumeric, <=63 chars.

    `reserve` leaves headroom for a suffix the caller will append later (a
    retry's "-rN") so a later valid_job_id() on the combined string never
    has to truncate mid-suffix.
    """
    slug = _slug(raw)[: JOB_ID_MAX - reserve].rstrip("-") or "c"
    if not slug[0].isalpha():
        slug = ("c-" + slug)[: JOB_ID_MAX - reserve].rstrip("-")
    return slug


def job_id_for(case: Case, rep: int) -> str:
    # case_id alone is not unique across a plan: it is derived from mode plus
    # axes WITHOUT the tool name (manager/bench/plan.py's derive_case_id), so
    # e.g. s5cmd, minio-mc and s4cmd can all resolve to case_id "recursive"
    # on one plan. tool is prepended so their job ids never collide.
    return valid_job_id(f"{case.tool}-{case.case_id}-{rep}", reserve=SUFFIX_HEADROOM)


def render_batch_job(
    case: Case, destination: str, image: str, secrets_map: dict[str, dict[str, str]]
) -> dict:
    """Minimal Batch v1 job body: one task running measure.py in a container.

    Compare manager/campaign/batch.py's render_job(), which additionally
    handles network/subnet pinning, provisioning model choice, and validates
    every field before rendering.
    """
    commands = [
        "--tool", case.tool,
        "--mode", case.mode,
        "--auth", case.auth,
        "--prefix", "",
        "--output", "/tmp/attempt",
        "--destination", destination,
        "--timeout", str(case.timeout_s),
        "--image", image,
        "--machine-type", case.resources.machine_type,
    ]
    for name, value in case.env:
        commands.extend(("--case-env", f"{name}={value}"))

    # Batch injects each secret as an ordinary env var on the task;
    # --pass-env tells measure.py which of ITS OWN env vars to copy into the
    # subject's env. Naming them here, not baking them into --case-env,
    # keeps the credential values themselves out of this rendered job body.
    credential_secret = secrets_map.get(case.auth) if case.auth != "anonymous" else None
    if credential_secret:
        for env_name in credential_secret:
            commands.extend(("--pass-env", env_name))

    container = {"imageUri": image, "commands": commands}
    if case.resources.docker_options:
        # Batch's memoryMib only schedules the task; nothing enforces it
        # without this. Mirrors manager/campaign/batch.py's container
        # "options" -- without it a memory-sweep case measures nothing.
        container["options"] = shlex.join(case.resources.docker_options)

    task_spec = {
        "runnables": [{"container": container}],
        "computeResource": {
            "cpuMilli": str(case.resources.cpu_milli),
            "memoryMib": str(case.resources.memory_mib),
        },
        "maxRetryCount": 0,
        "maxRunDuration": f"{case.timeout_s + 300}s",
    }
    if credential_secret:
        task_spec["environment"] = {"secretVariables": credential_secret}

    instance_policy = {"machineType": case.resources.machine_type, "provisioningModel": "SPOT"}
    if case.resources.machine_type.startswith("n4-"):
        instance_policy["bootDisk"] = dict(N4_BOOT_DISK)

    return {
        "taskGroups": [{"taskCount": "1", "taskSpec": task_spec}],
        "allocationPolicy": {"instances": [{"policy": instance_policy}]},
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }


def submit_job(project: str, location: str, job_id: str, job_dict: dict) -> None:
    protobuf = batch_v1.Job.pb(batch_v1.Job())
    ParseDict(job_dict, protobuf)
    batch_v1.BatchServiceClient().create_job(
        parent=f"projects/{project}/locations/{location}",
        job=batch_v1.Job.wrap(protobuf),
        job_id=job_id,
        timeout=20,
    )


def describe_job(project: str, location: str, job_id: str) -> str:
    job = batch_v1.BatchServiceClient().get_job(
        name=f"projects/{project}/locations/{location}/jobs/{job_id}", timeout=20
    )
    return batch_v1.JobStatus.State(job.status.state).name


def cancel_job(project: str, location: str, job_id: str) -> None:
    # A cancel is a delete; the returned long-running operation is not
    # awaited here -- the next poll observes the job settling out of Batch.
    batch_v1.BatchServiceClient().delete_job(
        name=f"projects/{project}/locations/{location}/jobs/{job_id}", timeout=20
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


def record_submission(
    con: sqlite3.Connection, *, base_job_id: str, submission: int, job_id: str,
    case: Case, rep: int, destination: str, state: str,
) -> None:
    """The one insert both cmd_submit and cmd_retry use, so the row shape
    exists in exactly one place.
    """
    now = datetime.now(UTC).isoformat()
    con.execute(
        """
        INSERT INTO submissions
            (base_job_id, submission, job_id, tool, mode, case_id, fingerprint,
             rep, destination, state, submitted_at, updated_at)
        VALUES (:base_job_id, :submission, :job_id, :tool, :mode, :case_id, :fingerprint,
                :rep, :destination, :state, :submitted_at, :updated_at)
        """,
        {
            "base_job_id": base_job_id, "submission": submission, "job_id": job_id,
            "tool": case.tool, "mode": case.mode, "case_id": case.case_id,
            "fingerprint": case.fingerprint, "rep": rep, "destination": destination,
            "state": state, "submitted_at": now, "updated_at": now,
        },
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
    plan = Plan.load(Path(args.plan))
    secrets_map = load_secrets(args.secrets)
    con = open_db(args.state)
    try:
        for case in plan.cases:
            for rep in range(1, case.reps + 1):
                base = job_id_for(case, rep)
                if get_submission(con, base) is not None:
                    print(f"campaign: skipping already-tracked job {base}")
                    continue
                destination = f"gs://{args.results_bucket}/{base}/"
                job_dict = render_batch_job(case, destination, args.image, secrets_map)
                if args.dry_run:
                    print(f"campaign: [dry-run] would submit {base}")
                    print(job_dict)
                    continue
                print(f"campaign: submitting {base}")
                submit_job(args.project, args.location, base, job_dict)
                record_submission(
                    con, base_job_id=base, submission=1, job_id=base,
                    case=case, rep=rep, destination=destination, state="SUBMITTED",
                )
    finally:
        con.close()
    return 0


def poll_once(project: str, location: str, con: sqlite3.Connection, rows: list[sqlite3.Row]) -> bool:
    """One describe-and-record pass over `rows`. Returns True once every row
    in it is terminal.
    """
    all_terminal = True
    for row in rows:
        if row["state"] in TERMINAL_STATES:
            continue
        try:
            new_state = describe_job(project, location, row["job_id"])
        except GoogleAPIError as exc:
            print(f"campaign: describe failed for {row['job_id']}: {exc}", file=sys.stderr)
            all_terminal = False
            continue
        if new_state != row["state"]:
            print(f"campaign: {row['job_id']} {row['state']} -> {new_state}")
        update_submission_state(con, row["base_job_id"], row["submission"], new_state)
        if new_state not in TERMINAL_STATES:
            all_terminal = False
    return all_terminal


def cmd_poll(args: argparse.Namespace) -> int:
    con = open_db(args.state)
    try:
        # Default: only the latest submission per case. --all also polls
        # superseded submissions (earlier retries) still sitting in flight.
        rows_for = all_submissions if args.all else latest_submissions
        if not args.watch:
            poll_once(args.project, args.location, con, rows_for(con))
            return 0
        # --watch trusts Batch's reported state completely and just loops
        # describing everything non-terminal until nothing is left to
        # describe; no exponential backoff, no jitter, no wall-time cap.
        while not poll_once(args.project, args.location, con, rows_for(con)):
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
    plan = Plan.load(Path(args.plan))
    secrets_map = load_secrets(args.secrets)
    # Keyed by (tool, case_id), not case_id alone: case_id is derived from
    # mode plus axes WITHOUT the tool name (see job_id_for), so two tools can
    # share one case_id on the same plan.
    cases_by_key = {(case.tool, case.case_id): case for case in plan.cases}
    con = open_db(args.state)
    try:
        for row in latest_submissions(con):
            if args.job_id:
                if row["job_id"] != args.job_id:
                    continue
            elif row["state"] != "FAILED":
                continue
            case = cases_by_key.get((row["tool"], row["case_id"]))
            if case is None:
                print(f"campaign: {row['case_id']!r} is no longer in {args.plan}; skipping retry", file=sys.stderr)
                continue

            next_submission = row["submission"] + 1
            retry_job_id = valid_job_id(f"{row['base_job_id']}-r{next_submission}")
            destination = f"gs://{args.results_bucket}/{retry_job_id}/"
            job_dict = render_batch_job(case, destination, args.image, secrets_map)
            print(f"campaign: retrying {row['job_id']} (submission {row['submission']}) as {retry_job_id}")
            submit_job(args.project, args.location, retry_job_id, job_dict)
            record_submission(
                con, base_job_id=row["base_job_id"], submission=next_submission, job_id=retry_job_id,
                case=case, rep=row["rep"], destination=destination, state="SUBMITTED",
            )
    finally:
        con.close()
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    con = open_db(args.state)
    try:
        for row in all_submissions(con):
            if row["state"] in TERMINAL_STATES:
                continue
            print(f"campaign: cancelling {row['job_id']}")
            try:
                cancel_job(args.project, args.location, row["job_id"])
            except GoogleAPIError as exc:
                print(f"campaign: cancel failed for {row['job_id']}: {exc}", file=sys.stderr)
                continue
            update_submission_state(con, row["base_job_id"], row["submission"], "CANCELLED")
    finally:
        con.close()
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """For each latest submission with a SUCCEEDED job, compare it against
    --reference-case's latest submission in-process, writing verify.json
    into each compared attempt's own leaf. Closes the loop that otherwise
    demanded one manual verify.py invocation per case.
    """
    plan = Plan.load(Path(args.plan))
    con = open_db(args.state, readonly=True)
    try:
        rows = latest_submissions(con)
    finally:
        con.close()

    matches = [row for row in rows if row["case_id"] == args.reference_case]
    if not matches:
        print(f"campaign: no submission for case_id {args.reference_case!r}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        tools = [row["tool"] for row in matches]
        print(f"campaign: case_id {args.reference_case!r} is ambiguous across tools {tools}; "
              "refusing to guess which is the reference", file=sys.stderr)
        return 1
    reference_row = matches[0]

    for row in rows:
        if row["case_id"] == args.reference_case or row["state"] != "SUCCEEDED":
            continue
        exit_code, output = verify.verify_leaves(
            tool=row["tool"], bucket=plan.bucket, prefix="", mode=row["mode"],
            actual_destination=row["destination"], reference_destination=reference_row["destination"],
            adapter_root=args.adapter_root,
        )
        if "verdict" in output:
            print(f"{row['job_id']}: verdict={output['verdict']} (exit {exit_code})")
        else:
            print(f"{row['job_id']}: refused -- {output.get('error')} (exit {exit_code})", file=sys.stderr)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit, poll, verify, and report on a benchmark campaign.")
    parser.add_argument("--state", default=STATE_FILENAME, help="campaign.db path (sqlite3).")
    sub = parser.add_subparsers(dest="command", required=True)

    def _batch_target(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", required=True, help="GCP project Batch jobs run in.")
        p.add_argument("--location", required=True, help="GCP Batch location, e.g. us-central1.")

    def _plan_and_submission(p: argparse.ArgumentParser) -> None:
        p.add_argument("--plan", required=True, help="bench/buckets/<bucket>.yaml path.")
        p.add_argument("--results-bucket", required=True, help="gs:// bucket (name only) attempts upload to.")
        p.add_argument("--image", required=True, help="Container image URI measure.py runs in.")
        p.add_argument("--secrets", default=None, help="YAML: auth stratum -> {ENV_NAME: secret version}.")

    submit_p = sub.add_parser("submit")
    _batch_target(submit_p)
    _plan_and_submission(submit_p)
    submit_p.add_argument("--dry-run", action="store_true", help="Render job bodies without submitting.")
    submit_p.set_defaults(func=cmd_submit)

    poll_p = sub.add_parser("poll")
    _batch_target(poll_p)
    poll_p.add_argument("--watch", action="store_true", help="Loop until every polled row reaches a terminal state.")
    poll_p.add_argument("--interval", type=int, default=30, help="Seconds between --watch polls.")
    poll_p.add_argument("--all", action="store_true", help="Poll every submission, not just each case's latest.")
    poll_p.set_defaults(func=cmd_poll)

    status_p = sub.add_parser("status")
    status_p.set_defaults(func=cmd_status)

    cancel_p = sub.add_parser("cancel")
    _batch_target(cancel_p)
    cancel_p.set_defaults(func=cmd_cancel)

    retry_p = sub.add_parser("retry")
    _batch_target(retry_p)
    _plan_and_submission(retry_p)
    retry_p.add_argument(
        "--job-id", default=None,
        help="Retry only this one job id, regardless of its state. Default: every FAILED case's latest submission.",
    )
    retry_p.set_defaults(func=cmd_retry)

    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--plan", required=True, help="bench/buckets/<bucket>.yaml path.")
    verify_p.add_argument("--reference-case", required=True, help="case_id to compare every other latest submission against.")
    verify_p.add_argument("--adapter-root", default="tools", help="Checkout-relative tools/<tool>/adapter directory.")
    verify_p.set_defaults(func=cmd_verify)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
