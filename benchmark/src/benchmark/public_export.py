"""Project the private campaign ledger into one public, allowlisted release.

The private ledger carries the estate this study runs on -- service accounts,
secret resources, provider job names, full provider request JSON, executor
environment, and the result-store prefixes evidence lands under. None of that is
a result, and publishing the ledger would publish all of it. So this exporter
never serializes a ledger row: it *constructs* a public row field by field and
then refuses to emit anything whose key path is not in :data:`ALLOWLIST` or
whose rendered text matches a forbidden pattern. A deny-list applied after
serialization fails open the day a column is added; an allow-list fails closed.

Binding and classification are not reimplemented here. :mod:`benchmark.report`
already binds a ledger row to its `result.json`, refuses evidence whose recorded
identity disagrees with the row, attaches preparation chains, and classifies
replay capacity and delivered timing treatment. This module calls
`report.report_rows` and projects what comes back, so a public row cannot claim
a health the campaign reporting path would not.

Release facts -- status, claim ceiling, what is included, plan attribution --
live in `benchmark/publication/<release>.yaml`, not in this file.

Usage:
    python -m benchmark.public_export --state campaign.db \\
        --release benchmark/publication/2026-09-scale-diagnostics.yaml \\
        --output results --cache-dir .cache/public-export
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from benchmark import gcs, public_render, report
from benchmark import replay as replay_contract
from benchmark.ledger import SCHEMA_VERSION as LEDGER_SCHEMA_VERSION
from benchmark.ledger import STATE_FILENAME, TERMINAL_STATES, attempt_rows, ledger

# Bumped when a committed row's meaning changes. A reader that understands
# version N must not silently reinterpret a file written at N+1.
ROW_SCHEMA_VERSION = 1
EXPORTER_VERSION = 1
# The derivation behind `derived.wall_keys_per_second`: row_count / wall_seconds.
FORMULA_VERSION = 1

SPEC_VERSION = 1

ATTEMPT_DIR = "<attempt-dir>"
REPLAY_ENDPOINT = "<replay-endpoint>"

RELEASE_DIRNAME = "results"


class ExportError(RuntimeError):
    """The release cannot be produced safely from the inputs given."""


# --------------------------------------------------------------------------
# The allowlist
# --------------------------------------------------------------------------

LEAF = "leaf"  # any JSON scalar: str, int, float, bool, or null
LEAF_LIST = "leaf-list"  # a list of scalars
CONFIG_KEY = re.compile(r"\A[a-z][a-z0-9_]{0,39}\Z")
CALL_CLASS = re.compile(r"\A[a-z][a-z0-9_]{0,39}\Z")

# A free-form mapping is still bounded: keys must match a pattern and values
# must be scalars, so a nested private blob cannot ride in under one key.
FreeMap = tuple[str, re.Pattern[str]]
CONFIG_MAP: FreeMap = ("free-map", CONFIG_KEY)
PROBE_MAP = ("probe-map", CALL_CLASS)

ALLOWLIST: Mapping[str, Any] = {
    "schema_version": LEAF,
    "release_id": LEAF,
    "attempt_id": LEAF,
    "case_id": LEAF,
    "attempt": LEAF,
    "group_id": LEAF,
    "source": {
        "repository_commit": LEAF,
        "plan_path": LEAF,
        "plan_sha256": LEAF,
        "ledger_schema_version": LEAF,
        "ledger_suite": LEAF,
        "exporter_version": LEAF,
    },
    "classification": {
        "purpose": LEAF,
        "statistic": LEAF,
        "publication_status": LEAF,
        "capacity_status": LEAF,
        "replay_timing": LEAF,
    },
    "tool": {
        "name": LEAF,
        "version": LEAF,
        "mode": LEAF,
        "product": LEAF,
        "fields": LEAF,
        "concurrency": LEAF,
        "config": CONFIG_MAP,
        "image_digest": LEAF,
        "image_set_sha256": LEAF,
        "tool_slice_sha256": LEAF,
        "platform_sha256": LEAF,
    },
    "invocation": {
        "argv": LEAF_LIST,
        "provenance": LEAF,
        "paths_normalized": LEAF,
        "original_sha256": LEAF,
    },
    "target": {
        "service": LEAF,
        "bucket": LEAF,
        "region": LEAF,
        "prefix": LEAF,
        "auth_role": LEAF,
    },
    "fixture": {
        "id": LEAF,
        "sha256": LEAF,
        "serving_mode": LEAF,
        "metadata_ref": LEAF,
    },
    "replay": {
        "capacity_status": LEAF,
        "serving_mode": LEAF,
        "server_image_digest": LEAF,
        "latency_model": {
            "deadlines_ms": CONFIG_MAP,
            "scale": LEAF,
            "jitter": LEAF,
        },
        "allocation": CONFIG_MAP,
        "evidence_state": LEAF,
        "timing_state": LEAF,
        "timing_reasons": LEAF_LIST,
        "requests": {"before": LEAF, "after": LEAF},
        "errors": {"before": LEAF, "after": LEAF},
    },
    "machine": {
        "type": LEAF,
        "vcpus": LEAF,
        "memory_gb": LEAF,
        "container_memory_gb": LEAF,
        "subject_vcpus": LEAF,
        "executor": LEAF,
        "location": LEAF,
    },
    "state": {
        "provider": LEAF,
        "detail": LEAF,
        "evidence": LEAF,
        "recorded_at": LEAF,
        "settled_at": LEAF,
    },
    "outcome": {
        "subject_exit_code": LEAF,
        "worker_exit_code": LEAF,
        "timed_out": LEAF,
        "row_count": LEAF,
        "wall_seconds": LEAF,
        "listing_seconds": LEAF,
        "max_rss_kb": LEAF,
        "max_rss_floor_kb": LEAF,
        "started_at": LEAF,
        "finished_at": LEAF,
    },
    "derived": {
        "wall_keys_per_second": LEAF,
        "formula_version": LEAF,
    },
    "preparation": {
        "produced_by": LEAF,
        "chain": LEAF_LIST,
        "prep_seconds": LEAF,
        "crosses_group": LEAF,
    },
    "native_summary": {
        "objects": LEAF,
        "listing_duration_ms": LEAF,
        "duration_ms": LEAF,
        "keys_per_sec": LEAF,
        "peak_in_flight": LEAF,
        "api_calls": LEAF,
        "probe_latency": PROBE_MAP,
    },
    "evidence": (
        "list",
        {"kind": LEAF, "sha256": LEAF, "published": LEAF, "reason": LEAF},
    ),
}

# Text that must never reach a generated file, whatever produced it. The names
# are the ledger columns and private locations they stand for; the patterns are
# deliberately wider than the exact column so a near-miss is caught too.
FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ledger column service_account", re.compile(r"service[_-]?account", re.IGNORECASE)),
    ("ledger column secret_resource", re.compile(r"secret[_-]?resource", re.IGNORECASE)),
    ("ledger column job_name", re.compile(r"job[_-]?name", re.IGNORECASE)),
    ("ledger column request_json", re.compile(r"request[_-]?json", re.IGNORECASE)),
    ("ledger column executor_env", re.compile(r"executor[_-]?env", re.IGNORECASE)),
    ("ledger column result_prefix", re.compile(r"result[_-]?prefix", re.IGNORECASE)),
    ("private result-store URI", re.compile(r"gs://")),
    ("home-directory path", re.compile(r"/home/[A-Za-z0-9_]")),
    ("private notes repository", re.compile(r"s3-listing-study-notes")),
    ("service-account address", re.compile(r"iam\.gserviceaccount\.com")),
    ("cloud project", re.compile(r"varve-oss")),
    ("e-mail address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
)


def forbidden_hits(text: str) -> list[str]:
    """Every forbidden pattern this text carries, named by what it stands for."""
    return [name for name, pattern in FORBIDDEN_PATTERNS if pattern.search(text)]


def as_mapping(value: object) -> dict[str, Any]:
    """A mapping, or an empty one. Recorded contracts are not all the same shape."""
    return dict(value) if isinstance(value, Mapping) else {}


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def check_allowlist(value: object, spec: object, path: str) -> None:
    """Refuse any key path the release schema does not declare.

    Built by construction rather than filtered after the fact, so this is a
    second gate rather than the only one -- but it is the gate that fails the
    day someone adds a field to the row builder without adding it here.
    """
    if spec == LEAF:
        if not _is_scalar(value):
            raise ExportError(f"{path}: allowlisted as a scalar, got {type(value).__name__}")
        return
    if spec == LEAF_LIST:
        if not isinstance(value, list) or not all(_is_scalar(item) for item in value):
            raise ExportError(f"{path}: allowlisted as a list of scalars")
        return
    if isinstance(spec, tuple) and spec and spec[0] == "list":
        if not isinstance(value, list):
            raise ExportError(f"{path}: allowlisted as a list")
        for index, item in enumerate(value):
            check_allowlist(item, spec[1], f"{path}[{index}]")
        return
    if isinstance(spec, tuple) and spec and spec[0] in {"free-map", "probe-map"}:
        if value is None:
            return
        if not isinstance(value, dict):
            raise ExportError(f"{path}: allowlisted as a mapping")
        pattern = spec[1]
        for key, item in value.items():
            if not isinstance(key, str) or not pattern.match(key):
                raise ExportError(f"{path}.{key!r}: key is outside the permitted key shape")
            if spec[0] == "probe-map":
                check_allowlist(
                    item,
                    {"count": LEAF, "p50_ms": LEAF, "p90_ms": LEAF, "p99_ms": LEAF},
                    f"{path}.{key}",
                )
            elif not _is_scalar(item):
                raise ExportError(f"{path}.{key}: mapping values must be scalars")
        return
    if isinstance(spec, dict):
        if value is None:
            return
        if not isinstance(value, dict):
            raise ExportError(f"{path}: allowlisted as an object")
        for key, item in value.items():
            if key not in spec:
                raise ExportError(f"{path}.{key}: not in the public allowlist")
            check_allowlist(item, spec[key], f"{path}.{key}" if path else key)
        return
    raise ExportError(f"{path}: malformed allowlist entry")  # pragma: no cover


def vet_row(row: Mapping[str, Any]) -> None:
    """Refuse a row that carries a field or a string the release may not publish."""
    check_allowlist(dict(row), dict(ALLOWLIST), "")
    hits = forbidden_hits(json.dumps(row, ensure_ascii=True, sort_keys=True))
    if hits:
        raise ExportError(
            f"attempt {row.get('attempt_id')!r} would publish {', '.join(sorted(set(hits)))}"
        )


# --------------------------------------------------------------------------
# Evidence cache
# --------------------------------------------------------------------------


@contextmanager
def evidence_cache(directory: Path | None) -> Any:
    """Read every `gs://` object through a content-addressed local cache.

    Two exports of one settled ledger must produce byte-identical files. The
    evidence is already immutable -- an attempt prefix is written once -- so the
    cache buys determinism against a transient read failure as well as speed on
    a re-run. Without a directory this is a no-op and reads go straight out.
    """
    if directory is None:
        yield
        return
    directory.mkdir(parents=True, exist_ok=True)
    download, exists = gcs.download_bytes, gcs.blob_exists

    def cached_download(uri: str) -> bytes:
        path = directory / f"{hashlib.sha256(uri.encode()).hexdigest()}.blob"
        if path.exists():
            raw = path.read_bytes()
            if not raw:
                raise FileNotFoundError(uri)
            return raw
        try:
            raw = download(uri)
        except Exception:
            path.write_bytes(b"")
            raise
        path.write_bytes(raw)
        return raw

    def cached_exists(uri: str) -> bool:
        try:
            return bool(cached_download(uri))
        except Exception:
            return False

    gcs.download_bytes, gcs.blob_exists = cached_download, cached_exists
    try:
        yield
    finally:
        gcs.download_bytes, gcs.blob_exists = download, exists


# --------------------------------------------------------------------------
# Release spec
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseSpec:
    path: Path
    release_id: str
    title: str
    status: str
    claim_ceiling: dict[str, bool]
    include: str
    exclusions: list[dict[str, str]]
    ledger_suite: str
    ledger_schema_version: int
    plans: dict[str, str]


def load_release_spec(path: Path) -> ReleaseSpec:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ExportError(f"release spec {path} is not readable YAML: {exc}") from None
    if not isinstance(document, dict):
        raise ExportError(f"release spec {path} is not a mapping")
    if document.get("spec_version") != SPEC_VERSION:
        raise ExportError(
            f"release spec {path} has spec_version {document.get('spec_version')!r}, "
            f"this reader supports {SPEC_VERSION}"
        )
    if document.get("include") != "all-terminal":
        raise ExportError(
            f"release spec {path} declares include={document.get('include')!r}; this exporter "
            "only implements 'all-terminal', because a selective public release needs a "
            "committed selection rule this code does not have"
        )
    source = document.get("source_ledger") or {}
    return ReleaseSpec(
        path=path,
        release_id=str(document["release_id"]),
        title=str(document["title"]),
        status=str(document["status"]),
        claim_ceiling={str(k): bool(v) for k, v in (document["claim_ceiling"] or {}).items()},
        include=str(document["include"]),
        exclusions=[dict(entry) for entry in (document.get("exclusions") or [])],
        ledger_suite=str(source.get("suite")),
        ledger_schema_version=int(source["schema_version"]),
        plans={str(k): str(v) for k, v in (document.get("plans") or {}).items()},
    )


# --------------------------------------------------------------------------
# Small projections
# --------------------------------------------------------------------------


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def repository_commit(root: Path) -> str:
    """The exporter's own commit: a release states the code that made it."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ExportError(f"cannot read the repository commit: {exc}") from None
    return out.stdout.strip()


def unset(value: object) -> Any:
    """`report`'s "-" sentinel becomes a JSON null.

    An unavailable metric is null, never 0 and never an empty string: a zero
    that means "not measured" is the single most damaging thing this export
    could ship.
    """
    return None if value == "-" else value


def image_digest(uri: object) -> str | None:
    """Only the content digest of an image, never the registry path it sits at."""
    if not isinstance(uri, str) or "@" not in uri:
        return None
    return uri.rsplit("@", 1)[1]


def normalize_argv(argv: Sequence[str]) -> list[str]:
    """Replace the two private locations a recorded argv can carry.

    The attempt working directory and the replay server's loopback endpoint are
    facts about where this ran, not about the command. Everything else is the
    tool-native invocation and is published verbatim.
    """
    normalized = []
    for token in argv:
        # Bounded so the substitution stops at the end of the URL rather than
        # eating the rest of a token that embeds one (rclone's connection string).
        token = re.sub(r"https?://[^\s\"',]+", REPLAY_ENDPOINT, token)
        token = re.sub(r"/tmp/attempt(?=/|\Z)", ATTEMPT_DIR, token)
        normalized.append(token)
    return normalized


def fixture_ref(replay_document: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """`(fixture id, digest)` from a replay contract, without the store URI."""
    if not replay_document:
        return None, None
    backend = as_mapping(replay_document.get("backend"))
    uri = str(backend.get("fixture_uri", ""))
    digest = backend.get("fixture_sha256")
    marker = "/fixtures/"
    identifier = None
    if marker in uri:
        identifier = uri.split(marker, 1)[1].rsplit("/", 1)[0]
    return identifier, (str(digest) if digest else None)


def native_summary_block(summary: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The allowlisted slice of a retained `_swath_summary.json`."""
    if not summary:
        return None
    efficiency = as_mapping(summary.get("efficiency"))
    engine = as_mapping(summary.get("engine"))
    cost = as_mapping(summary.get("cost"))
    probes: dict[str, Any] = {}
    for entry in summary.get("probe_latency") or []:
        if not isinstance(entry, Mapping) or entry.get("phase") != "total":
            continue
        call_class = str(entry.get("call_class", ""))
        if not CALL_CLASS.match(call_class):
            continue
        probes[call_class] = {
            "count": entry.get("count"),
            "p50_ms": entry.get("p50_ms"),
            "p90_ms": entry.get("p90_ms"),
            "p99_ms": entry.get("p99_ms"),
        }
    return {
        "objects": summary.get("objects"),
        "listing_duration_ms": summary.get("listing_duration_ms"),
        "duration_ms": summary.get("duration_ms"),
        "keys_per_sec": efficiency.get("keys_per_sec"),
        "peak_in_flight": engine.get("peak_in_flight"),
        "api_calls": cost.get("api_calls"),
        "probe_latency": probes,
    }


def publication_status(bound: Mapping[str, Any]) -> str:
    """`measurement` only where the campaign's own gate already says so."""
    if report.is_publishable_measurement(dict(bound)) and bound["purpose"] == "measurement":
        return "measurement"
    return str(bound["purpose"])


def wall_keys_per_second(row_count: object, wall_seconds: object) -> float | None:
    if isinstance(row_count, bool) or isinstance(wall_seconds, bool):
        return None
    if not isinstance(row_count, int) or not isinstance(wall_seconds, (int, float)):
        return None
    if wall_seconds <= 0:
        return None
    return round(row_count / wall_seconds, 3)


# --------------------------------------------------------------------------
# One public row
# --------------------------------------------------------------------------


def public_row(
    db_row: sqlite3.Row,
    bound: Mapping[str, Any],
    *,
    spec: ReleaseSpec,
    commit: str,
    plan_digests: Mapping[str, str | None],
    result: Mapping[str, Any] | None,
    native: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = result or {}
    replay_document: dict[str, Any] | None = (
        json.loads(str(db_row["replay"])) if db_row["replay"] is not None else None
    )
    fixture_id, fixture_sha = fixture_ref(replay_document)
    argv = result.get("argv")
    original_argv = list(argv) if isinstance(argv, list) else None
    detail = bound.get("replay_timing_detail")
    evidence = result.get("replay_evidence") if isinstance(result, Mapping) else None
    native_block = native_summary_block(native)
    listing_seconds = (
        round(float(native_block["listing_duration_ms"]) / 1000.0, 6)
        if native_block and isinstance(native_block.get("listing_duration_ms"), (int, float))
        else None
    )
    row_count = unset(bound["row_count"])
    wall_seconds = unset(bound["wall_seconds"])
    plan_path = spec.plans.get(str(db_row["group_id"]))
    row: dict[str, Any] = {
        "schema_version": ROW_SCHEMA_VERSION,
        "release_id": spec.release_id,
        "attempt_id": str(db_row["attempt_id"]),
        "case_id": str(db_row["case_id"]),
        "attempt": int(db_row["attempt"]),
        "group_id": str(db_row["group_id"]),
        "source": {
            "repository_commit": commit,
            "plan_path": plan_path,
            "plan_sha256": plan_digests.get(plan_path) if plan_path else None,
            "ledger_schema_version": spec.ledger_schema_version,
            "ledger_suite": spec.ledger_suite,
            "exporter_version": EXPORTER_VERSION,
        },
        "classification": {
            "purpose": str(db_row["purpose"]),
            "statistic": str(db_row["statistic"]),
            "publication_status": publication_status(bound),
            "capacity_status": unset(bound["capacity_status"]),
            "replay_timing": unset(bound["replay_timing"]),
        },
        "tool": {
            "name": str(db_row["tool"]),
            "version": result.get("tool_version"),
            "mode": unset(bound["mode"]),
            "product": unset(bound["product"]),
            "fields": unset(bound["fields"]),
            "concurrency": unset(bound["concurrency"]),
            "config": json.loads(str(db_row["config"])),
            "image_digest": image_digest(db_row["image_uri"]),
            "image_set_sha256": str(db_row["image_set_sha256"]),
            "tool_slice_sha256": str(db_row["tool_slice_sha256"]),
            "platform_sha256": str(db_row["platform_sha256"]),
        },
        "invocation": {
            "argv": normalize_argv(original_argv) if original_argv is not None else [],
            # `recorded` because this argv came out of the attempt's own
            # result.json. Nothing here is reconstructed from a pinned adapter.
            "provenance": "recorded" if original_argv is not None else "unavailable",
            "paths_normalized": original_argv is not None,
            "original_sha256": (
                sha256_hex(json.dumps(original_argv, ensure_ascii=True).encode())
                if original_argv is not None
                else None
            ),
        },
        "target": {
            "service": "s3",
            "bucket": str(db_row["target_bucket"]),
            "region": str(db_row["target_region"]),
            "prefix": str(db_row["target_prefix"]),
            "auth_role": db_row["auth_role"],
        },
        "fixture": (
            None
            if fixture_id is None
            else {
                "id": fixture_id,
                "sha256": fixture_sha,
                "serving_mode": as_mapping(as_mapping(replay_document).get("backend")).get(
                    "serving_mode"
                ),
                "metadata_ref": f"fixtures.json#{fixture_id}",
            }
        ),
        "replay": _replay_block(replay_document, bound, detail, evidence),
        "machine": {
            "type": str(db_row["machine_type"]),
            "vcpus": int(db_row["vcpus"]),
            "memory_gb": int(db_row["memory_gb"]),
            "container_memory_gb": db_row["container_memory_gb"],
            "subject_vcpus": as_mapping(as_mapping(replay_document).get("allocation")).get(
                "subject_vcpus"
            ),
            "executor": str(db_row["executor"]),
            "location": str(db_row["location"]),
        },
        "state": {
            "provider": str(db_row["state"]),
            "detail": db_row["state_detail"],
            "evidence": str(bound["evidence_state"]),
            "recorded_at": str(db_row["recorded_at"]),
            "settled_at": db_row["settled_at"],
        },
        "outcome": {
            "subject_exit_code": unset(bound["exit"]),
            "worker_exit_code": unset(bound["worker_exit"]),
            "timed_out": result.get("timed_out"),
            "row_count": row_count,
            "wall_seconds": wall_seconds,
            "listing_seconds": listing_seconds,
            "max_rss_kb": unset(bound["max_rss_kb"]),
            "max_rss_floor_kb": unset(bound["max_rss_floor_kb"]),
            "started_at": result.get("started_at"),
            "finished_at": result.get("finished_at"),
        },
        "derived": {
            "wall_keys_per_second": wall_keys_per_second(row_count, wall_seconds),
            "formula_version": FORMULA_VERSION,
        },
        "preparation": (
            None
            if db_row["produced_by"] is None and not bound.get("preparations")
            else {
                "produced_by": db_row["produced_by"],
                "chain": list(bound.get("preparations") or []),
                "prep_seconds": unset(bound["prep_seconds"]),
                "crosses_group": bound.get("crosses_group"),
            }
        ),
        "native_summary": native_block,
        "evidence": _evidence_entries(result, native),
    }
    vet_row(row)
    return row


def _replay_block(
    replay_document: Mapping[str, Any] | None,
    bound: Mapping[str, Any],
    detail: object,
    evidence: object,
) -> dict[str, Any] | None:
    if replay_document is None:
        return None
    backend = as_mapping(replay_document.get("backend"))
    latency = as_mapping(backend.get("latency_model"))
    reasons = detail.get("reasons") if isinstance(detail, Mapping) else None
    return {
        "capacity_status": unset(bound["capacity_status"]),
        "serving_mode": backend.get("serving_mode"),
        "server_image_digest": image_digest(backend.get("server_image_uri")),
        "latency_model": {
            "deadlines_ms": as_mapping(latency.get("deadlines_ms")),
            "scale": latency.get("scale"),
            "jitter": latency.get("jitter"),
        },
        "allocation": as_mapping(replay_document.get("allocation")),
        "evidence_state": unset(bound["replay_state"]),
        "timing_state": unset(bound["replay_timing"]),
        "timing_reasons": [str(reason) for reason in (reasons or [])]
        if isinstance(reasons, list)
        else [],
        "requests": {
            "before": replay_contract.counter_value(
                (evidence or {}).get("before") if isinstance(evidence, Mapping) else None,
                replay_contract.REQUEST_COUNTER,
            ),
            "after": replay_contract.counter_value(
                (evidence or {}).get("after") if isinstance(evidence, Mapping) else None,
                replay_contract.REQUEST_COUNTER,
            ),
        },
        "errors": {
            "before": replay_contract.counter_value(
                (evidence or {}).get("before") if isinstance(evidence, Mapping) else None,
                replay_contract.ERROR_COUNTER,
            ),
            "after": replay_contract.counter_value(
                (evidence or {}).get("after") if isinstance(evidence, Mapping) else None,
                replay_contract.ERROR_COUNTER,
            ),
        },
    }


WITHHELD = "private result store; the public fields are projected into this row"


def _evidence_entries(
    result: Mapping[str, Any] | None, native: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """What evidence exists, named and digested, without saying where it lives."""
    entries: list[dict[str, Any]] = []
    if result:
        entries.append(
            {
                "kind": "result-json",
                "sha256": sha256_hex(
                    json.dumps(result, sort_keys=True, ensure_ascii=True).encode()
                ),
                "published": False,
                "reason": WITHHELD,
            }
        )
        for kind, key in (("stdout-log", "stdout"), ("stderr-log", "stderr")):
            marker = result.get(key)
            if isinstance(marker, Mapping):
                entries.append(
                    {
                        "kind": kind,
                        "sha256": marker.get("sha256"),
                        "published": False,
                        "reason": "subject console output is not published in this release",
                    }
                )
        product = result.get("product")
        if isinstance(product, Mapping):
            entries.append(
                {
                    "kind": "listing-product",
                    "sha256": product.get("sha256"),
                    "published": False,
                    "reason": "listing products are gigabytes of third-party object keys",
                }
            )
    if native:
        entries.append(
            {
                "kind": "swath-native-summary",
                "sha256": sha256_hex(
                    json.dumps(native, sort_keys=True, ensure_ascii=True).encode()
                ),
                "published": False,
                "reason": "allowlisted fields are projected into native_summary",
            }
        )
    return entries


# --------------------------------------------------------------------------
# Release-level files
# --------------------------------------------------------------------------

SUMMARY_COLUMNS = (
    "release_id",
    "workload",
    "tool",
    "version",
    "mode",
    "concurrency",
    "purpose",
    "statistic",
    "publication_status",
    "capacity_status",
    "replay_timing",
    "state",
    "state_detail",
    "subject_exit",
    "worker_exit",
    "row_count",
    "wall_seconds",
    "listing_seconds",
    "wall_keys_per_second",
    "max_rss_kb",
    "max_rss_floor_kb",
    "fixture_id",
    "fixture_objects",
    "replay_requests",
    "replay_errors",
    "machine_type",
    "subject_vcpus",
    "container_memory_gb",
    "group_id",
    "case_id",
    "attempt_id",
)


def _cell(value: object) -> str:
    """A missing metric is an empty cell. Never a zero, never a dash."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def summary_csv(rows: Sequence[Mapping[str, Any]], fixtures: Mapping[str, Any]) -> str:
    """The flat convenience view, generated from the JSONL and nothing else."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(SUMMARY_COLUMNS)
    for row in rows:
        fixture = row.get("fixture") or {}
        replay = row.get("replay") or {}
        writer.writerow(
            [
                _cell(value)
                for value in (
                    row["release_id"],
                    row["target"]["bucket"],
                    row["tool"]["name"],
                    row["tool"]["version"],
                    row["tool"]["mode"],
                    row["tool"]["concurrency"],
                    row["classification"]["purpose"],
                    row["classification"]["statistic"],
                    row["classification"]["publication_status"],
                    row["classification"]["capacity_status"],
                    row["classification"]["replay_timing"],
                    row["state"]["provider"],
                    row["state"]["detail"],
                    row["outcome"]["subject_exit_code"],
                    row["outcome"]["worker_exit_code"],
                    row["outcome"]["row_count"],
                    row["outcome"]["wall_seconds"],
                    row["outcome"]["listing_seconds"],
                    row["derived"]["wall_keys_per_second"],
                    row["outcome"]["max_rss_kb"],
                    row["outcome"]["max_rss_floor_kb"],
                    fixture.get("id"),
                    (fixtures.get(str(fixture.get("id"))) or {}).get("object_count"),
                    (replay.get("requests") or {}).get("after"),
                    (replay.get("errors") or {}).get("after"),
                    row["machine"]["type"],
                    row["machine"]["subject_vcpus"],
                    row["machine"]["container_memory_gb"],
                    row["group_id"],
                    row["case_id"],
                    row["attempt_id"],
                )
            ]
        )
    return buffer.getvalue()


def staged_count(bundle: Mapping[str, Any], report_block: Mapping[str, Any]) -> int | None:
    for value in (bundle.get("distinct_keys"), report_block.get("objects")):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def build_fixtures(
    rows: Sequence[Mapping[str, Any]], *, fetch: Callable[[str], Mapping[str, Any] | None]
) -> dict[str, Any]:
    """One record per replay fixture the release depends on.

    Digests and reproduction metadata, never the fixture itself: the bundles are
    tens of gigabytes of third-party object keys. `availability` says so rather
    than leaving a reader to guess a download exists.
    """
    observed: dict[str, int] = {}
    for row in rows:
        fixture = row.get("fixture")
        count = row["outcome"]["row_count"]
        if fixture and row["state"]["provider"] == "SUCCEEDED" and isinstance(count, int):
            key = str(fixture["id"])
            observed[key] = max(observed.get(key, 0), count)

    catalog: dict[str, Any] = {}
    for row in rows:
        fixture = row.get("fixture")
        if not fixture or fixture["id"] in catalog:
            continue
        identifier = str(fixture["id"])
        staged = fetch(identifier)
        capture = as_mapping(as_mapping(staged).get("capture"))
        report_block = as_mapping(capture.get("report"))
        shape = as_mapping(report_block.get("shape"))
        bundle = as_mapping(as_mapping(staged).get("fixture"))
        latency = as_mapping(as_mapping(staged).get("latency_model"))
        catalog[identifier] = {
            "id": identifier,
            "source": {
                "bucket": as_mapping(as_mapping(staged).get("source")).get("bucket")
                or row["target"]["bucket"],
                "region": as_mapping(as_mapping(staged).get("source")).get("region")
                or row["target"]["region"],
                "prefix": as_mapping(as_mapping(staged).get("source")).get("prefix"),
            },
            # Every fixture in this release was captured on the study host, not
            # by a campaign attempt, so there is no capture attempt to cite.
            "capture_attempt_id": None,
            "captured_at": (staged or {}).get("generated_at"),
            "object_count": staged_count(bundle, report_block) or observed.get(identifier),
            # Where no bundle summary was staged, the count is the largest row
            # count any successful attempt reported off that fixture -- an
            # observation from this release's own data, labelled as one, never a
            # number invented to fill the column.
            "object_count_source": (
                "staged-bundle-summary"
                if staged_count(bundle, report_block)
                else ("observed-row-count" if observed.get(identifier) else None)
            ),
            "manifest_sha256": row["fixture"]["sha256"],
            "capture_tool": "swath" if staged else None,
            "capture_tool_version": as_mapping(shape.get("fingerprint")).get("git_sha"),
            "capture_image_digest": image_digest(capture.get("swath_image")),
            "shape": {
                "distinct_keys": bundle.get("distinct_keys"),
                "duplicate_keys": bundle.get("duplicate_keys"),
                "bytes": bundle.get("bytes"),
                "first_characters": bundle.get("first_characters"),
                "distinct_prefixes_by_depth": bundle.get("distinct_prefixes_by_depth"),
                "mass_skew_gini": shape.get("mass_skew_gini"),
                "alphabet_positions_observed": shape.get("alphabet_positions_observed"),
                "delimiter_fanout_max": as_mapping(shape.get("delimiter_fanout")).get("max"),
                "divergence_depth_histogram": shape.get("divergence_depth_histogram"),
            },
            "serving_mode": row["fixture"]["serving_mode"],
            "latency_model": {
                "deadlines_ms": latency.get("deadlines_ms")
                or as_mapping(as_mapping(row.get("replay")).get("latency_model")).get(
                    "deadlines_ms"
                ),
                "scale": as_mapping(as_mapping(row.get("replay")).get("latency_model")).get(
                    "scale"
                ),
                "jitter": as_mapping(as_mapping(row.get("replay")).get("latency_model")).get(
                    "jitter"
                ),
            },
            "metadata_staged": staged is not None,
            "availability": "not-published",
            "recapture": (
                "Re-list the source bucket with the pinned capture tool at the recorded "
                "version and sorted output; the manifest digest identifies the bundle."
            ),
        }
    return {key: catalog[key] for key in sorted(catalog)}


ROLE_NOTE = (
    "Roles and dispositions are a later layer; this release states only what ran and how it "
    "settled."
)


def build_subjects(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    catalog: dict[str, dict[str, Any]] = {}
    for row in rows:
        tool = row["tool"]
        entry = catalog.setdefault(
            tool["name"],
            {
                "tool": tool["name"],
                "capsule_path": f"tools/{tool['name']}/",
                "versions": set(),
                "modes": set(),
                "image_digests": set(),
                "image_set_sha256": set(),
                "tool_slice_sha256": set(),
                "platform_sha256": set(),
                "attempts": 0,
                "note": ROLE_NOTE,
            },
        )
        entry["attempts"] += 1
        for key, value in (
            ("versions", tool["version"]),
            ("modes", tool["mode"]),
            ("image_digests", tool["image_digest"]),
            ("image_set_sha256", tool["image_set_sha256"]),
            ("tool_slice_sha256", tool["tool_slice_sha256"]),
            ("platform_sha256", tool["platform_sha256"]),
        ):
            if value is not None:
                entry[key].add(value)
    return {
        name: {
            key: (sorted(value) if isinstance(value, set) else value)
            for key, value in catalog[name].items()
        }
        for name in sorted(catalog)
    }


def name_of(value: object) -> str:
    """A tally key. An absent classification is `null`, not the string `None`."""
    return "null" if value is None else str(value)


def counts_by(
    rows: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], str]
) -> dict[str, int]:
    tally: dict[str, int] = {}
    for row in rows:
        name = key(row)
        tally[name] = tally.get(name, 0) + 1
    return {name: tally[name] for name in sorted(tally)}


def json_bytes(document: object) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        for row in rows
    )


def sort_key(row: Mapping[str, Any]) -> tuple[str, str, str, int]:
    """Stable order: workload, tool, case, attempt ordinal."""
    return (
        str(row["target"]["bucket"]),
        str(row["tool"]["name"]),
        str(row["case_id"]),
        int(row["attempt"]),
    )


# --------------------------------------------------------------------------
# The release
# --------------------------------------------------------------------------

DISCLOSURE_NO_MEASUREMENT = (
    "No attempt in this release carries purpose=measurement. Every row is a diagnostic or a "
    "preparation, and no row may be read as a calibrated benchmark result."
)


def _fixture_metadata_uri(fixture_uri: str) -> str:
    return fixture_uri.rsplit("/", 1)[0] + "/fixture.json"


def _load_native_summary(
    db_row: sqlite3.Row, result: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """The Swath run summary, only where the products were actually retained."""
    if not result or db_row["tool"] != "swath":
        return None
    names = result.get("native_files")
    if not isinstance(names, Mapping):
        return None
    for name in sorted(names):
        if str(name).endswith("_swath_summary.json"):
            loaded = report.load_json_at(str(db_row["result_prefix"]), f"native/{name}")
            return loaded[0] if loaded else None
    return None


def _repo_relative(path: Path, repo_root: Path) -> str:
    """A committed path is cited by its repository-relative name, never absolutely."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return resolved.name


@dataclass
class Release:
    spec: ReleaseSpec
    rows: list[dict[str, Any]]
    fixtures: dict[str, Any]
    subjects: dict[str, Any]
    manifest: dict[str, Any]
    skipped: list[str]


def build_release(
    db_rows: Sequence[sqlite3.Row],
    *,
    spec: ReleaseSpec,
    commit: str,
    repo_root: Path,
    adapter_root: str,
) -> Release:
    terminal = [row for row in db_rows if row["state"] in TERMINAL_STATES]
    skipped = sorted(
        str(row["attempt_id"]) for row in db_rows if row["state"] not in TERMINAL_STATES
    )
    bound_rows = report.report_rows(list(terminal), adapter_root=adapter_root)
    bound_by_id = {str(bound["attempt_id"]): bound for bound in bound_rows}

    plan_digests: dict[str, str | None] = {}
    for plan_path in sorted(set(spec.plans.values())):
        candidate = repo_root / plan_path
        plan_digests[plan_path] = (
            sha256_hex(candidate.read_bytes()) if candidate.is_file() else None
        )

    fixture_sources: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for db_row in terminal:
        loaded = report.load_json_at(str(db_row["result_prefix"]), "result.json")
        result = loaded[0] if loaded else None
        native = _load_native_summary(db_row, result)
        row = public_row(
            db_row,
            bound_by_id[str(db_row["attempt_id"])],
            spec=spec,
            commit=commit,
            plan_digests=plan_digests,
            result=result,
            native=native,
        )
        rows.append(row)
        if db_row["replay"] is not None:
            backend = as_mapping(json.loads(str(db_row["replay"])).get("backend"))
            identifier, _ = fixture_ref(json.loads(str(db_row["replay"])))
            if identifier and backend.get("fixture_uri"):
                fixture_sources.setdefault(identifier, str(backend["fixture_uri"]))
    rows.sort(key=sort_key)

    def fetch(identifier: str) -> Mapping[str, Any] | None:
        uri = fixture_sources.get(identifier)
        if uri is None:
            return None
        try:
            return dict(json.loads(gcs.download_bytes(_fixture_metadata_uri(uri))))
        except Exception:
            return None

    fixtures = build_fixtures(rows, fetch=fetch)
    subjects = build_subjects(rows)

    groups = counts_by(rows, lambda row: name_of(row["group_id"]))
    unattributed = sorted(name for name in groups if name not in spec.plans)
    unstaged = sorted(key for key, value in fixtures.items() if not value["metadata_staged"])
    unbound = sorted(
        row["attempt_id"] for row in rows if row["state"]["evidence"] != "RESULT_BOUND"
    )
    with_native = [row["attempt_id"] for row in rows if row["native_summary"]]

    disclosures: list[dict[str, Any]] = [
        {
            "id": "no-measurement-rows",
            "detail": DISCLOSURE_NO_MEASUREMENT,
            "affects": "every row",
        }
        if not any(row["classification"]["publication_status"] == "measurement" for row in rows)
        else {
            "id": "measurement-rows-present",
            "detail": "Some rows passed the campaign's publishable-measurement gate.",
            "affects": "see classification.publication_status",
        },
        {
            "id": "plan-path-not-recorded",
            "detail": (
                "The ledger records no plan path, so plan attribution is declared in the release "
                "spec. Groups with no confident attribution carry source.plan_path = null rather "
                "than a guess."
            ),
            "affects": unattributed,
        },
        {
            "id": "evidence-not-bound",
            "detail": (
                "These attempts have no result.json bound to them, so every outcome metric is "
                "null. The rows are kept: a settled attempt with no evidence is data."
            ),
            "affects": unbound,
        },
        {
            "id": "fixture-metadata-not-staged",
            "detail": (
                "No staged fixture bundle summary was found for these fixtures, so their shape "
                "metrics are null and their object_count is the largest row count a successful "
                "attempt reported off the fixture (object_count_source says which)."
            ),
            "affects": unstaged,
        },
        {
            "id": "native-summary-rarely-retained",
            "detail": (
                "The Swath run summary is only present where the run's products were retained. "
                "Every other Swath row carries native_summary = null."
            ),
            "affects": sorted(with_native),
        },
        {
            "id": "real-s3-rows-are-single-observations",
            "detail": (
                "Rows with replay = null ran against the live bucket, uncontrolled and n=1. They "
                "are observations, not performance measurements."
            ),
            "affects": sorted(row["attempt_id"] for row in rows if row["replay"] is None),
        },
        {
            "id": "non-terminal-attempts-skipped",
            "detail": "Attempts that had not settled when the release was cut are not included.",
            "affects": skipped,
        },
    ]

    manifest = {
        "schema_version": ROW_SCHEMA_VERSION,
        "release_id": spec.release_id,
        "title": spec.title,
        "status": spec.status,
        "claim_ceiling": spec.claim_ceiling,
        "include": spec.include,
        "exclusions": spec.exclusions,
        "source": {
            "repository_commit": commit,
            "release_spec": _repo_relative(spec.path, repo_root),
            "exporter_version": EXPORTER_VERSION,
            "row_schema_version": ROW_SCHEMA_VERSION,
            "formula_version": FORMULA_VERSION,
            "ledger_suite": spec.ledger_suite,
            "ledger_schema_version": spec.ledger_schema_version,
            "reader_ledger_schema_version": LEDGER_SCHEMA_VERSION,
        },
        "groups": [
            {
                "group_id": name,
                "attempts": count,
                "plan_path": spec.plans.get(name),
                "plan_sha256": plan_digests.get(spec.plans.get(name, "")),
            }
            for name, count in groups.items()
        ],
        "fixtures": [
            {"id": key, "sha256": value["manifest_sha256"], "object_count": value["object_count"]}
            for key, value in fixtures.items()
        ],
        "images": {
            "toolbox_image_digests": sorted(
                {row["tool"]["image_digest"] for row in rows if row["tool"]["image_digest"]}
            ),
            "image_set_sha256": sorted({row["tool"]["image_set_sha256"] for row in rows}),
            "replay_server_image_digests": sorted(
                {
                    row["replay"]["server_image_digest"]
                    for row in rows
                    if row["replay"] and row["replay"]["server_image_digest"]
                }
            ),
        },
        "counts": {
            "attempts": len(rows),
            "by_state": counts_by(rows, lambda row: name_of(row["state"]["provider"])),
            "by_purpose": counts_by(rows, lambda row: name_of(row["classification"]["purpose"])),
            "by_publication_status": counts_by(
                rows, lambda row: name_of(row["classification"]["publication_status"])
            ),
            "by_tool": counts_by(rows, lambda row: name_of(row["tool"]["name"])),
            "by_workload": counts_by(rows, lambda row: name_of(row["target"]["bucket"])),
            "by_capacity_status": counts_by(
                rows, lambda row: name_of(row["classification"]["capacity_status"])
            ),
            "by_replay_timing": counts_by(
                rows, lambda row: name_of(row["classification"]["replay_timing"])
            ),
            "skipped_non_terminal": len(skipped),
        },
        "disclosures": [entry for entry in disclosures if entry.get("affects") != []],
        "files": [],
        "data_as_of": max((str(row["state"]["settled_at"] or "") for row in rows), default=""),
        "published_at": None,
    }
    return Release(
        spec=spec,
        rows=rows,
        fixtures=fixtures,
        subjects=subjects,
        manifest=manifest,
        skipped=skipped,
    )


def readme_text(release: Release) -> str:
    return f"""# Public results

Each directory under `results/` is one immutable release of this study's public
result data. A release is generated, never hand-edited.

## Release contract

- **Immutable.** Once a release directory is committed, its files are not
  revised in place. A correction is a new release whose `manifest.json` names
  what it supersedes, plus an erratum note in the superseded release.
- **Canonical versus original.** `attempts.jsonl` is the canonical *public*
  dataset. It is not the original evidence: the campaign ledger, the provider
  request, the per-attempt `result.json`, the console logs and the listing
  products stay private. Every row's `evidence[]` names what exists, with a
  digest and the reason it is not published.
- **Generated.** `summary.csv`, the charts and their CSVs are derived from
  `attempts.jsonl`. Never edit them; regenerate.
- **Bounded claims.** `manifest.json.claim_ceiling` states in machine-readable
  form what the release may be used to claim. Prose and figures are bounded by
  it.

## Files in a release

| File | Role |
| --- | --- |
| `manifest.json` | Identity, status, claim ceiling, commit, counts, checksums, disclosures |
| `attempts.jsonl` | One compact JSON object per attempt — the canonical dataset |
| `summary.csv` | Flat scalar view of the same rows, for spreadsheets |
| `fixtures.json` | Per replay fixture: source, digest, object count, shape, latency, availability |
| `subjects.json` | Per tool: versions, image and slice digests, modes seen, capsule path |
| `charts/*.svg`, `charts/*.csv` | Deterministic figures and the exact rows behind each one |
| `checksums.sha256` | SHA-256 of every other file in the release |

`results/latest.json` points at the most recent release. Durable claims should
cite an immutable release path, not `latest`.

## Schema versions

- Row schema: `{ROW_SCHEMA_VERSION}` (`attempts.jsonl`, field `schema_version`)
- Exporter: `{EXPORTER_VERSION}` (`manifest.json.source.exporter_version`)
- Derived-rate formula: `{FORMULA_VERSION}`
  (`derived.wall_keys_per_second = row_count / wall_seconds`)

A reader that understands version *N* must refuse a file at *N+1* rather than
reinterpret it.

## Nulls, failures, and diagnostics

An unavailable metric is `null` — never `0`, never `"-"`, never an empty
string. Failed, cancelled and evidence-less attempts are rows, not omissions.
`classification.publication_status` never says `measurement` unless the
campaign's own gate says the row is a publishable measurement: `purpose ==
measurement`, `capacity_status == CALIBRATED`, and `replay_timing ==
TIMING_VALID`.

## Correction policy

1. Never rewrite a committed release.
2. Publish a new release with `status: erratum` (or a normal release naming the
   supersession) and record what changed.
3. Add the erratum pointer to the superseded release's manifest in the same
   commit as the new release.

## Regenerating and validating

```
uv run python -m benchmark.public_export \\
    --state <campaign.db> \\
    --release benchmark/publication/{release.spec.release_id}.yaml \\
    --output results
uv run python -m benchmark.public_validate --release-dir results/{release.spec.release_id}
```

The exporter needs the private ledger and the private evidence store. The
validator does not: it reads only the committed files, and CI runs it on every
change.

## Releases

- [`{release.spec.release_id}/`]({release.spec.release_id}/) — {release.spec.title}
  (`{release.spec.status}`)
"""


def render_release(release: Release, output_root: Path, *, chart_spec: Path | None) -> list[Path]:
    """Write every file of the release, then seal it with its own checksums."""
    directory = output_root / release.spec.release_id
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def write(relative: str, payload: bytes) -> None:
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        written.append(path)

    write("attempts.jsonl", jsonl_bytes(release.rows))
    write("summary.csv", summary_csv(release.rows, release.fixtures).encode())
    write("fixtures.json", json_bytes(release.fixtures))
    write("subjects.json", json_bytes(release.subjects))
    if chart_spec is not None:
        written.extend(
            public_render.render_charts(
                chart_spec,
                rows=release.rows,
                output_dir=directory / "charts",
                fixtures=release.fixtures,
            )
        )

    manifest = dict(release.manifest)
    manifest["published_at"] = manifest["published_at"] or manifest["data_as_of"]
    manifest["files"] = [
        {
            "path": path.relative_to(directory).as_posix(),
            "sha256": sha256_hex(path.read_bytes()),
            "bytes": path.stat().st_size,
        }
        for path in sorted(written, key=lambda item: item.relative_to(directory).as_posix())
    ]
    write("manifest.json", json_bytes(manifest))

    checksums = "".join(
        f"{sha256_hex(path.read_bytes())}  {path.relative_to(directory).as_posix()}\n"
        for path in sorted(written, key=lambda item: item.relative_to(directory).as_posix())
    )
    (directory / "checksums.sha256").write_text(checksums)

    (output_root / "latest.json").write_bytes(
        json_bytes(
            {
                "schema_version": ROW_SCHEMA_VERSION,
                "release_id": release.spec.release_id,
                "status": release.spec.status,
                "path": f"{RELEASE_DIRNAME}/{release.spec.release_id}/",
                "source_commit": release.manifest["source"]["repository_commit"],
                "published_at": manifest["published_at"],
            }
        )
    )
    (output_root / "README.md").write_text(readme_text(release))
    return [*written, directory / "checksums.sha256"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export one public result release.")
    parser.add_argument("--state", default=STATE_FILENAME, help="campaign.db path (sqlite3).")
    parser.add_argument("--release", type=Path, required=True, help="release spec YAML.")
    parser.add_argument("--output", type=Path, default=Path(RELEASE_DIRNAME))
    parser.add_argument("--charts", type=Path, default=None, help="chart spec YAML.")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Content-addressed evidence cache; makes a re-export cheap and stable.",
    )
    parser.add_argument("--adapter-root", default=report.DEFAULT_ADAPTER_ROOT)
    parser.add_argument("--repo-root", type=Path, default=Path(report.DEFAULT_ADAPTER_ROOT).parent)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        spec = load_release_spec(args.release)
        with ledger(args.state, readonly=True) as con, evidence_cache(args.cache_dir):
            release = build_release(
                attempt_rows(con),
                spec=spec,
                commit=repository_commit(args.repo_root),
                repo_root=args.repo_root.resolve(),
                adapter_root=args.adapter_root,
            )
        written = render_release(release, args.output, chart_spec=args.charts)
    except (ExportError, OSError, KeyError, ValueError) as exc:
        print(f"public-export: {exc}", file=sys.stderr)
        return 1
    print(
        f"public-export: {len(release.rows)} attempt(s), {len(written)} file(s) under "
        f"{args.output / spec.release_id}"
    )
    if release.skipped:
        print(f"public-export: skipped {len(release.skipped)} non-terminal attempt(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
