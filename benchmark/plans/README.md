# Benchmark plans

Reusable study plans live under [`buckets/`](buckets/), saying what to run
against that bucket and on what box. [`canaries/`](canaries/) holds the two
small runner qualifications: the compatible capsule roster against replay and
representative capsule/output shapes against ordinary S3. A plan is execution
intent, not a history folder; superseded diagnostic rungs stay in Git and their
receipts/notes rather than accumulating here.

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

### A plan may state the backend it measures against

A plan whose target is a real bucket says nothing about a backend: the backend is
S3. A plan served by the swath replay server states one, because the server *is*
what every case here is measured against, and two runs against differently
configured servers are two measurements rather than one.

It splits by what varies. The plan-level `replay:` block holds what does not:

```yaml
replay:
  capacity_status: uncalibrated
  server_image_uri: registry/replay@sha256:<64 hex>
  fixture_sha256: <64 hex, over the served parts in key order>
  serving_mode: sorted            # or duckdb; always stated, never inferred
  latency_model:
    deadlines_ms: {worker_page: 107, pivot_probe: 41, structure_probe: 49}
    scale: 1.0
    jitter: none
```

The other explicit treatment is no injection:

```yaml
  latency_model: none
```

The latency treatment is a **measurement, not a preference**. A fixed model
adds the fixture's measured per-request latency profile; `none` measures the
subject and replay server without that delay. They impose different demand on
the server and therefore have different case identities. A swath run report
carries a `probe_latency` block whose call classes are exactly the replay
server's shape classifier, so when fixed injection is intended, read the honest
profile from the fixture rather than picking one. Note the direction the dial
moves: demand on the server scales inversely with the profile, so a profile
chosen because the server can meet it is a profile that hides an undersized
server. No other scalar or incomplete fixed mapping is accepted.

What *does* vary per case is how much machine the server gets, so those are
ordinary row fields, prefixed `replay_` and resolving through the same three
layers as every other allocation:

```yaml
defaults:
  vcpus: 16                        # the box, as always
  memory_gb: 64
  container_memory_gb: 40          # subject cgroup ceiling
  subject_vcpus: 7
  replay_vcpus: 8
  replay_memory_gb: 16
  replay_parquet_connections: 640
  replay_max_concurrent_requests: 512
  replay_prefetch: false
  replay_prefetch_max_windows: 96 # response-window cache capacity
  replay_heap_percent: 75
```

`vcpus`/`memory_gb` keep meaning **the box**. `container_memory_gb` is the
subject ceiling; `subject_vcpus` and the replay fields are the independent
allocations. When the subject has a ceiling, the remaining host CPU and memory
are derived and each must be positive — host reserves are not separately
authored identity inputs. Omitting `container_memory_gb` also omits the subject
cgroup limit for a replay diagnostic. The CPU remainder is still derived, but
there is then no guaranteed host memory headroom; resolved plans and reports
label it `unreserved` rather than inventing a reservation.
Reader-pool size and request-admission width are separate fields, and
`replay_prefetch` is a YAML boolean rather than an integer shorthand.
`replay_prefetch_max_windows` defaults to 96 when omitted; state it explicitly
when a diagnostic varies cache capacity.

`capacity_status` is a simple plan fact, not another control surface:
`uncalibrated` permits diagnostic replay work only, while `calibrated` permits
replay measurements. Set it to `calibrated` only after a real diagnostic
capacity canary has a committed receipt for this backend and allocation family.
Replay plans carry no correctness manifest: the worker counts rows in-container,
retains raw products, and routine reporting reads `result.json` only.

An image-bundled fixture states `fixture_sha256` alone. A staged fixture states
both `fixture_uri` and `fixture_sha256`. For staged Parquet, the digest is over
the UTF-8 bytes of sorted
`name<TAB>size<TAB>file-sha256<NEWLINE>` rows for the immediate `*.parquet`
children. The staging runnable recomputes it after download and before starting
the replay server, so a mutable wildcard cannot silently change a case. Generate
the value from an already staged directory with:

```sh
uv run python -m benchmark.replay_fixture /path/to/fixture
```

The staged-fixture branch remains `VERIFIED: no`: its manifest contract has
offline coverage, but no committed campaign receipt has exercised the provider
download path. Do not treat an image-bundled replay canary as qualifying it.

A `replay_*` key in a plan with no `replay:` block is refused, and so is a
`replay:` block whose defaults do not size a server: a plan states its backend
completely or not at all.

### `config` is the one nested map

Everything else a row states is a flat scalar. A row may also carry `config`, a
mapping of the capsule-declared keys the study reserves no row field for:

```yaml
some-tool:
  cases:
    - {mode: list, config: {page_size: 500}}
```

A reserved axis stays a first-class row field — `concurrency` and `segments`
are, because a report reads those columns across tools. s3-fast-list's hinted
path states both flat:

```yaml
s3-fast-list:
  cases:
    - {mode: list-hinted, segments: 16}
```

`config` is for a knob that is one tool's own business and no axis describes.
It is the common extension path for every capsule, not a Swath exception. A
tool may expose one argument or twenty; tuning depth can differ while the runner
continues to transport, hash, and record every tool's config identically.

Its keys are folded into the case's config blob *before* the capsule sees it, so
the capsule's own refusal still runs over them: a key it never declared in
`CONFIG_KEYS`, or one its mode declares `Fixed`, is refused there rather than
quietly forwarded. A key with a row field of its own — `mode`, `concurrency` — is
refused inside `config`: one way to say each thing.

These keys are hashed and rendered into the case label exactly as a row field is,
so two rows differing only in a `config` value are two cases rather than one
refused duplicate.

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

## A row has a label; identity is minted at submit

A row's derived label (`recursive-parquet-sorted.container_memory_gb-2`) is a
reviewer's handle: it is built from the *union* of the keys a tool's rows
state, so a ragged row set still labels one tool in one shape — a row that
omitted a key renders the value it inherited, and `container_memory_gb-none`
is the ceiling nobody set. The label is also what refuses two rows that
resolve to the same thing.

The label is not the identity. What a case *is* — the `case_id` a ledger row
and an evidence prefix carry — is a hash over everything that can change the
measurement, minted at submit when the tool and platform slices are known.
A plan never states or predicts it; `--dry-run` prints it. What goes into
that hash, and what deliberately stays out, is
[`../docs/identity.md`](../docs/identity.md); where it is recorded is
[`../docs/model.md`](../docs/model.md).

`reps` allocates attempts of one case — how many times we ran something is
not part of what we ran. `timeout_s` is a hash input, because it can truncate
a run and change the result.

## Where the evidence goes

One attempt owns one authoritative prefix, computed from its row rather than
discovered by listing:

```text
gs://<results-bucket>/<suite>/<target-bucket>/<tool>.<hash>.s<attempt>/
  result.json
  stderr.log.gz
  stdout.log.gz        -- only when stdout is a log
  native/listing.txt   -- the product, named for what is in it
```

**The product is a file the mode declares, and stdout is a log.** Nine of the
eleven subjects only print, so the worker lands fd 1 in the declared file and
there is no separate stdout capture at all — those bytes *are* the product.
The two with an output flag of their own (`s3-fast-list`, `swath`) are pointed
at the same path by their capsule, and their stdout uploads beside it as the
log it is. `result.json` records which channel applied, so no reader infers it
from which files happen to be there —
[`../docs/model.md`](../docs/model.md) § *What an attempt publishes* has the
block, and [`../docs/capsule-contract.md`](../docs/capsule-contract.md)
§ *The product travels on a declared file* has the rule.

Writes are create-only, `result.json` uploads last and is what makes an
attempt complete, and the evidence names the row it belongs to — the binding
rules and their reasons are [`../docs/model.md`](../docs/model.md)
§ *Object layout*. Create-only writes refuse a second execution merging into
a first; they do not seal evidence against replacement by credentials with
broader permissions — see
[`Attempt evidence is create-only`](../README.md#attempt-evidence-is-create-only).
