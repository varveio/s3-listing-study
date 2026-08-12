"""The worker: run one listing tool against one bucket, capture what it did,
and upload the result to GCS.

This is a SKETCH. It stands in for src/s3_listing_study/worker/engine.py +
worker/upload.py (~2,600 lines combined), and trusts the process exit code
and GCP Batch's job status completely. It does NOT do: create-only ("never
overwrite") upload preconditions, TwinStamp evidence sealing, disk sampling,
or SIGTERM/grace-period handling. It DOES keep several properties that are
cheap here and expensive to lose (see README.md's "The minimum rigor we
kept" / "Round 2: purpose-fitness additions"): every invocation uploads to
its own uuid4 leaf under --destination rather than a shared path; result.json
is written atomically and uploaded last, so a leaf missing it is legible as
incomplete; and a bounded pre-upload secret scan of stdout/stderr (see
scan_for_secrets) refuses to upload anything at all on a hit, rather than
publishing captured output that happens to contain credential material.

Usage:
    measure.py --tool aws-cli --bucket some-bucket --prefix "" \\
        --mode s3api-v2-text --output /tmp/attempt --destination gs://bucket/job-id/
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
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

from tools import TOOLS

EXIT_SECRET_DETECTED = 9

SECRET_PATTERNS = {
    "AWS access key id": re.compile(rb"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "aws_secret_access_key assignment": re.compile(rb"aws_secret_access_key\s*=\s*\S+", re.IGNORECASE),
    "GCP private key": re.compile(rb"BEGIN PRIVATE KEY"),
}

SUBJECT_ENV = {
    # Mirrors worker/engine.py's BASE_SUBJECT_ENV: a small, stable
    # environment rather than whatever ambient variables happen to be set
    # in the runner. The real engine also strips secret-carrying variables
    # and declares functional env per tool; this sketch just fixes the
    # basics and disables the EC2 metadata probe every AWS SDK tries first.
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
    Bounded gap, not a full scan: only stdout/stderr are checked here, never
    a file-sink tool's native output (tools.py's "native" key) -- the real
    engine's secret_scan.py scans everything an attempt could publish.
    """
    for path in paths:
        if not path.exists():
            continue
        data = path.read_bytes()
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                return f"{path.name}: {name}"
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one listing tool and upload the attempt.")
    parser.add_argument("--tool", required=True, choices=sorted(TOOLS))
    parser.add_argument("--mode", required=True, help="Recorded in result.json; not used to pick argv.")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="")
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
    return parser.parse_args(argv)


def preflight(argv: list[str]) -> bool:
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
    argv: list[str], attempt_dir: Path, timeout: int, env: dict[str, str]
) -> tuple[int, float, int]:
    """Run argv, capture stdout/stderr to files, return (exit_code, wall_s, max_rss_kb)."""
    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"

    start = time.monotonic()
    with open(stdout_path, "wb") as stdout_f, open(stderr_path, "wb") as stderr_f:
        try:
            proc = subprocess.Popen(argv, stdout=stdout_f, stderr=stderr_f, env=env)
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            exit_code = 124  # conventional timeout exit code
    wall_s = time.monotonic() - start

    # RUSAGE_CHILDREN aggregates every child this interpreter has waited on;
    # fine here because measure.py runs exactly one subject per invocation.
    rusage = resource.getrusage(resource.RUSAGE_CHILDREN)
    max_rss_kb = rusage.ru_maxrss  # KiB on Linux

    return exit_code, wall_s, max_rss_kb


def gzip_file(path: Path) -> Path:
    gz_path = path.with_suffix(path.suffix + ".gz")
    with open(path, "rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return gz_path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def attempt_size_bytes(attempt_dir: Path) -> int:
    """Total bytes under the attempt directory, recorded so a reader can spot
    an oversized native output (e.g. a Parquet sink) without downloading it.
    """
    return sum(f.stat().st_size for f in attempt_dir.rglob("*") if f.is_file())


def write_result_atomic(path: Path, result: dict) -> None:
    """Write result.json via temp-file-then-rename so a reader never observes
    a partially-written marker -- only ever "absent" or "complete".
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(result, indent=2) + "\n")
    os.replace(tmp_path, path)


def _gsutil_cp(sources: list[Path], destination: str) -> bool:
    result = subprocess.run(
        ["gsutil", "-m", "cp", *(str(s) for s in sources), destination],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"measure: upload failed: {result.stderr}", file=sys.stderr)
        return False
    return True


def upload(attempt_dir: Path, destination: str) -> bool:
    """Upload everything except result.json first, then result.json alone,
    last. A leaf whose upload dies between the two steps is left with
    artifacts but no marker -- exactly the shape verify.py treats as
    "incomplete", never as a passing (or failing) verdict.
    """
    artifacts = sorted(p for p in attempt_dir.iterdir() if p.name != "result.json")
    if artifacts and not _gsutil_cp(artifacts, destination):
        return False
    return _gsutil_cp([attempt_dir / "result.json"], destination)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    tool_spec = TOOLS[args.tool]
    command = tool_spec["argv"](args.bucket, args.prefix)

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
    env = {**SUBJECT_ENV, **case_env, **passthrough_env}

    attempt_dir = Path(args.output)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    # Every invocation gets its own leaf: two launches of the same case
    # never contend for the same destination, and there is no "last write
    # wins" to reason about.
    attempt_uuid = str(uuid.uuid4())
    leaf_destination = args.destination.rstrip("/") + "/" + attempt_uuid + "/"

    started_at = datetime.now(UTC).isoformat()
    exit_code, wall_s, max_rss_kb = run_tool(command, attempt_dir, args.timeout, env)
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

    stdout_gz = gzip_file(stdout_path) if stdout_path.exists() else None
    stderr_gz = gzip_file(stderr_path) if stderr_path.exists() else None

    result = {
        "tool": args.tool,
        "mode": args.mode,
        "bucket": args.bucket,
        "prefix": args.prefix,
        "attempt_uuid": attempt_uuid,
        "destination": leaf_destination,
        "argv": command,
        "case_env": case_env,
        "exit_code": exit_code,
        "wall_seconds": round(wall_s, 3),
        "max_rss_kb": max_rss_kb,
        "started_at": started_at,
        "finished_at": finished_at,
        "stdout_size": stdout_size,
        "stderr_size": stderr_size,
        "stdout_gz": stdout_gz.name if stdout_gz else None,
        "stdout_gz_sha256": sha256_of(stdout_gz) if stdout_gz else None,
        "stderr_gz": stderr_gz.name if stderr_gz else None,
        "stderr_gz_sha256": sha256_of(stderr_gz) if stderr_gz else None,
    }
    # Write once to establish the file, then again with its own total size
    # folded in -- attempt_size_bytes() has to walk the directory after
    # result.json exists to account for it too. Both writes are atomic.
    write_result_atomic(attempt_dir / "result.json", result)
    result["attempt_size_bytes"] = attempt_size_bytes(attempt_dir)
    write_result_atomic(attempt_dir / "result.json", result)

    if not upload(attempt_dir, leaf_destination):
        return 1

    if exit_code != 0:
        print(f"measure: {args.tool} exited {exit_code}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
