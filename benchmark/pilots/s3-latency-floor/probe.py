#!/usr/bin/env python3
"""Measure what a real S3 ListObjectsV2 costs, per request shape.

Every threshold in the replay-backed benchmark is stated relative to an injected
latency profile, and that profile has never been measured. The number in use --
`prod-commoncrawl`'s 223 ms -- is a fleet mean at concurrency 64 from one
pathological run, not a warm per-request page latency, and it says nothing at all
about what the *expensive* shapes cost. A rollup on a real bucket does not return
in the same time as a page, which is why the reserved profile carries a
`+55 ms/cp` term; whether either number is right is the question here.

Three shapes, classified exactly as the replay server classifies them:

  worker_page      max-keys=1000, no delimiter   -- the ordinary listing page
  pivot_probe      max-keys=1                    -- a bounded position probe
  structure_probe  delimiter=/                   -- a rollup, whose cost on S3
                                                    grows with the fanout it
                                                    returns

Serial by construction. A per-request cost is what an injected profile injects,
and a concurrent measurement returns a throughput-shaped number instead -- which
is precisely the mistake the 223 ms carries. Concurrency belongs in a separate
measurement of the *ceiling*, not of the floor.

Anonymous, path-agnostic HTTPS against a public bucket, over whatever network
path the runner sits on: the point is to measure the campaign's own path, so this
is meant to run on a Batch VM in the campaign's region, not on a workstation.

Prints one JSON document. No listing data is retained -- only latencies, HTTP
status, and the counts (`KeyCount`, `CommonPrefixes`) each response reported.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KEY_COUNT = re.compile(rb"<KeyCount>(\d+)</KeyCount>")
COMMON_PREFIX = re.compile(rb"<CommonPrefixes>")
NEXT_TOKEN = re.compile(rb"<NextContinuationToken>([^<]+)</NextContinuationToken>")


def request(url: str, timeout: float) -> tuple[float, int, int, int, bytes | None]:
    """One request: elapsed ms, status, KeyCount, CommonPrefixes, continuation."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        body, status = error.read(), error.code
    elapsed = (time.monotonic() - started) * 1000.0
    keys = int(match.group(1)) if (match := KEY_COUNT.search(body)) else 0
    prefixes = len(COMMON_PREFIX.findall(body))
    token = match.group(1) if (match := NEXT_TOKEN.search(body)) else None
    return elapsed, status, keys, prefixes, token


def summarize(samples: list[float]) -> dict:
    ordered = sorted(samples)
    if not ordered:
        return {}
    return {
        "n": len(ordered),
        "mean_ms": round(statistics.fmean(ordered), 1),
        "p50_ms": round(ordered[len(ordered) // 2], 1),
        "p90_ms": round(ordered[int(len(ordered) * 0.90)], 1),
        "p99_ms": round(ordered[min(int(len(ordered) * 0.99), len(ordered) - 1)], 1),
        "max_ms": round(ordered[-1], 1),
    }


def walk(base: str, params: dict, count: int, timeout: float) -> tuple[list[float], list[dict]]:
    """Page forward `count` times, following the continuation token.

    Following the token rather than seeking to scattered `start-after` keys is
    deliberate: it is what every subject under test actually does, and S3's cost
    for a continuation is not obviously the cost of a cold seek.
    """
    samples: list[float] = []
    detail: list[dict] = []
    token: bytes | None = None
    for _ in range(count):
        query = dict(params)
        if token:
            query["continuation-token"] = token.decode()
        url = f"{base}?{urllib.parse.urlencode(query)}"
        elapsed, status, keys, prefixes, token = request(url, timeout)
        samples.append(elapsed)
        detail.append({"ms": round(elapsed, 1), "status": status, "keys": keys, "cp": prefixes})
        if status != 200:
            break
        if token is None:
            break
    return samples, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--count", type=int, default=200, help="requests per shape")
    parser.add_argument("--warmup", type=int, default=10, help="discarded leading requests")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    base = f"https://{args.bucket}.s3.{args.region}.amazonaws.com/"
    shapes = {
        "worker_page": {"list-type": "2", "max-keys": "1000"},
        "pivot_probe": {"list-type": "2", "max-keys": "1"},
        "structure_probe": {"list-type": "2", "delimiter": "/", "max-keys": "1000"},
    }

    document: dict = {
        "bucket": args.bucket,
        "region": args.region,
        "count": args.count,
        "warmup": args.warmup,
        "shapes": {},
    }
    for shape, params in shapes.items():
        samples, detail = walk(base, params, args.count + args.warmup, args.timeout)
        measured = samples[args.warmup :]
        statuses = {entry["status"] for entry in detail}
        document["shapes"][shape] = {
            **summarize(measured),
            "statuses": sorted(statuses),
            # The fanout each request returned, which is what the profile's
            # `/cp` slope has to be fitted against.
            "cp_returned": summarize([float(entry["cp"]) for entry in detail[args.warmup :]]),
            "keys_returned": summarize([float(entry["keys"]) for entry in detail[args.warmup :]]),
        }
        print(f"s3_floor shape={shape} {json.dumps(document['shapes'][shape])}", file=sys.stderr)

    print("s3_latency_floor " + json.dumps(document, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
