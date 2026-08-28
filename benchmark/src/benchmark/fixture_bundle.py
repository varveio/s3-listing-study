"""Build one reproducible sorted replay fixture bundle from a public S3 bucket."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from google.cloud import storage

from benchmark.contract import canonical_json
from benchmark.replay import ReplayError
from benchmark.replay_fixture import fixture_manifest

HINTS_NAME = "s3-fast-list-hints.input"
SUMMARY_NAME = "fixture.json"
README_NAME = "README.md"
IMAGE_RE = re.compile(r"\A[^\s]+@sha256:[0-9a-f]{64}\Z")
SAFE_BUCKET_RE = re.compile(r"\A[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
DEFAULT_READY_TIMEOUT_S = 600


class FixtureBundleError(ValueError):
    """The requested capture cannot produce an evidence-bound fixture bundle."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_image(value: str, name: str) -> str:
    if IMAGE_RE.fullmatch(value) is None:
        raise FixtureBundleError(f"{name} must be an immutable image@sha256 URI")
    return value


def _run_stream(command: Sequence[str], log_path: Path) -> None:
    """Run one command, retaining and echoing its combined byte stream."""
    with log_path.open("xb") as sink:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        source = process.stdout
        if source is None:
            raise FixtureBundleError("capture process has no output pipe")
        for chunk in iter(source.readline, b""):
            sink.write(chunk)
            sink.flush()
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        return_code = process.wait()
    if return_code != 0:
        raise FixtureBundleError(f"capture exited {return_code}; see {log_path}")


def _capture_command(args: argparse.Namespace, output: Path) -> tuple[str, ...]:
    target = f"s3://{args.bucket}"
    if args.prefix:
        target += "/" + args.prefix.lstrip("/")
    memory = f"{args.memory_gb}g"
    container_name = "fixture-" + re.sub(r"[^a-z0-9-]", "-", args.bucket)[:38]
    return (
        "docker",
        "run",
        "--rm",
        "--pull=never",
        f"--name={container_name}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        f"--cpuset-cpus={args.cpuset}",
        f"--memory={memory}",
        f"--memory-swap={memory}",
        f"--volume={output}:/evidence",
        args.swath_image,
        "list",
        target,
        "--no-sign-request",
        "--region",
        args.region,
        "--concurrency",
        str(args.concurrency),
        "--format",
        "parquet",
        "--sort",
        "-o",
        "/evidence/dataset",
        "--report",
        "/evidence/report/report.json",
        "--progress",
        "--progress-interval",
        "30s",
    )


def _generator_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    path = root / "tools/s3-fast-list/adapter/fixture_hints.py"
    if not path.is_file():
        raise FixtureBundleError(f"fixture hints generator is missing: {path}")
    return path


def _harness_revision() -> str:
    root = Path(__file__).resolve().parents[3]
    status = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    if status.stdout:
        raise FixtureBundleError("fixture capture requires a clean repository checkout")
    revision = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise FixtureBundleError("could not resolve the fixture builder revision")
    return revision


def _generate_hints(data_dir: Path, output: Path, segments: int) -> dict[str, object]:
    done = subprocess.run(
        (
            sys.executable,
            str(_generator_path()),
            "--fixture",
            str(data_dir),
            "--segments",
            str(segments),
            "--output",
            str(output),
        ),
        check=False,
        text=True,
        capture_output=True,
    )
    if done.returncode != 0:
        raise FixtureBundleError(done.stderr.strip() or "fixture hints generation failed")
    try:
        summary = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise FixtureBundleError("fixture hints generator returned malformed JSON") from exc
    if not isinstance(summary, dict) or summary.get("sha256") != _sha256_file(output):
        raise FixtureBundleError("fixture hints summary does not bind its output")
    return {str(key): value for key, value in summary.items()}


def _fixture_analysis(data_dir: Path) -> dict[str, object]:
    paths = sorted(data_dir.glob("*.parquet"))
    with duckdb.connect() as connection:
        connection.execute("SET threads=8")
        connection.from_parquet([str(path) for path in paths]).create_view("fixture_source")
        columns = {
            str(name): str(kind)
            for _, name, kind, *_ in connection.execute(
                "PRAGMA table_info('fixture_source')"
            ).fetchall()
        }
        if columns.get("key") != "BLOB" or columns.get("row_type") != "VARCHAR":
            raise FixtureBundleError("fixture must expose key BLOB and row_type VARCHAR columns")
        counts = connection.execute(
            """
            SELECT count(*), count(DISTINCT key),
                   count(*) FILTER (WHERE row_type <> 'OBJECT'),
                   count(*) FILTER (WHERE right(decode(key), 1) = '/'),
                   min(decode(key)), max(decode(key))
            FROM fixture_source
            """
        ).fetchone()
        assert counts is not None
        row_types = connection.execute(
            "SELECT row_type, count(*) FROM fixture_source GROUP BY 1 ORDER BY 1"
        ).fetchall()
        first_character_row = connection.execute(
            """
            SELECT string_agg(value, '' ORDER BY value)
            FROM (SELECT DISTINCT substr(decode(key), 1, 1) AS value FROM fixture_source)
            """
        ).fetchone()
        if first_character_row is None:
            raise FixtureBundleError("fixture has no first-character observation")
        first_characters = first_character_row[0]
        depths: dict[str, int] = {}
        for depth in (1, 2, 3):
            expression = " || '/' || ".join(
                f"split_part(decode(key), '/', {index})" for index in range(1, depth + 1)
            )
            depth_row = connection.execute(
                f"SELECT count(DISTINCT {expression}) FROM fixture_source"
            ).fetchone()
            if depth_row is None:
                raise FixtureBundleError(f"fixture has no depth-{depth} observation")
            depths[str(depth)] = int(depth_row[0])
        top_level = [
            {"prefix": str(prefix), "objects": int(objects)}
            for prefix, objects in connection.execute(
                """
                SELECT split_part(decode(key), '/', 1), count(*)
                FROM fixture_source GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 20
                """
            ).fetchall()
        ]
    return {
        "rows": int(counts[0]),
        "distinct_keys": int(counts[1]),
        "duplicate_keys": int(counts[0]) - int(counts[1]),
        "non_object_rows": int(counts[2]),
        "slash_markers": int(counts[3]),
        "min_key": str(counts[4]),
        "max_key": str(counts[5]),
        "row_types": {str(kind): int(count) for kind, count in row_types},
        "first_characters": str(first_characters),
        "distinct_prefixes_by_depth": depths,
        "largest_root_prefixes": top_level,
    }


def _latency_profile(report: Mapping[str, object]) -> dict[str, object]:
    observations = report.get("probe_latency")
    if not isinstance(observations, list):
        raise FixtureBundleError("Swath report has no probe_latency list")
    totals: dict[str, dict[str, object]] = {}
    for item in observations:
        if not isinstance(item, dict) or item.get("phase") != "total":
            continue
        call_class = item.get("call_class")
        if isinstance(call_class, str):
            totals[call_class] = {str(key): value for key, value in item.items()}
    required = ("worker_page", "pivot_probe", "structure_probe")
    if any(name not in totals for name in required):
        raise FixtureBundleError("Swath report is missing a total probe-latency class")
    deadlines: dict[str, int] = {}
    for name in required:
        p50 = totals[name].get("p50_ms")
        if isinstance(p50, bool) or not isinstance(p50, (int, float)):
            raise FixtureBundleError(f"Swath report has invalid {name} p50 latency")
        deadlines[name] = round(float(p50))
    return {"deadlines_ms": deadlines, "observations": totals, "scale": 1.0, "jitter": "none"}


def _wait_for_health(url: str, timeout_s: int) -> dict[str, object]:
    started = time.monotonic()
    attempts = 0
    last_error = ""
    while time.monotonic() - started < timeout_s:
        attempts += 1
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    response.read()
                    return {
                        "state": "ready",
                        "wait_ms": round((time.monotonic() - started) * 1000),
                        "attempts": attempts,
                    }
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise FixtureBundleError(f"replay sorted validation did not become ready: {last_error}")


def _validate_sorted(
    args: argparse.Namespace, data_dir: Path, latency: Mapping[str, object], log_path: Path
) -> dict[str, object]:
    deadlines = latency["deadlines_ms"]
    assert isinstance(deadlines, Mapping)
    profile = ",".join(
        f"{name}={int(deadlines[name])}ms"
        for name in ("worker_page", "pivot_probe", "structure_probe")
    )
    name = "fixture-validation-" + re.sub(r"[^a-z0-9-]", "-", args.bucket)[:34]
    volume = f"{data_dir}:/fixture:ro"
    command = (
        "docker",
        "run",
        "--rm",
        "-d",
        f"--name={name}",
        "--network=host",
        f"--memory={args.replay_memory_gb}g",
        f"--memory-swap={args.replay_memory_gb}g",
        f"--volume={volume}",
        args.replay_image,
        "serve",
        "--fixture",
        "/fixture",
        "--bucket",
        args.bucket,
        "--host",
        "127.0.0.1",
        "--port",
        str(args.validation_port),
        "--metrics-port",
        str(args.validation_metrics_port),
        "--serving-mode",
        "sorted",
        "--parquet-connections",
        "64",
        "--max-concurrent-requests",
        "512",
        "--inject-latency",
        profile,
        "--latency-scale",
        "1.0",
    )
    started = subprocess.run(command, check=False, text=True, capture_output=True)
    if started.returncode != 0:
        raise FixtureBundleError(started.stderr.strip() or "could not start replay validation")
    readiness: dict[str, object]
    try:
        readiness = _wait_for_health(
            f"http://127.0.0.1:{args.validation_metrics_port}/healthz",
            args.ready_timeout_s,
        )
        logs = subprocess.run(
            ("docker", "logs", name), check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        log_path.write_bytes(logs.stdout)
    finally:
        subprocess.run(("docker", "stop", "-t", "10", name), check=False, capture_output=True)
    return {"serving_mode": "sorted", "readiness": readiness, "log": log_path.name}


def _gcs_parts(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise FixtureBundleError("--gcs-prefix must begin with gs://")
    bucket, separator, prefix = uri[5:].partition("/")
    if not bucket or not separator or not prefix.strip("/"):
        raise FixtureBundleError("--gcs-prefix must include a bucket and object prefix")
    return bucket, prefix.strip("/")


def _upload_bundle(uri: str, paths: Iterable[Path]) -> list[str]:
    bucket_name, prefix = _gcs_parts(uri)
    bucket = storage.Client().bucket(bucket_name)
    uploaded: list[str] = []
    for path in paths:
        object_name = f"{prefix}/{path.name}"
        try:
            bucket.blob(object_name).upload_from_filename(path, if_generation_match=0)
        except Exception as exc:
            target = f"gs://{bucket_name}/{object_name}"
            raise FixtureBundleError(f"create-only upload failed for {target}: {exc}") from exc
        uploaded.append(f"gs://{bucket_name}/{object_name}")
    return uploaded


def _read_report(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise FixtureBundleError(f"Swath report is malformed: {path}") from exc
    if (
        not isinstance(value, dict)
        or value.get("completed") is not True
        or value.get("exit_code") != 0
    ):
        raise FixtureBundleError("Swath report does not describe a completed exit-zero capture")
    return {str(key): item for key, item in value.items()}


def _render_readme(summary: Mapping[str, object]) -> str:
    def mapping(name: str) -> Mapping[str, object]:
        value = summary[name]
        if not isinstance(value, Mapping):
            raise FixtureBundleError(f"summary field {name!r} is not an object")
        return value

    source = mapping("source")
    fixture = mapping("fixture")
    hints = mapping("s3_fast_list_hints")
    latency = mapping("latency_model")
    capture = mapping("capture")
    return f"""# Replay fixture bundle

Generated by `python -m benchmark.fixture_bundle`; this directory is evidence,
not a benchmark result.

- Source: `s3://{source["bucket"]}` in `{source["region"]}`
- Rows / distinct keys: {fixture["rows"]:,} / {fixture["distinct_keys"]:,}
- Fixture digest: `{fixture["sha256"]}`
- Sorted Parquet: {fixture["files"]} part(s), {fixture["bytes"]:,} bytes
- s3-fast-list hints: `{HINTS_NAME}`, {hints["cut_points"]} cuts / {hints["ranges"]} ranges,
  SHA-256 `{hints["sha256"]}`
- Replay latency deadlines: `{latency["deadlines_ms"]}`
- Swath image: `{capture["swath_image"]}`
- Replay validation image: `{capture["replay_image"]}`
- Fixture builder revision: `{capture["harness_revision"]}`

The hints are a deterministic companion input generated from the exact Parquet
parts at the fixed segment count in `fixture.json`. Upload is create-only; a
replay plan addresses the Parquet parts as `part-*.parquet` and the replay-only
s3-fast-list mode reads `{HINTS_NAME}` from the same staged directory.
"""


def build_bundle(args: argparse.Namespace) -> dict[str, object]:
    harness_revision = _harness_revision()
    output = args.output.resolve()
    if output.exists():
        raise FixtureBundleError(f"refusing to reuse existing output directory {output}")
    output.mkdir(parents=True)
    dataset = output / "dataset"
    report_dir = output / "report"
    dataset.mkdir(mode=0o777)
    report_dir.mkdir(mode=0o777)
    dataset.chmod(0o777)
    report_dir.chmod(0o777)
    command = _capture_command(args, output)
    _run_stream(command, output / "capture.log")

    report = _read_report(report_dir / "report.json")
    data_dir = dataset / "data"
    manifest_sha256, manifest_rows = fixture_manifest(data_dir)
    analysis = _fixture_analysis(data_dir)
    if analysis["rows"] != report.get("objects"):
        raise FixtureBundleError(
            f"fixture has {analysis['rows']} rows but Swath reported {report.get('objects')}"
        )
    hints_path = output / HINTS_NAME
    hints = _generate_hints(data_dir, hints_path, args.segments)
    latency = _latency_profile(report)
    validation = _validate_sorted(args, data_dir, latency, output / "sorted-validation.log")
    parquet_paths = sorted(data_dir.glob("*.parquet"))
    gcs_prefix = args.gcs_prefix.rstrip("/") if args.gcs_prefix else None
    summary: dict[str, object] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {"bucket": args.bucket, "region": args.region, "prefix": args.prefix},
        "capture": {
            "swath_image": args.swath_image,
            "replay_image": args.replay_image,
            "harness_revision": harness_revision,
            "command": list(command),
            "cpuset": args.cpuset,
            "memory_gb": args.memory_gb,
            "concurrency": args.concurrency,
            "host": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "cpu_count": os.cpu_count(),
            },
            "report": report,
        },
        "fixture": {
            **analysis,
            "sha256": manifest_sha256,
            "manifest_rows": list(manifest_rows),
            "files": len(parquet_paths),
            "bytes": sum(path.stat().st_size for path in parquet_paths),
        },
        "s3_fast_list_hints": {**hints, "segments": args.segments, "name": HINTS_NAME},
        "latency_model": latency,
        "sorted_validation": validation,
        "gcs_prefix": gcs_prefix,
    }
    summary_path = output / SUMMARY_NAME
    readme_path = output / README_NAME
    summary_path.write_text(canonical_json(summary) + "\n")
    readme_path.write_text(_render_readme(summary))
    if gcs_prefix is not None:
        _upload_bundle(
            gcs_prefix,
            (*parquet_paths, hints_path, summary_path, readme_path),
        )
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture, inspect, validate, and optionally upload one replay fixture bundle."
    )
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--swath-image", required=True)
    parser.add_argument("--replay-image", required=True)
    parser.add_argument("--cpuset", required=True)
    parser.add_argument("--memory-gb", type=int, default=16)
    parser.add_argument("--replay-memory-gb", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=128)
    parser.add_argument("--segments", type=int, default=1000)
    parser.add_argument("--gcs-prefix")
    parser.add_argument("--validation-port", type=int, default=29090)
    parser.add_argument("--validation-metrics-port", type=int, default=29192)
    parser.add_argument("--ready-timeout-s", type=int, default=DEFAULT_READY_TIMEOUT_S)
    args = parser.parse_args(argv)
    if SAFE_BUCKET_RE.fullmatch(args.bucket) is None:
        parser.error("--bucket is not a valid DNS-style S3 bucket name")
    for name in ("memory_gb", "replay_memory_gb", "concurrency", "segments", "ready_timeout_s"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    try:
        args.swath_image = _require_image(args.swath_image, "--swath-image")
        args.replay_image = _require_image(args.replay_image, "--replay-image")
    except FixtureBundleError as exc:
        parser.error(str(exc))
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = build_bundle(args)
    except (FixtureBundleError, ReplayError, OSError, duckdb.Error) as exc:
        print(f"fixture-bundle: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
