# How the instrument works

Most runs in this study do not go to S3. They go to a **replay server**: a
local process that answers `ListObjectsV2` from a staged copy of a real
bucket's key listing, with a fixed latency per request. This page explains
that instrument once, so that each release's report can point here and say
only how that release departed from it.

## Why replay

Running ten tools with high fan-out against one live bucket makes them
compete for the same S3 key-range budget, and the first attempt at that
(August 2026) drew `SlowDown` throttling that belonged to the contention, not
to any tool. Replay gives every tool the same fixture, the same latency, the
same backend and a declared machine allocation, so differences are the
tools'. The price is that replay is a model of S3, not S3. The rest of this
page is about that price.

## What the server does

The server, `swath-replay`, is part of the [Swath](https://github.com/varveio/swath)
repository. It serves path-style `ListObjectsV2` only: no authentication, no
`GetObject`, no versions, listing metadata only. A fixture is a sorted Parquet
copy of a bucket's listing, captured by one Swath run against the live bucket
and identified by digest.

Every request is classified into one of three shapes by its syntax:

| shape | the request | what it returns |
| --- | --- | --- |
| `worker_page` | any ordinary listing request | up to 1,000 objects |
| `pivot_probe` | `max-keys` of 1, no delimiter | one nearby key |
| `structure_probe` | any request with `delimiter=/` | common prefixes plus bare objects |

## What a deadline is

Each shape has a **deadline** in milliseconds, declared in the plan. The
server answers the request from Parquet, then holds the response until the
deadline has elapsed. So:

- the client sees every request of that shape take the same time, the
  deadline, unless the server itself was slower;
- a request the server could not serve within its deadline is an **overrun**,
  and overruns are counted per shape;
- the deadline is a target total time, not a timeout and not a surcharge.

There is no jitter, no tail, no throttling, and no rise under load. That is a
deliberate control choice for screening, and it is the main way replay
differs from S3.

## Where the deadlines come from

Each fixture's deadlines are the median round trip of one request per shape,
measured by the Swath run that captured the fixture, from the same GCP zone
the campaigns run in. The capture runs held 64 to 128 requests in flight, so
the number was checked two ways for client-side and S3-side load effects.

| fixture | region | worker / pivot / structure deadline (ms) | capture concurrency |
| --- | --- | --- | --- |
| `noaa-nws-fourcastnetgfs-pds` | us-east-1 | 85 / 35 / 37 | c64, one CPU |
| `nara-1950-census` | us-east-2 | 86 / 44 / 50 | c128 |
| `noaa-nbm-grib2-pds` | us-east-1 | 87 / 37 / 51 | c128 |
| `aws-public-blockchain` | us-east-2 | 94 / 46 / 55 | c128 |
| `real-changesets` | us-west-2 | 122 / 88 / 88 | c1024, 32 in flight on average |

**Inside the capture.** Swath records, per request, the wait for a pooled
connection, the time to first byte, and the total. In every capture that kept
that breakdown, the median pool wait was a few microseconds and the total was
about 4 ms above the first byte. So the deadline is one request's round trip
with no client-side queue in it. FourCast's worker deadline of 85 ms was set
between Swath's 86.0 and a same-day serial AWS CLI sample's 82 ms.

**Against a serial client on the same bucket.** A client holding 128
requests in flight could load S3 in a way a one-at-a-time client never does,
and Swath's own clock cannot see that. So on 2026-09-02 a serial control was
run from the same zone: 100 unsigned 1,000-key pages, one at a time, over one
keep-alive connection, timed from request sent to last byte.

| fixture | deadline | serial p50 | serial mean | p90 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: |
| FourCast | 85 | 74.5 | 78.8 | 87 | 228 |
| NBM | 87 | 78.0 | 81.0 | 93 | 190 |
| NARA | 86 | 92.3 | 95.6 | 104 | 320 |
| blockchain | 94 | 95.8 | 101.1 | 112 | 423 |

Two deadlines sit 11–14% above the serial median (Virginia) and two sit 2–7%
below it (Ohio). A deadline above the serial median is applied uniformly to
every request; how much wall time it adds depends on each tool's request
count, shapes and concurrency. One below it is optimistic for everyone. Capture load did not
push the Ohio deadlines above what a serial client sees. This control is recorded in the study's working notes, not in a release.

**Against the serial tools' own live runs.** In the August live-S3 pass, the
serial tools' wall time per page, minus their client cost per page as measured
on a no-latency replay, left 77 to 87 ms in `us-east-1` against deadlines of
85 to 87. Their replay wall clocks under the treatment matched their live-S3
wall clocks within a few percent:

| serial tool | live S3, ms per page | replay under 85 ms, ms per page |
| --- | ---: | ---: |
| s5cmd | 108 | 107 |
| rclone (hierarchical) | 108 | 105 |
| aws-cli | 177 to 182 | 179 |

One serial tool does not reproduce: minio-mc's recursive mode asks S3 for
owner information on every page, which S3 serves more slowly, and replay does
not model that. Its live cycle was about 569 ms per page against 114 under
replay. Replay is optimistic for that mode.

## Known skews

| skew | direction | who it touches |
| --- | --- | --- |
| **No rise under load.** A client that pushes S3 above roughly 1,000 pages per second sees higher latency on S3 than replay gives it (the threshold is from Swath's own live runs, recorded in the study's working notes). | favours the fast client | Only Swath can reach that rate. No run in the current release did: the fastest Swath row made about 870 requests per second (a release field), at or below the rate of the same bucket's live capture (from the capture's own summary). |
| **No tail.** Live S3's p99 is 2.5 to 4.5 times its median. Replay has none. | favours everyone; direction between tools depends on their design | all |
| **No throttling.** Replay never returns `SlowDown`. | favours everyone | all; no single-client live run in this study drew throttling (from the runs' own summaries, not a release field) |
| **Priced by syntax.** A `delimiter=/` request draws the structure deadline even when it returns 1,000 plain objects, as it does on a flat namespace. | favours delimiter-paging modes on flat fixtures | rclone's walk, s7cmd's drain |
| **Owner fetch not modelled.** | favours minio-mc's recursive mode | minio-mc |

## Known defects, by release

**2026-09-scale-diagnostics.** On fixtures with many small directories, the
server's sorted-Parquet seek makes a `delimiter=/` rollup far more expensive
than a page, the inverse of S3. Under Swath's page load the server missed the
structure deadline on about half of those requests, so every Swath row on the
three large fixtures failed the study's timing gate. A warmed server changed
nothing, so the cost is per seek, not cold start. The numbers are in the
[release report](../results/2026-09-scale-diagnostics/REPORT.md#the-instrument-in-this-release).

## How a release grades the delivered treatment

Declaring a deadline does not prove the server delivered it. Every
latency-injected run is graded afterwards from the server's own meters:

| grade | meaning |
| --- | --- |
| `TIMING_VALID` | every shape's mean within 110% of its deadline, under 1% overruns, replay CPU not saturated |
| `PRESSURE_DEGRADED` | missed that gate, but no shape over 10% overruns or 125% of its deadline |
| `CAPACITY_FAILED` | a shape beyond those limits, or replay CPU sustained at the ceiling |
| `INSUFFICIENT_EVIDENCE` | too few meters or resource samples to grade |
| `NOT_APPLICABLE` | no latency was injected |

`TIMING_VALID` means the synthetic treatment was delivered as declared. It
does not mean the treatment matches S3. The thresholds are in
[`methodology.md`](methodology.md).
