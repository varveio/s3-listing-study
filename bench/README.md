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
allocation, and matrix all live in the one file.

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

Losing a level of indentation on a `matrix:` makes it a sibling of the tool
rather than its body. That is refused — as an unregistered tool with no default
mode — rather than quietly running the tool once.

## Cases are generated

Tools needing more than one case declare a `matrix`, whose cross-product is
that tool's set of
cases — two modes and two memory sizes is four cases from four lines, rather
than four hand-copied blocks that can drift apart.

One cross-product forces every mode to take every value of every axis, which is
wrong as soon as one mode needs an allocation its siblings do not. So a tool may
state several blocks and take their union, and a block may carry its own
`resources`:

```yaml
swath:
  matrix:
    - mode: [recursive-tsv, recursive-parquet]
      memory_gb: [4]

    - mode: [recursive-parquet-sorted]
      memory_gb: [4, 8]
```

Every block must declare the same axis *names*, so one tool's case IDs keep
their shape; the values are what differ.

Values resolve in four shallow layers, nearest statement winning:
`defaults` → the tool → the block → the matrix axis. `resources` is a flat
table of scalars, so there is no nesting for a merge surprise to hide in.

## A plan asks for a shape, not a machine type

`resources` states `vcpus` and `memory_gb`; [`instances.yaml`](instances.yaml)
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
  matrix:
    - mode: [recursive-parquet-sorted]
      container_memory_gb: [2, 4]
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

An ID is derived from the axis values (`recursive-parquet.memory_mib-2048`), so
adding an axis later changes every ID a tool generates. Identity is therefore
carried by `fingerprint`, a digest over the resolved case. It survives an ID
scheme change, and it refuses the reverse mistake: editing a matrix value while
the derived ID lands the same would otherwise append non-comparable runs into
one case directory.

`reps` is excluded from the fingerprint — how many times we ran something is
not part of what we ran. `timeout_s` is included, because it can truncate a run
and change the result.
