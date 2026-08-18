# The capsule contract

What a capsule declares to the harness, and what the harness promises it in
return. This is the runtime boundary between `benchmark/` and `tools/<tool>/`.

[`docs/operating/tool-structure.md`](../../docs/operating/tool-structure.md)
owns the capsule's *directory* layout and its Markdown roles;
[`capsule-authoring.md`](../../docs/operating/capsule-authoring.md) owns the
process of writing one. This page owns the Python contract between them.

Read [`architecture.md`](architecture.md) for the ownership question these rules
all descend from.

## The one question

**Does the harness have to do something different because of this value?**

If yes, the harness owns it — it is a column, it is acted on, it is an identity
input. If no, the capsule owns it and the harness forwards it without looking.

| Value | What the harness does | Owner |
| --- | --- | --- |
| `auth_role` | Resolves to a service account and a credential secret; null runs unsigned | harness |
| `executor` | Selects which execution environment renders and submits the job | harness |
| `location` | Chooses the region the machine runs in — the network distance to the target | harness |
| `machine_type` | The shape resolved from vCPUs and memory; what the executor allocates | harness |
| `vcpus`, `memory_gb` | The declared pair a shape is resolved from | harness |
| `container_memory_gb` | Sets the container's cgroup ceiling | harness |
| `heap_percent` | Fixes one share for every managed runtime, so tuned and untuned tools stay comparable | harness |
| `timeout_s` | The worker's kill deadline, and the basis of the provider's run duration | harness |
| `target_bucket`, `target_region`, `target_prefix` | Names the target; region reaches the subject's environment | harness |
| mode, concurrency, page size, output flags | Nothing — forwarded, never read | capsule |
| managed-runtime heap flags | Nothing — the capsule renders the share into whatever its runtime reads | capsule |

`mode` looks like an exception and is not one. Verification does need it —
`verify` normalizes both sides through a capsule's `normalize.py` — but it
arrives there as a **pass-through**: `adapters.normalize_to_path(adapter_dir,
tool, mode, …)` hands the value on without the harness ever branching on it.

The config blob must reach **both** capsule entry points, `command.py` and
`normalize.py`. Today `normalize.py` is given only `(mode, prefix)`, so a
capsule whose output shape depends on a config key cannot parse its own output —
a gap to close alongside the blob's plumbing, not after it.

## What a capsule declares

Every declaration lives **inside `command.py`**, beside `MODES`.
`adapter_bundle_sha256` covers a closed tuple —
`ADAPTER_FILES = ("command.py", "normalize.py")` (`build_selection.py:18`) — so
a declaration in a new file under `adapter/` would change no identity at all,
and a capsule could be altered without anything noticing.

| Export | Required | What it states |
| --- | --- | --- |
| `TOOL` | yes | The tool slug this capsule is for |
| `EXECUTABLES` | yes | The subject's executables, cross-checked against `build/image.json`; each mode names which it runs |
| `MODES` | yes | The mode vocabulary, and one manifest per mode — see below |
| `build_command(request)` | yes | Compiles the complete subject argv |
| `SUPPORTS_UNSIGNED` | yes | Whether the subject can list without a credential |
| `SUPPORTS_SIGNED` | no, defaults true | False only where the *harness* cannot yet deliver a credential this subject would accept |
| `CONFIG_KEYS` | no, defaults empty | The config keys this capsule accepts; anything else is refused |
| `FUNCTIONAL_ENV` | no, defaults empty | Non-secret environment the subject structurally needs |
| `build_env(request)` | no, defaults to `FUNCTIONAL_ENV` | Environment derived from the request — heap flags, and nothing else so far |
| `REQUIRES` | no, defaults empty | The chain of this capsule's own modes that must run before a mode, in order, each naming the artifact taken from it |
| `VALIDATE_ARTIFACT` | no, defaults empty | Per producing mode, the check that refuses an artifact that is structurally useless before anything consumes it |

A capsule that declares neither `SUPPORTS_UNSIGNED` nor `SUPPORTS_SIGNED` as
true describes a subject that can issue no request at all, and the loader
refuses it.

## A mode is not just a legal string

`MODES` maps each mode to a manifest, because the facts the harness needs vary
*per mode* rather than per tool:

```python
MODES = {
    "recursive-tsv": Mode(
        product="text",
        fields=("key", "size", "mtime", "etag"),
        axes={"concurrency": Ceiling(64, "source@cef8ec2")},
    ),
    "recursive-parquet-sorted": Mode(
        product="parquet-sorted",
        fields=("key", "size", "mtime", "etag"),
        axes={"concurrency": Ceiling(64, "source@cef8ec2")},
        purpose_ceiling="measurement",
    ),
}
```

None of this is used to *run* the tool — `build_command` still owns argv
entirely and the harness never interprets a mode. The manifest exists for what
happens afterwards: deciding what a result may be compared with, and recording
what it actually ran at.

**Per mode, not per capsule**, because the truth differs within one tool:
rclone's `--checkers` is live on its walk mode and inert on its flat one;
s7cmd's `--no-sort` reaches one of five recursive modes; swath's Parquet modes
write a directory sink while its TSV mode streams to stdout.

| Field | What it is for |
| --- | --- |
| `product` | The output artifact — `text`, `parquet`, `parquet-sorted`. **Shared vocabulary**: "text" means the same for aws-cli and swath, so a report can group a text stratum and keep a Parquet number out of it. |
| `fields` | Which contract columns this mode populates. A mode emitting key-only must not be ranked against one emitting four columns — otherwise a tool wins by emitting less. |
| `axes` | Per reserved name: `Fixed(v)`, `Default(v, provenance)`, `Ceiling(v, provenance)`, `Stated()`, `Inert`, or absent. |
| `purpose_ceiling` | The most a plan may claim this mode is. A plan may demote a run to `canary`; it may never promote `summarize` to `measurement`. A mode capped at `preparation` is also never row-counted: what it publishes is not a listing, and the worker records a null count rather than asking a normalizer a question about a mode it does not have. |
| `inline` | Another mode of this capsule the worker runs untimed, in the same container, immediately before the timed subject — see *A setup exec is not a chain link*. |
| `artifacts` | Logical name to the filename this mode publishes into its sink. What a consumer asks for by name — see *A producer declares its artifacts by name*. |
| `product_artifact` | Which of `artifacts` carries this mode's *measured* output. Empty while a product still streams through stdout, which is every mode today. |

**`product` and `fields` translate across tools; `axes` does not.** The first two
describe the artifact, which is why they are comparable at all. An axis name
identifies the axis and explicitly not the semantics — `-c`, `--checkers` and
`--list-concurrency` govern different things, and no declaration makes them one.
`axes` exists so the recorded value stops lying, not so it can be compared.

### The six states of an axis

`Absent` currently has to mean six things at once, and cannot:

| State | Means | Example |
| --- | --- | --- |
| absent | This tool has no such knob | minio-mc concurrency |
| `Fixed(v)` | Real, effective, and not settable | ps3 at 256 |
| `Default(v)` | Settable; this is what it runs at unsilenced | s4cmd `-c` |
| `Ceiling(v, provenance)` | Settable; the subject's own limit when unsilenced, and the effective width is lower and data-dependent | swath's 64 with an AIMD start at `min(4,N)`; s5cmd's `min(numworkers, shards)` |
| `Stated()` | Settable, and the capsule has no value of its own: the plan must state it | s3-fast-list `segments` |
| `Inert` | Flag accepted, no effect **on this mode** | rclone `--checkers` on flat `ListR` |

`Inert` means *statically* inert for the mode. A knob whose effect depends on the
target — s4cmd's `-c` does nothing on a flat prefix but works on a nested one —
is not `Inert`; namespace shape is an analysis covariate, not a declaration.

`Stated` is not `Default` with an `unverified` provenance: that still asserts *a*
number, where `Stated` asserts there is none to record — upstream documents no
default the capsule could cite, and a capsule that quietly chose one would freeze
every sweep at it. Resolution refuses a plan that leaves a `Stated` axis silent,
offline, rather than folding anything in. When a prerequisite chain expands, a
`Stated` (or `Default`/`Ceiling`) axis the prerequisite mode itself declares
inherits the consuming row's value, while an axis the prerequisite does not
declare never flows into it. An inline setup exec inherits by the same rule —
s3-fast-list's `ks-split` cuts at the `segments` the `list-hinted` row stated.

`Ceiling` is `Default` plus one semantic, so it carries the same value and the
same provenance: the subject's own number, not the study's. **What a campaign
asks for is plan content** — a row states `concurrency: 8` against swath's
declared 64, which is what makes a detune visible and reviewable instead of a
prose convention buried in a capsule. What was *achieved* is a third thing
again: a fact about the run, which belongs in evidence and never in `config`.
Writing a nominal 8 into a hashed blob when the tool ran at 4 is the same lie
this table exists to prevent.

## Signing is declared, never assumed

Whether a request is signed is a fact about the subject, not a plan's
preference. Four of the eleven tools have no unsigned request path. So the
capsule declares what it can issue, and the resolver obeys:

- no unsigned path → the case signs;
- cannot sign → the case lists unsigned;
- both available → **unsigned**, unless a row asks otherwise.

`SUPPORTS_SIGNED = False` records a **harness limitation, not a tool property**,
and every use of it is a defect to be retired. minio-mc is the only one: it
resolves credentials from an `MC_HOST_<alias>` URL, and upstream's
`parseEnvURLStr` accepts `https://ACCESS:SECRET[:TOKEN]@host` — so the subject
can sign; the harness simply has no way to render that template. Closing it
means the worker, which already holds the parsed credential, building the alias
from a capsule-declared form, with percent-encoding mandatory since AWS secrets
contain `/` and `+` while mc's parser splits on `:` and `@`. Until then mc
appears only in unsigned tables, and its absence from a signed one is a fact
about us.

Unsigned is the default for a tool that can do either because signing adds a
signature to roughly a thousand requests, which is a different measurement, and
the cheaper one is the better baseline.

**A stratum a capsule cannot issue is refused, not ignored.** That refusal is
the whole point: six attempts in the first campaign recorded `authenticated` and
ran unsigned, because four capsules pinned `--no-sign-request` in argv and never
read the stratum at all.

The harness passes a derived `signed` boolean — `auth_role is not None` —
because signing is also an argv decision inside six capsules: `--no-sign-request`
in aws-cli, s5cmd, s3-fast-list and swath, `--target-no-sign-request` in s7cmd,
and a branch in rclone. *Which* identity signs stays the harness's business and
never reaches the subject.

## Configuration is opaque, and its key names are not

Everything on the capsule side travels as one canonical JSON `config` blob,
which the harness treats as **opaque bytes**: it hashes them for identity,
stores them, and forwards them. A value that changes what the *subject* does is
the tool's business, and the harness's interest ends at "these bytes differ, so
this is a different case."

**Opaque at the boundary is not opaque in a plan.** A plan keeps `mode` as a
named row field, because that is how a human sweeps one and how
[`../plans/README.md`](../plans/README.md) explains a case; resolution folds it
into `config`. That is the relationship the plan already has with `signed`,
which a row states and the resolver turns into `auth_role`.

A row may also state any capsule-declared key with no row field of its own
directly under `config:` — a knob only one subject has and no axis describes.
Those keys are folded into the blob before the capsule sees it, so its own
refusals still decide what is legal there: an undeclared key, or one its mode
declares `Fixed`, is refused exactly as it would be if `build_command` read it
directly.

**An undeclared key is refused.** `LoadedCommandAdapter.compile` rejects
anything outside `CONFIG_KEYS` before `build_command` runs. Without that,
`concurency: 8` would be silently ignored and a sweep would produce cells that
are all identical.

### Axis names are shared; their meanings are not

A knob's *meaning* is the capsule's. A knob's *name* is the study's.

Concurrency forces the distinction. "Every tool at logical concurrency 8" is
only meaningful if every capsule means the same thing by the number, and no
schema can make them: the translations are `-c`, `--checkers`,
`--list-concurrency`, `--max-parallel-listings`, `--concurrency`, governing
request fan-out, directory checkers and page-fetch workers respectively. A
shared typed field could enforce a range but never a meaning, which is why there
is no such field.

But the number at which a run listed is a **primary analysis axis**. A
comparison that cannot say what concurrency each subject used is not a
comparison anyone can defend. So three rules apply to any axis compared across
tools:

- **Reserve the name.** A capsule with a listing-concurrency knob calls it
  `concurrency`. One free to call it `checkers` or `workers` would make the axis
  unqueryable while looking perfectly reasonable in its own file.
- **Declare it, do not pin it.** A number compiled into `build_command` is
  covered by `adapter_bundle_sha256`, so identity stays correct — but it is
  invisible to every report and cannot be swept. Declared, it is hashed,
  recorded, projected into a column, and a plan can vary it.
- **Record the effective value, including the default.** A plan stating no
  concurrency gets the capsule's default written into `config`, not an absent
  key — the loader merges declared defaults **before hashing**, so the identity
  reflects what ran. Absent means *this tool has no such knob*; a value means
  *this is what it ran at*.

### A recorded number must carry its provenance

A capsule that misses an upstream version bump writes a confident lie into
`config`, and a recorded-but-wrong value is **worse than an absent one, because
it claims knowledge**. So each number a capsule records for its subject — a
`Default` and equally a `Ceiling` — states where it came from:

| Provenance | Meaning |
| --- | --- |
| `help` | Printed by the subject's own `--help`, checkable at build time |
| `source@<rev>` | Read from upstream source at a pinned revision |
| `unverified` | Believed, not established — surfaced as such in every report |

A build-time check can only close the first kind: it diffs `help`-provenance
defaults against the binary's actual `--help` output and fails the build on
drift. It cannot receipt s3p's 100 (a source constant), mc's SDK retry
constants, or s7cmd's unset timeouts that fall through to an AWS SDK default —
which is exactly why a blanket `--help` gate would either block those capsules
or invite fabricated receipts. `unverified` is the honest state for them until
someone runs the tool and writes a receipt.

Reserved today: `mode` and `concurrency`. Page size is the obvious next one.
Reserving a name costs a line in a capsule; discovering after a campaign that
the axis was unrecorded costs the campaign.

**A tool with no such knob is a different answer from a tool we did not ask.**
Six of the eleven expose nothing and list at whatever their own default is, and
the study does not currently know those numbers. That is an honest absent value
rather than a hidden one — and unfinished work under the comparable-setup-effort
rule in [`../../AGENTS.md`](../../AGENTS.md). Those defaults are facts about
each tool, established by reading its source or running it and recorded on its
tool page, never assumed.

## The ceiling, and the share of it

A managed runtime has to be told how much of its memory it may use as heap, and
that number is a function of the container ceiling. Two different things are
tangled there, with different owners:

| The value | What it is | Owner |
| --- | --- | --- |
| The share — `75` percent | How much of the visible ceiling a managed runtime may take, chosen once so tuned and untuned tools are compared on equal terms | harness |
| The translation — `JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage={percent}` | How *this* runtime is told: which variable, which syntax, proportion or absolute MiB | capsule |

The share is a methodology decision under the comparable-setup-effort rule:
pushing it into eleven capsules would let each drift to its own number, which is
exactly the tuning asymmetry that rule prevents. The translation is tool
knowledge the harness has no business holding — that swath is a JVM and s3p is
V8 is a fact about those two tools and nothing else.

Neither is a plan's business. Nine of eleven tools have no heap to size, so a
per-bucket heap knob would be one most cases ignored and every plan restated;
the Go, Rust and Python tools take what they take.

So `CommandRequest` carries `visible_memory_gb` — the container ceiling, or the
whole box when there is none — and `heap_percent`. A capsule turns them into
whatever its runtime needs. Because heap flags arrive as environment rather than
argv, a capsule exports `build_env(request)`; the loader defaults it to
returning `FUNCTIONAL_ENV`, so the nine capsules with no managed runtime declare
nothing.

Identity follows the same split. The share is not a global hash input — nine
subjects cannot feel it, and re-identifying their cases over a number they
ignore would be over-invalidation with no measurement behind it. But for the two
subjects that *can* feel it, changing the share changes the measurement, so it
must change their identity. The mechanism is the axis machinery that already
exists: a managed-runtime capsule declares `heap_percent` as a `Fixed` axis
carrying the constant, and the loader merges it into that capsule's `config`
before hashing, exactly as it merges a declared default. The loader refuses a
capsule declaring any other number than the harness's — which is what keeps the
share a single methodology decision rather than eleven drifting ones. The
rendered heap flags are then derived from values already hashed, so like
`signed` they are recorded and never a hash input of their own.

## Declaring a prerequisite

That a hinted listing needs hints is a fact about the tool, in the same category
as which modes exist. So the capsule states it — `REQUIRES`, mapping a mode to
the mode that must precede it **and the artifact taken from it** — and the plan
says nothing at all. A row asking for the dependent mode is asking for whatever
that mode requires.

```python
REQUIRES = {"list-hinted": (("list", "keyspace"),)}
```

Four constraints:

- **The prerequisite names a mode of the same capsule.** A dependency on
  something another tool produced — an S3 Inventory, say — is a different
  problem: that artifact is an input the study supplies, entering as an ordinary
  hashed input with no preparation attempt behind it.
- **The declaration is static.** `plan_cli` contacts nothing and writes nothing,
  and a reviewer must be able to see that a plan yields twenty-two attempts
  rather than eleven before anything is submitted. The *shape* is knowable
  offline even though the dependent `case_id` is not.
- **The prerequisite does not inherit the consumer's config.** It takes whatever
  config the capsule says it takes, which is what lets one preparation serve a
  whole sweep. See [`identity.md`](identity.md) § *Two identities, two
  questions*.

- **The prerequisite names the artifact, not just the mode.** The pair is
  required and the bare mode is refused; see below.

**A chain may be more than one link.** `REQUIRES` is an ordered list, not a
single mode. What the one-edge rule was protecting is *dynamic* graphs — a chain
discovered at run time — and a statically declared list preserves that
completely, since the whole shape is readable offline and the depth is bounded by
the declaration.

### A producer declares its artifacts by name

A mode declares every file it publishes into its sink, under a logical name:

```python
"list": Mode(..., artifacts={"keyspace": KS_NAME}),
```

and a consumer names the one it wants. This is not decoration. Selecting a
producer's artifact was *"the manifest holds exactly one file, take it"* — true
only for as long as every producing mode published exactly one, and a listing
that writes its product to a file publishes two. The failure that rule reaches
is silent in the worst direction: a 131 MB listing staged where a 679 KB hints
file belongs, under a digest that checks out. Named, the same lookup also
catches the sink holding one *wrong* file, which no count ever could.

`artifacts` is not `product` under another name. `product` is the **format**
vocabulary — `text`, `parquet` — shared across tools so a report can keep a
Parquet number out of a text stratum. `artifacts` says which files land.
`product_artifact` names which of them carries the measured output; empty means
the product does not travel as a declared file yet.

The refusals, all at load:

- an artifact name the required mode does not declare;
- a required mode that declares no artifacts at all;
- a `product_artifact` that is not one of the mode's own `artifacts`;
- a bare mode where the pair belongs — sugar for "its sole artifact" is exactly
  the inference this replaces, and it would silently rebind every consumer the
  day a capsule publishes a second file;
- two consumers wanting *different* artifacts of one producing mode. The
  producing attempt records one `artifact_sha256`, and two answers to that one
  question is how evidence comes to disagree with itself. No capsule declares
  that shape; the column has to grow a name before one can.

Only `s3-fast-list` declares artifacts today, because only its chain consumes
one: `list` publishes `keyspace`, `ks-split` publishes `hints`.

### A setup exec is not a chain link

A chain link is what a **shared** artifact is for: one preparation, its own
identity, and every consumer of those bytes bound to it. s3-fast-list's hinted
path has both kinds of step in it. The bootstrap `list` emits a key distribution
that a whole sweep can share, so it is a link. `ks-tool split` then cuts that
distribution into ranges — a sub-second local transform whose output exactly one
measurement reads — and making *that* an attempt buys a slot, a job, a VM and an
identity for a file nothing else will ever ask for.

So a mode may declare `inline`, naming another mode of the same capsule that the
worker runs **untimed, in the same container, immediately before the timed
subject**. The setup exec is handed whatever the chain staged, and what it
publishes is what the subject is compiled against. Its wall clock is recorded as
setup evidence in `result.json` and never merged into the measurement's timing;
its captures and its sink upload under the attempt's own `inline/` directory,
which keeps it out of the native sink a listing's row count is read from.

The declaration carries the same offline-readability constraints as a chain, so
the refusals are:

- the named mode must exist in this capsule, and may not be the mode itself;
- it must declare `purpose_ceiling = "preparation"` — a setup exec produces, it
  does not measure;
- it may not declare an `inline` of its own, and may not be a mode with its own
  `REQUIRES`. One flat step inside one attempt: anything deeper puts a graph back
  where no reviewer and no slot can see it.

**A setup exec gets 300 seconds, whatever the row's timeout is.** The provider
deadline bounds the *container*, not a phase, so a setup allowed the subject's
full timeout could push the measurement past it and lose the whole attempt to a
hard kill. A setup exec is a cheap local transform of bytes the chain already
staged — the worked example runs in under a second — so a step that needs longer
is a preparation, and its own attempt.

**The credential reaches only the timed subject.** A setup exec is by contract a
local transform of bytes the chain already staged, so it gets the harness base
environment, the region, and its capsule's functional environment — and not the
signing credential. A mode that has to sign to do its work is a preparation with
its own identity, not an inline setup.

An inline setup exec carries **no identity of its own**. It is part of the
measurement attempt, and the axes it runs at are already in that attempt's config
blob, which is what the case hashed. A setup exec that fails — nonzero, timed
out, leaving a process behind, or publishing anything other than exactly one file
— fails the whole attempt (`EXIT_SETUP_FAILED`), because a subject run on hints
that were never made measures something else.

Failing does not mean vanishing. The attempt still publishes: `result.json` in
the usual shape with every subject field null (`execution`, `wall_seconds`,
`max_rss_kb`, `row_count`), the `setup` block saying what the exec did and how
long it took, and its captured stdout/stderr under `inline/`. That capture is
the only account of *why* the attempt has no measurement in it, and it is held
to the same secret scan as the subject's own.

### An artifact is validated before anything consumes it

A digest proves an artifact is *unchanged*, not that it is *usable*. s3-fast-list
is the worked example: `ks-tool split` emits one cut point per line, but if the
first distribution row alone exceeds the average the first line comes out
**empty** — and an empty cut point becomes a full-range serial scan running
alongside every real segment, so the hinted run can be slower than the unhinted
one it was meant to beat. On a flat namespace the same code path yields a single
empty line, producing two identical full-range tasks.

Nothing about those files is corrupt. They digest cleanly and would flow
straight into a measurement that silently means nothing. So a capsule declares
`VALIDATE_ARTIFACT` — a mapping from **producing mode** to the check its bytes
must pass, because a capsule with two producers has two structures and neither
check reads the other's file. A mode with no entry produces bytes nothing
structural can be said about.

Where it runs follows where the artifact was made. A chain link's artifact is
checked when the preparation settles and before any consumer is minted, and a
refusal fails the preparation rather than the case that would have consumed it.
An inline setup exec's artifact is checked inside the attempt, before the subject
is compiled against it, and a refusal fails that attempt as
`EXIT_ARTIFACT_UNUSABLE` — the same claim about the same kind of bytes, one
location over. This is the refuse-rather-than-guess rule applied to the one place
where a hash cannot help.

### Inbound artifacts

`sink_dir` tells a mode where it may write. A mode that *consumes* an artifact
needs the symmetric thing: a container-local path where the harness has staged
the bytes it is to read. Without it, a hints file, a shard plan, or a commands
file cannot reach argv at all — and this is what blocks s5cmd's defining mode,
which is a single process reading one file, not a multi-process invocation.

An inbound artifact is content-addressed like any other, so the case that
consumes it hashes the digest, never the path. The request carries the staging
as `artifact_path` — the symmetric field to `sink_dir`, empty for the many
modes that consume nothing, and a consuming capsule refuses an empty one
rather than inventing a location.

What the planner does with these declarations is in
[`architecture.md`](architecture.md); where the resulting attempts are recorded
is in [`model.md`](model.md).
