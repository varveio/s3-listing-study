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

## Cases are generated

Each tool declares a `matrix`, and its cross-product is that tool's set of
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
      memory_mib: [2048]

    - mode: [recursive-parquet-sorted]
      memory_mib: [2048, 4096]
      resources:
        machine_type: n4-highcpu-4
```

Every block must declare the same axis *names*, so one tool's case IDs keep
their shape; the values are what differ.

Values resolve in four shallow layers, nearest statement winning:
`defaults` → the tool → the block → the matrix axis. `resources` is a flat
table of scalars, so there is no nesting for a merge surprise to hide in.

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
