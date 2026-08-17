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
| `FIXED_COMMAND_PREFIX` | yes | The subject's executable, recorded and cross-checked against `build/image.json` |
| `MODES` | yes | The mode vocabulary, and one manifest per mode — see below |
| `build_command(request)` | yes | Compiles the complete subject argv |
| `SUPPORTS_UNSIGNED` | yes | Whether the subject can list without a credential |
| `SUPPORTS_SIGNED` | no, defaults true | False only where the *harness* cannot yet deliver a credential this subject would accept |
| `CONFIG_KEYS` | no, defaults empty | The config keys this capsule accepts; anything else is refused |
| `FUNCTIONAL_ENV` | no, defaults empty | Non-secret environment the subject structurally needs |
| `build_env(request)` | no, defaults to `FUNCTIONAL_ENV` | Environment derived from the request — heap flags, and nothing else so far |
| `REQUIRES` | no, defaults empty | A mode that must run before another mode of this same capsule |

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
        axes={"concurrency": Ceiling(8)},
    ),
    "recursive-parquet-sorted": Mode(
        product="parquet-sorted",
        fields=("key", "size", "mtime", "etag"),
        axes={"concurrency": Ceiling(8)},
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
| `axes` | Per reserved name: `Fixed(v)`, `Default(v)`, `Ceiling(v)`, `Inert`, or absent. |
| `purpose_ceiling` | The most a plan may claim this mode is. A plan may demote a run to `canary`; it may never promote `summarize` to `measurement`. |

**`product` and `fields` translate across tools; `axes` does not.** The first two
describe the artifact, which is why they are comparable at all. An axis name
identifies the axis and explicitly not the semantics — `-c`, `--checkers` and
`--list-concurrency` govern different things, and no declaration makes them one.
`axes` exists so the recorded value stops lying, not so it can be compared.

### The five states of an axis

`Absent` currently has to mean five things at once, and cannot:

| State | Means | Example |
| --- | --- | --- |
| absent | This tool has no such knob | minio-mc concurrency |
| `Fixed(v)` | Real, effective, and not settable | ps3 at 256 |
| `Default(v)` | Settable; this is what it runs at unsilenced | s4cmd `-c` |
| `Ceiling(v)` | Settable, but the effective width is lower and data-dependent | swath's AIMD start at `min(4,N)`; s5cmd's `min(numworkers, shards)` |
| `Inert` | Flag accepted, no effect **on this mode** | rclone `--checkers` on flat `ListR` |

`Inert` means *statically* inert for the mode. A knob whose effect depends on the
target — s4cmd's `-c` does nothing on a flat prefix but works on a nested one —
is not `Inert`; namespace shape is an analysis covariate, not a declaration.

A `Ceiling` axis records what was *asked for*; what was *achieved* is a fact
about the run and belongs in evidence, never in `config`. Writing a nominal 8
into a hashed blob when the tool ran at 4 is the same lie this table exists to
prevent.

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
  key. Absent means *this tool has no such knob*; a value means *this is what it
  ran at*.

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
nothing. The result is derived from values already hashed, so like `signed` it
is recorded and never a hash input.

## Declaring a prerequisite

That a hinted listing needs hints is a fact about the tool, in the same category
as which modes exist. So the capsule states it — `REQUIRES`, mapping a mode to
the mode that must precede it — and the plan says nothing at all. A row asking
for the dependent mode is asking for whatever that mode requires.

Three constraints:

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

What the planner does with the declaration is in
[`architecture.md`](architecture.md); where the resulting attempts are recorded
is in [`model.md`](model.md).
