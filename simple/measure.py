"""The worker: compile one case's command through the real tool capsule, run
it, capture what it did, and upload the result to GCS.

This is a SKETCH. It stands in for src/s3_listing_study/worker/engine.py +
worker/upload.py (~2,600 lines combined), and trusts the process exit code
and GCP Batch's job status completely. It does NOT do: create-only ("never
overwrite") upload preconditions, TwinStamp evidence sealing, disk sampling,
or SIGTERM/grace-period handling. It DOES keep several properties that are
cheap here and expensive to lose (see README.md's "The minimum rigor we
kept" sections): every invocation uploads to its own uuid4 leaf under
--destination rather than a shared path; result.json is written atomically
and uploaded last, so a leaf missing it is legible as incomplete; and a
bounded pre-upload secret scan of stdout/stderr (see scan_for_secrets)
refuses to upload anything at all on a hit.

Command compilation and native-output normalization are no longer guessed
here (round 2's simple/tools/ is gone): see adapters.py, the bridge to the
real tools/<tool>/adapter/ capsules.

Usage:
    measure.py --tool s5cmd --mode recursive --bucket some-bucket --region us-east-1 \\
        --output /tmp/attempt --destination gs://results-bucket/job-id/
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import adapters
import gcs
from contract import sha256_of

EXIT_ADAPTER_ERROR = 3
EXIT_SECRET_DETECTED = 9

SECRET_PATTERNS = {
    "AWS access key id": re.compile(rb"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "aws_secret_access_key assignment": re.compile(rb"aws_secret_access_key\s*=\s*\S+", re.IGNORECASE),
    "GCP private key": re.compile(rb"BEGIN PRIVATE KEY"),
}
SECRET_SCAN_CHUNK = 1024 * 1024
SECRET_SCAN_OVERLAP = 64  # wider than any pattern above, so a match can't split across a chunk boundary

SUBJECT_ENV = {
    # Mirrors worker/engine.py's BASE_SUBJECT_ENV: a small, stable
    # environment rather than whatever ambient variables happen to be set
    # in the runner. The real engine also strips secret-carrying variables;
    # this sketch just fixes the basics and disables the EC2 metadata probe
    # every AWS SDK tries first. A capsule's own FUNCTIONAL_ENV (see
    # adapters.compile_command) is merged in on top of this.
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "AWS_EC2_METADATA_DISABLED": "true",
}


def parse_case_env(pairs: list[str]) -> dict[str, str]:
    env = {}
    for pair in pairs:
        name, _, value = pair.partition("=")
        if not name or not _:
            raise ValueError(f"--case-env must be NAME=VALUE: {pair!r}")
        env[name] = value
    return env


def scan_for_secrets(paths: list[Path]) -> str | None:
    """The name of the first stream+pattern a possible secret matched, or None.

    A leak gate, not redaction: on a hit the caller uploads nothing and never
    prints the matched text itself, only which stream and which pattern.
    Streamed in overlapping chunks rather than one read_bytes() call, so a
    large capture never sits in memory whole. Bounded gap, not a full scan:
    only stdout/stderr are checked here, never a dataset-sink tool's native
    output -- the real engine's secret_scan.py scans everything an attempt
    could publish.
    """
    for path in paths:
        if not path.exists():
            continue
        with open(path, "rb") as f:
            carry = b""
            while chunk := f.read(SECRET_SCAN_CHUNK):
                window = carry + chunk
                for name, pattern in SECRET_PATTERNS.items():
                    if pattern.search(window):
                        return f"{path.name}: {name}"
                carry = window[-SECRET_SCAN_OVERLAP:]
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile, run, and upload one case's attempt.")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--auth", default="anonymous", choices=("anonymous", "authenticated"))
    parser.add_argument(
        "--adapter-dir", default=adapters.DEFAULT_ADAPTER_DIR,
        help="Directory holding this tool's command.py/normalize.py (one per derived image).",
    )
    parser.add_argument("--output", required=True, help="Local attempt directory to write into.")
    parser.add_argument(
        "--destination", required=True,
        help="gs://bucket/job-id/ this case's attempts land under; this invocation's own uuid4 leaf is appended.",
    )
    parser.add_argument("--timeout", type=int, default=3600, help="Seconds before the subject is killed.")
    parser.add_argument(
        "--case-env", action="append", default=[], metavar="NAME=VALUE",
        help="Extra environment for the subject, e.g. JAVA_TOOL_OPTIONS for swath. Repeatable.",
    )
    parser.add_argument(
        "--pass-env", action="append", default=[], metavar="NAME",
        help="Copy NAME from this process's own environment into the subject's env "
             "(e.g. a Batch secretVariable). Repeatable. Never recorded in result.json.",
    )
    parser.add_argument("--image", default=None, help="Image URI this attempt ran under; campaign.py knows it.")
    parser.add_argument("--machine-type", default=None, help="Batch machine type; campaign.py knows it.")
    return parser.parse_args(argv)


def preflight(argv: tuple[str, ...]) -> bool:
    """Check the subject binary exists before we bother creating an attempt dir.

    The real engine resolves this implicitly by attempting exec() and
    reading the OS error; a preflight check just gets to the same verdict
    with a clearer message.
    """
    binary = shutil.which(argv[0])
    if binary is None:
        print(f"measure: {argv[0]!r} not found on PATH", file=sys.stderr)
        return False
    return True


def run_tool(
    argv: tuple[str, ...], attempt_dir: Path, timeout: int, env: dict[str, str]
) -> tuple[int, float, int, bool]:
    """Run argv, capture stdout/stderr to files, return
    (exit_code, wall_s, max_rss_kb, timed_out).
    """
    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"

    start = time.monotonic()
    timed_out = False
    with open(stdout_path, "wb") as stdout_f, open(stderr_path, "wb") as stderr_f:
        try:
            proc = subprocess.Popen(argv, stdout=stdout_f, stderr=stderr_f, env=env)
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            exit_code = 124  # conventional timeout exit code
            timed_out = True
    wall_s = time.monotonic() - start

    # RUSAGE_CHILDREN aggregates every child this interpreter has waited on;
    # fine here because measure.py runs exactly one subject per invocation.
    rusage = resource.getrusage(resource.RUSAGE_CHILDREN)
    max_rss_kb = rusage.ru_maxrss  # KiB on Linux

    return exit_code, wall_s, max_rss_kb, timed_out


def gzip_file(path: Path) -> Path:
    gz_path = path.with_suffix(path.suffix + ".gz")
    with open(path, "rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return gz_path


def row_count_for(adapter_dir: str, tool: str, mode: str, prefix: str, native_path: Path) -> tuple[int | None, str | None]:
    """(row_count, error) -- mirrors worker/summary.py's post-measurement,
    bounded native counting, simplified to "run the normalizer, count its
    output lines" rather than importing each capsule's own count_rows().
    """
    try:
        tsv = adapters.normalize_attempt(adapter_dir, tool, mode, prefix, native_path.read_bytes())
    except adapters.AdapterError as exc:
        return None, str(exc)[:300]
    return tsv.count(b"\n"), None


def write_result_atomic(path: Path, result: dict) -> None:
    """Write result.json via temp-file-then-rename so a reader never observes
    a partially-written marker -- only ever "absent" or "complete".
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(result, indent=2) + "\n")
    os.replace(tmp_path, path)


def upload(attempt_dir: Path, destination: str) -> bool:
    """Upload everything except result.json first, then result.json alone,
    last. A leaf whose upload dies between the two steps is left with
    artifacts but no marker -- exactly the shape verify.py treats as
    "incomplete", never as a passing (or failing) verdict.
    """
    artifacts = sorted(p for p in attempt_dir.iterdir() if p.name != "result.json")
    try:
        for path in artifacts:
            gcs.upload_file(path, destination.rstrip("/") + "/" + path.name)
        gcs.upload_file(attempt_dir / "result.json", destination.rstrip("/") + "/result.json")
    except Exception as exc:
        print(f"measure: upload failed: {exc}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    attempt_dir = Path(args.output)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    try:
        command, functional_env = adapters.compile_command(
            args.adapter_dir, args.tool, mode=args.mode, bucket=args.bucket, region=args.region,
            prefix=args.prefix, auth=args.auth, sink_dir=str(attempt_dir),
        )
    except adapters.AdapterError as exc:
        print(f"measure: {exc}", file=sys.stderr)
        return EXIT_ADAPTER_ERROR

    if not preflight(command):
        return 127

    try:
        case_env = parse_case_env(args.case_env)
    except ValueError as exc:
        print(f"measure: {exc}", file=sys.stderr)
        return 2
    # --pass-env is deliberately kept out of case_env: case_env is recorded
    # in result.json (a published artifact), and a value copied in here is a
    # credential -- e.g. Batch's secretVariables land in os.environ, and this
    # is how they reach the subject without ever being written down.
    passthrough_env = {name: os.environ[name] for name in args.pass_env if name in os.environ}
    missing_pass_env = [name for name in args.pass_env if name not in os.environ]
    if missing_pass_env:
        print(f"measure: --pass-env variable(s) not set in this environment: {missing_pass_env}", file=sys.stderr)
    env = {**SUBJECT_ENV, **functional_env, **case_env, **passthrough_env}

    # Every invocation gets its own leaf: two launches of the same case
    # never contend for the same destination, and there is no "last write
    # wins" to reason about.
    attempt_uuid = str(uuid.uuid4())
    leaf_destination = args.destination.rstrip("/") + "/" + attempt_uuid + "/"

    started_at = datetime.now(UTC).isoformat()
    exit_code, wall_s, max_rss_kb, timed_out = run_tool(command, attempt_dir, args.timeout, env)
    finished_at = datetime.now(UTC).isoformat()

    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"
    stdout_size = stdout_path.stat().st_size if stdout_path.exists() else 0
    stderr_size = stderr_path.stat().st_size if stderr_path.exists() else 0

    # Scanned uncompressed, before anything is uploaded: a hit here refuses
    # the whole leaf outright rather than uploading the captured output and
    # hoping something downstream notices.
    secret_hit = scan_for_secrets([stdout_path, stderr_path])
    if secret_hit:
        print(f"measure: possible secret in {secret_hit}; refusing to upload this attempt", file=sys.stderr)
        return EXIT_SECRET_DETECTED

    # Tool failures and partial runs are deliberately not counted (mirrors
    # worker/summary.py): their raw output remains evidence, but its row
    # count is not the target's completed logical object count.
    row_count = row_count_error = None
    if exit_code == 0 and not timed_out:
        row_count, row_count_error = row_count_for(args.adapter_dir, args.tool, args.mode, args.prefix, stdout_path)

    stdout_gz = gzip_file(stdout_path) if stdout_path.exists() else None
    stderr_gz = gzip_file(stderr_path) if stderr_path.exists() else None
    # Computed once, before the marker is written -- nothing after this adds
    # another artifact, so there is no stale-then-corrected total to chase.
    artifacts_size_bytes = sum(p.stat().st_size for p in attempt_dir.iterdir())

    result = {
        "tool": args.tool,
        "mode": args.mode,
        "bucket": args.bucket,
        "region": args.region,
        "prefix": args.prefix,
        "auth": args.auth,
        "attempt_uuid": attempt_uuid,
        "destination": leaf_destination,
        "argv": list(command),
        "case_env": case_env,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "wall_seconds": round(wall_s, 3),
        "max_rss_kb": max_rss_kb,
        "row_count": row_count,
        "row_count_error": row_count_error,
        "started_at": started_at,
        "finished_at": finished_at,
        "stdout_size": stdout_size,
        "stderr_size": stderr_size,
        "stdout_gz": stdout_gz.name if stdout_gz else None,
        "stdout_gz_sha256": sha256_of(stdout_gz) if stdout_gz else None,
        "stderr_gz": stderr_gz.name if stderr_gz else None,
        "stderr_gz_sha256": sha256_of(stderr_gz) if stderr_gz else None,
        "artifacts_size_bytes": artifacts_size_bytes,
        "image": args.image,
        "machine_type": args.machine_type,
        "batch_job_uid": os.environ.get("BATCH_JOB_UID"),
    }
    write_result_atomic(attempt_dir / "result.json", result)

    if not upload(attempt_dir, leaf_destination):
        return 1

    if exit_code != 0:
        print(f"measure: {args.tool} exited {exit_code}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
