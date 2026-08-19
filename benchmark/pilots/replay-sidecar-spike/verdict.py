#!/usr/bin/env python3
"""Read one spike run's logs and answer: was the server the bottleneck?

The predicate is stated once, here, and evaluated mechanically — a sizing run
that has to be read by eye is a sizing run whose answer moves with whoever reads
it. It consumes the log lines `job.py`'s subject script prints:
`replay_sidecar_cgroup`, `replay_ready`, `replay_metrics label=<before|after>`,
`subject_result`.

    gcloud logging read 'labels."batch.googleapis.com/job_id"="<job>"' \
        --project varve-oss --format='value(textPayload)' --limit 2000 \
      | python3 verdict.py --rows-expected 9919142

`profile(shape)` must be the delay the server was actually told to inject; the
default is `prod-commoncrawl`, which is the only profile the ladder uses.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# The reserved profile's flat base per shape, in milliseconds. A structure
# probe's `/cp` term is deliberately ignored: comparing its latency against the
# base alone can only make the verdict stricter than the truth.
#
# `prod-commoncrawl`'s 223 ms is a placeholder and is known to be the wrong
# number: it is a fleet mean at concurrency 64, not a warm per-request page
# latency, and the real floor over the campaign's own network path has still to
# be measured. It matters more than it looks: demand scales inversely with the
# profile, so a 64-way subject asks ~287 pages/s of the server at 223 ms and
# ~640 pages/s at 100 ms. Sizing against a profile the campaign will not use
# undersizes the server by exactly that ratio.
PROD_COMMONCRAWL_MS = {"worker_page": 223.0, "pivot_probe": 121.0, "structure_probe": 223.0}

# The hard gate is the overrun count: a request that finished inside its
# deadline is invisible to the client whether it took 5 ms or 60 ms, because
# the delay is a deadline and not a surcharge. This fraction only buys margin,
# so that a shape which passes does not sit so close to the profile that the
# next run flips it. It is deliberately loose — the goal is a server that keeps
# out of the way, not a fast server.
HEADROOM_FRACTION = 0.50

METRICS_LINE = re.compile(r"replay_metrics label=(\w+) (\{.*)$")
CGROUP_LINE = re.compile(r"replay_sidecar_cgroup role=(\w+) (.*)$")
READY_LINE = re.compile(r"replay_ready wait_ms=(\d+)")
RESULT_LINE = re.compile(r"subject_result exit=(\S+) rows=(\d+) wall_s=(\S+)")


def subject_report(raw: str) -> dict:
    """swath's own run report, as the subject script cat'd it into the log.

    The server's meters answer "did the backend keep out of the way". They say
    nothing about how fast the subject went, which is the question the whole
    exercise exists to answer -- so both halves are read out of the same run and
    printed together. A rung that improves throughput while the server stays
    invisible is progress; one that improves throughput because the server
    started overrunning is not a result at all.
    """
    start = raw.rfind('{\n  "schema_version"')
    if start < 0:
        marker = raw.find('"schema_version"')
        if marker < 0:
            return {}
        start = raw.rfind("{", 0, marker)
    depth = 0
    for index in range(start, len(raw)):
        if raw[index] == "{":
            depth += 1
        elif raw[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : index + 1])
                except ValueError:
                    return {}
    return {}


def meters(document: dict) -> dict[tuple[str, str], dict]:
    """Every meter keyed by (name, shape-tag); an untagged meter keys on ""."""
    table: dict[tuple[str, str], dict] = {}
    for meter in document.get("meters", ()):
        table[(meter["name"], meter.get("tags", {}).get("shape", ""))] = meter
    return table


def evaluate(after: dict, *, profile: dict[str, float], rows_expected: int, observed: dict) -> int:
    table = meters(after)
    failures: list[str] = []
    voids: list[str] = []

    if observed.get("ready_ms") is None:
        voids.append("the server never reported ready")
    if observed.get("exit") not in ("0", 0):
        voids.append(f"subject exited {observed.get('exit')!r}")
    if rows_expected and observed.get("rows") != rows_expected:
        voids.append(f"row count {observed.get('rows')} != {rows_expected}")
    for role in ("server", "subject"):
        if role not in observed.get("cgroups", {}):
            voids.append(f"no cgroup readback from the {role} container")

    refused = table.get(("swath.replay.serving.refused", ""))
    if refused and refused.get("count", 0) > 0:
        voids.append(f"serving.refused count={refused['count']}")

    print("shape                 n       p50      p99      max   profile  overrun")
    print("-" * 72)
    for shape, budget in profile.items():
        latency = table.get(("swath.replay.request.latency", shape))
        if latency is None or latency.get("count", 0) == 0:
            print(f"{shape:<18} (not exercised)")
            continue
        overrun = table.get(("swath.replay.inject.overrun", shape), {})
        overruns = int(overrun.get("count", 0))
        print(
            f"{shape:<18} {latency['count']:>5} {latency['p50_ms']:>8.1f} "
            f"{latency['p99_ms']:>8.1f} {latency['max_ms']:>8.1f} {budget:>9.0f} {overruns:>8}"
        )
        if overruns:
            failures.append(f"{shape}: {overruns} requests overran the profile")
        if latency["p99_ms"] > HEADROOM_FRACTION * budget:
            failures.append(
                f"{shape}: p99 {latency['p99_ms']:.1f} ms exceeds "
                f"{HEADROOM_FRACTION:.0%} of the {budget:.0f} ms profile"
            )
        # `max_ms` is printed above and deliberately not judged. A request whose
        # server cost exceeded the profile is already an overrun -- the two
        # conditions are the same condition at zero jitter -- and one whose cost
        # merely spiked without reaching the profile was absorbed by the
        # deadline and never reached the client. Judging it too would fail a run
        # for a JIT-warm outlier the measurement is immune to.

    print()
    for role, line in sorted(observed.get("cgroups", {}).items()):
        print(f"cgroup {role}: {line}")
    if observed.get("ready_ms") is not None:
        print(f"index derive + readiness: {observed['ready_ms'] / 1000:.1f} s")
    if observed.get("wall_s"):
        print(f"subject wall: {observed['wall_s']} s, rows {observed.get('rows')}")
    report = observed.get("report") or {}
    if report:
        efficiency = report.get("efficiency", {})
        engine = report.get("engine", {})
        print(
            f"subject: {efficiency.get('keys_per_sec', 0):,.0f} keys/s over "
            f"{report.get('duration_ms', 0) / 1000:.1f} s, "
            f"{engine.get('pages', 0):,} pages "
            f"({engine.get('pages', 0) / max(report.get('duration_ms', 1) / 1000, 1e-9):,.0f}/s), "
            f"in-flight avg {engine.get('avg_in_flight', 0):.1f} of peak "
            f"{engine.get('peak_in_flight', 0)}, "
            f"cpu {efficiency.get('cpu_seconds', 0):.0f}s "
            f"(x{efficiency.get('cpu_efficiency', 0):.2f} cores)"
        )
        # How long the engine took to reach its widest fan-out, against how long
        # the run lasted. swath's concurrency is an AIMD limit, not a fixed
        # width: it starts at min(4, N) permits and climbs. On a bucket small
        # enough to finish before it has climbed, the number measured is the ramp
        # and not the ceiling -- so a rung whose ramp is a large fraction of its
        # duration has outgrown the fixture, and the next rung needs a bigger
        # bucket rather than a bigger machine.
        ramp = engine.get("time_to_peak_in_flight_ms")
        duration = report.get("duration_ms") or 0
        if ramp is not None and duration:
            share = ramp / duration
            note = "  <-- ramp dominates; fixture too small for this width" if share > 0.5 else ""
            print(
                f"         reached peak fan-out after {ramp / 1000:.1f} s "
                f"of a {duration / 1000:.1f} s run ({share:.0%}){note}"
            )

    print()
    if voids:
        print("VOID — this run cannot be read as a sizing result:")
        for reason in voids:
            print(f"  - {reason}")
        return 2
    if failures:
        print("NOT CONVERGED — the server was in the way:")
        for reason in failures:
            print(f"  - {reason}")
        return 1
    print("CONVERGED — every shape stayed inside its budget with no overruns.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows-expected", type=int, default=0)
    parser.add_argument("--log", type=argparse.FileType("r"), default=sys.stdin)
    parser.add_argument(
        "--profile",
        default="",
        help="the injected profile the server actually ran, as "
        "'worker_page=100ms,pivot_probe=60ms,structure_probe=100ms'. "
        "Defaults to prod-commoncrawl's flat bases.",
    )
    args = parser.parse_args()

    profile = dict(PROD_COMMONCRAWL_MS)
    for term in filter(None, args.profile.split(",")):
        shape, _, delay = term.partition("=")
        if shape.strip() not in profile:
            print(f"verdict: unknown shape {shape!r}", file=sys.stderr)
            return 2
        profile[shape.strip()] = float(delay.strip().removesuffix("ms"))

    scrapes: dict[str, dict] = {}
    observed: dict = {"cgroups": {}}
    raw = args.log.read()
    observed["report"] = subject_report(raw)
    for line in raw.splitlines():
        if match := METRICS_LINE.search(line):
            scrapes[match.group(1)] = json.loads(match.group(2))
        elif match := CGROUP_LINE.search(line):
            observed["cgroups"][match.group(1)] = match.group(2).strip()
        elif match := READY_LINE.search(line):
            observed["ready_ms"] = int(match.group(1))
        elif match := RESULT_LINE.search(line):
            observed["exit"] = match.group(1)
            observed["rows"] = int(match.group(2))
            observed["wall_s"] = match.group(3)

    if "after" not in scrapes:
        print("verdict: no post-run metrics scrape in the log; nothing to judge", file=sys.stderr)
        return 2
    return evaluate(
        scrapes["after"],
        profile=profile,
        rows_expected=args.rows_expected,
        observed=observed,
    )


if __name__ == "__main__":
    raise SystemExit(main())
