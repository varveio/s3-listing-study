# Benchmark plans

One file per bucket under [`buckets/`](buckets/), saying what to run against
that bucket and on what box.

```
python -m benchmark.plan_cli --bucket noaa-ghcn-pds
```

expands a plan and prints every case it generates. It contacts nothing and
writes nothing, so it is the way to review a campaign before submitting one.

## A plan is not a registry entry

`data/registry.toml` binds a bucket to a reference manifest for the
verification lineage. Nothing in the attempt path reads it, and a benchmark
bucket has no manifest, so a plan is self-contained: bucket, region, roster,
allocation, and cases all live in the one file.

## A plan is intent; a campaign is an execution

A campaign runs one or more plans with its image set frozen, and receipts group
under the campaign that produced them. That is why a plan carries no campaign
ID, no image digest, and no date.

See [`../README.md`](../README.md) for campaign submission, reconciliation, and
reporting. The campaign's SQLite ledger is authoritative controller state and
must be retained and backed up; it is not interchangeable with result evidence.

The current benchmark policy is `reps: 1`: one scheduled run per case, on one
fresh Batch VM with one task. The plan schema retains `reps` as an explicit
schedule field, but published campaigns do not raise it without a dated
methodology change. There are no cold/warm arms.

## Most tools just run

A tool that runs once, at its usual mode, on the plan's own allocation says
nothing a plan needs to spell out. Writing the name and stopping is the whole
declaration:

```yaml
tools:
  aws-cli:
  s5cmd:
  rclone:
```

The mode each one runs lives in [`tools.yaml`](tools.yaml), because a tool's
representative mode is a fact about the tool rather than about any one bucket.
Restating it per plan would mean the same eleven lines in every file, drifting
apart one edit at a time. A test checks each default against the adapter that
implements it, so an adapter rename cannot leave it stale.

Losing a level of indentation on a `cases:` makes it a sibling of the tool
rather than its body. That is refused — as an unregistered tool with no default
mode — rather than quietly running the tool once.

## A layer and a row

A plan has two shapes, and every key in it belongs to one of them.

A **row** — one entry in a tool's `cases` — states what one case *is*: `mode`,
`signed`, and the allocation (`vcpus`, `memory_gb`, `container_memory_gb`).

A **layer** — `defaults`, or a tool's own body — states what every case under it
*inherits*: `signed` and the allocation again, plus the schedule (`reps`,
`timeout_s`). Never `mode`: eleven tools have eleven mode vocabularies, so
nothing above a row has a mode to state. A tool body is therefore `defaults`
plus `cases`.

A row carries only what the ID and the fingerprint can *both* see, which is what
keeps `timeout_s` out of one: it is in the fingerprint but not the ID, so two
rows differing only there would render one ID and two fingerprints — two
non-comparable runs filed into one case directory.

### Signing is the capsule's fact, not the plan's preference

Whether a request is signed says nothing about whether the bucket is private —
every target here is public. It is a fact about the subject: four of the eleven
tools have no unsigned request path, and one (minio-mc) resolves credentials
from a static alias and cannot carry a per-request one. So each capsule declares
what it can issue, and the plan does not get to overrule it:

- no unsigned path → the case signs;
- cannot sign → the case lists unsigned;
- both available → **unsigned**, unless a row or layer says `signed: true`.

Unsigned is the default for a tool that can do either because signing adds a
signature to every one of roughly a thousand requests, which is a different
measurement — and the cheaper one is the better baseline. A `signed:` that
contradicts what the capsule declared is refused rather than ignored, which is
the failure this replaced: six attempts in the first campaign recorded
`authenticated` and ran unsigned.

A signing case needs an identity, not a flag, because it runs under the service
account that may read the credential. The plan states one top-level `auth_role`
naming it — today `public-read`, matching `aws-s3-public-read-user` in the
estate. A plan whose roster resolves any case to signing without an `auth_role`
is refused.

## Cases are an ordered union

Each entry in `cases` is either one literal row or an explicit `product`
generator. Entries form an ordered union and each generator expands in place.
Literal rows stay the direct way to describe ragged cases:

```yaml
swath:
  cases:
    - {mode: recursive-tsv, container_memory_gb: 4}
    - {mode: recursive-parquet, container_memory_gb: 4}
    - {mode: recursive-parquet-sorted, container_memory_gb: 2}
    - {mode: recursive-parquet-sorted, container_memory_gb: 4}
```

Rows are ragged: a row states what differs and inherits the rest, which is how
the one mode that cares about memory gets swept without its siblings restating
an allocation they were happy with. A row may even omit `mode`, taking the tool's
usual one, so a sweep over allocation alone is one line per case:

```yaml
s5cmd:
  cases:
    - {vcpus: 2}
    - {vcpus: 8}
```

Values resolve in three shallow layers, nearest statement winning:
`defaults` → the tool → the row. Every level is a flat table of scalars, so
there is no nesting for a merge surprise to hide in.

Use `product` when independent axes should multiply. Its row-field values are
non-empty lists; a list on a literal row remains invalid, so multiplication is
never inferred from YAML type:

```yaml
swath:
  cases:
    - {mode: recursive-tsv}
    - product:
        mode: [recursive-parquet, recursive-parquet-sorted]
        zip:
          - {vcpus: 2, memory_gb: 4, container_memory_gb: 2}
          - {vcpus: 2, memory_gb: 4, container_memory_gb: 4}
          - {vcpus: 4, memory_gb: 8, container_memory_gb: 8}
```

`zip` is one optional correlated factor inside a product. It is a non-empty
list of atomic mappings with the same two or more row fields. The example
therefore asks for 2 and 4 GiB ceilings on the `2×4` machine, but only the 8 GiB
ceiling on the `4×8` machine; it never manufactures a `4×8` case with a low
ceiling. Zipped fields cannot also be independent axes. Unknown fields,
inconsistent zip mappings, duplicate zip choices, empty axes, and duplicate
resolved cases are refused.

Expansion order is deterministic and does not depend on YAML mapping order.
Zip choices are the outermost factor. Independent axes follow in canonical row
field order (`mode`, `signed`, `vcpus`, `memory_gb`,
`container_memory_gb`), with the rightmost advancing fastest. In the example,
each zipped allocation contains both modes. Expansion happens before the
ordinary three-layer inheritance, so an omitted generator field inherits
exactly as it does in a literal row. A value in an atomic zip row is still a row
value: the `container_memory_gb: 8` choice above overrides a lower global
default for just those expanded cases.

This is authoring sugar within spec v2: resolved case IDs, fingerprints, and
campaign attempts contain ordinary rows, never the generator structure. There
is still no plan-level sweep — one `defaults` row and a list of them mean the
same thing at one entry and diverge silently at the second, so a list there is
refused.

## A plan asks for a shape, not a machine type

A layer or a row states `vcpus` and `memory_gb`; [`instances.yaml`](instances.yaml)
says which machine type that pair is. A plan therefore never names a provider's
catalogue, and a new machine generation is one edit there rather than one per
case. A shape the catalogue does not offer is refused while resolving, rather
than when Batch rejects the job.

## The box and the process are different questions

`vcpus`/`memory_gb` buy a machine. `container_memory_gb` is a ceiling on top of
it — a real cgroup limit, passed as `docker run --memory` (Batch takes extra
docker flags through a container runnable's `options`). It is the only figure
here a running program is known to feel: Cloud Batch documents its per-task
`memoryMib` as a scheduling input — machine-type compatibility, and how many
tasks share a VM — and says nothing about enforcing it at runtime. Treat that
as undocumented rather than settled until one throwaway job confirms it.

So a memory sweep should move the ceiling, not the machine:

```yaml
swath:
  cases:
    - {mode: recursive-parquet-sorted, container_memory_gb: 2}
    - {mode: recursive-parquet-sorted, container_memory_gb: 4}
```

The declared shape stays identical across those two cases — same machine type,
cores, and memory, with each attempt alone on a fresh VM — and the ceiling
reaches sizes no machine type sells. Omitting
`container_memory_gb` means no ceiling: the container sees the whole box. A
ceiling larger than the box is refused, since it would constrain nothing.

How much of that a managed runtime may use as heap is set once, in
[`tools.yaml`](tools.yaml) beside the policies it configures — **not** in a
plan. Only swath's JVM and s3p's V8 are told; the Go, Rust and Python tools have
no such ceiling, so a per-bucket setting would be a knob nine cases in eleven
ignore and every plan restates. It matters at all because both runtimes default
to a *fraction* of the memory they can see, so leaving them alone would make the
runtime own heuristic the independent variable rather than the memory the case
asked for.

Every tool with a `build/image.json` must appear under `tools` or `exclude`
with a reason. A tool that is simply absent is a validation error — registering
a subject and forgetting a bucket should not look like a decision to skip it.

## Case IDs are paths, not identities

An ID is derived (`recursive-parquet-sorted.container_memory_gb-2`) from the
*union* of the keys a tool's rows state, so a ragged row set still gives that
tool IDs of one shape: a row that omitted a key renders the value it inherited,
and `container_memory_gb-none` is the ceiling nobody set.

Because the union is what renders, adding a key to one row changes every ID that
tool generates. Identity is therefore carried by `fingerprint`, a digest over
the resolved case. It survives an ID scheme change, and it refuses the reverse
mistake: editing a row's value while the derived ID lands the same would
otherwise append non-comparable runs into one case directory.

`reps` is excluded from the fingerprint — how many times we ran something is
not part of what we ran. `timeout_s` is included, because it can truncate a run
and change the result.

## Scheduled jobs and execution UUIDs are separate identities

The campaign model gives each scheduled run a stable job ID and a `run-<n>`
ordinal. That ordinal is separate from the worker's attempt UUID: current
`reps: 1` produces `run-1`; higher ordinals are reserved for separately
scheduled runs, not an implemented append-later rerun command. Every actual
worker-container execution independently mints an attempt UUID; `attempt_uuid` is
therefore per execution, never the scheduled run identity. Every execution owns
one authoritative tree:

```text
campaigns/<campaign>/results/<bucket>/<tool>/<case>/run-<n>/submission-<n>/<attempt-uuid>/
  result.json
  stdout.log.gz
  stderr.log.gz
  native/...
```

Neither the campaign run ordinal, submission number, nor execution UUID is
folded into the case or attempt fingerprint; those hashes remain content-derived
descriptions of what ran, while the path components say which scheduled run,
submission, and execution produced the evidence.

Raw artifacts upload first and `result.json` uploads last. Uploads are ordinary
object writes, not create-only writes. Fresh UUID attempt prefixes and numbered
submission prefixes reduce accidental collisions, but they do not seal evidence
against replacement by credentials with broader permissions. See
[`Evidence publication is not sealed`](../README.md#evidence-publication-is-not-sealed)
for the exact limitation. Job-name idempotence and resubmission numbering are
campaign-controller concerns.

The campaign sets automatic Batch retries to 0. Even so, its trust model does
not assume a scheduled job and a worker execution are one-to-one. For each
campaign-known submission prefix, the benchmark controller uses a GCS
delimiter listing to discover only its immediate UUID children, without
descending into or downloading raw artifacts, then GET the exact `result.json`
from each child. More than one child under one submission is a duplicate-execution
anomaly; reporting surfaces every result and selects none as canonical.
