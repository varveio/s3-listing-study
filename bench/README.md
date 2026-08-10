# Benchmark plans

One file per bucket under [`buckets/`](buckets/), saying what to run against
that bucket and on what box.

```
s3-listing-study resolve-plan --bucket noaa-ghcn-pds
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

A **row** — one entry in a tool's `cases` — states what one case *is*: `mode`
and the allocation (`vcpus`, `memory_gb`, `container_memory_gb`).

A **layer** — `defaults`, or a tool's own body — states what every case under it
*inherits*: the allocation again, plus the schedule (`reps`, `timeout_s`). Never
`mode`: eleven tools have eleven mode vocabularies, so nothing above a row has a
mode to state. A tool body is therefore `defaults` plus `cases`.

A row carries only what the ID and the fingerprint can *both* see, which is what
keeps `timeout_s` out of one: it is in the fingerprint but not the ID, so two
rows differing only there would render one ID and two fingerprints — two
non-comparable runs filed into one case directory.

## Cases are enumerated

Tools needing more than one case list rows, and each row is one case — the
number of cases is the number of lines, with nothing multiplied out:

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

A cross-product was the earlier answer, and it multiplied where it should have
enumerated: a tool whose sorted mode needed an allocation its siblings did not
had to be split into blocks and unioned back together. There is likewise no
plan-level sweep — one `defaults` row and a list of them mean the same thing at
one entry and diverge silently at the second, so a list there is refused.

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

The box stays identical across those two cases — same machine type, same cores,
same neighbours — and the ceiling reaches sizes no machine type sells. Omitting
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
