# Case identity

What makes two runs the same measurement or different ones: what goes into a
case's hash, what deliberately stays out, and the one input no hash can cover.

Read [`architecture.md`](architecture.md) first for why the boundaries fall
where they do. [`model.md`](model.md) is where these identities are stored.

## What a case is

A case is **the tool and a hash over everything that can change the
measurement**:

```
<tool>.<hash>
aws-cli.9f300cc4d2b1
```

Three groups of inputs go into the hash:

| Group | What it covers |
| --- | --- |
| **Environment** | The values the harness acts on: auth role, target bucket/region/prefix, location, machine type, vCPUs, memory, container ceiling, output target, timeout |
| **Config** | The capsule's own keys, `{}` when empty |
| **What ran it** | The tool slice and the platform slice |

Anything that could make two runs non-comparable is in it by construction. One
hash rather than a readable ID beside a fingerprint removes the law that the two
must move together — a field either changes the identity or it is not an input —
and removes the need for a revision counter, since a changed input is already a
changed hash. It also makes "have we already measured exactly this?" a lookup.

**What the hash costs:** the identity is not readable in a bucket listing.
`swath.recursive-parquet-sorted.container_memory_gb-2` would say what it was;
`swath.4c1e8a77b920` does not. The columns say it instead, and reports render
from them.

## The hash, normatively

An unspecified encoding will be re-derived differently by the next reader, so:

```python
CASE_HASH_V1 = b"s3-listing-study-case-v1\0"


def case_hash(environment: dict, config: dict, tool_slice: str, platform: str) -> str:
    document = json.dumps(
        {
            "environment": environment,  # the table above, absent keys omitted
            "config": config,  # the capsule's blob, as an object
            "tool_slice_sha256": tool_slice,
            "platform_sha256": platform,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(CASE_HASH_V1 + document).hexdigest()[:12]
```

- **Domain separation and version** lead the input, as `_input_digest` does for
  build inputs (`build_image.py:78`).
- **Canonical JSON** means `sort_keys=True`, `separators=(",", ":")`, ASCII
  escaping, and no non-finite numbers — the form `build_image.py:147` uses.
- **12 hex digits (48 bits)** is the identifier length, matching the job-ID
  digest. Collisions are a correctness failure, not a nuisance: two cases
  sharing a prefix would merge their evidence.

  Neither the primary key nor `UNIQUE (job_name)` catches that on its own. A
  colliding second case is allocated the next ordinal — `max(attempt) + 1`, as
  every repeat is — so it arrives with a fresh primary key and a fresh job name
  and files itself quietly under the first case's identity. The refusal is
  therefore explicit: `case_inputs` stores the canonical document this digest
  was taken over, and an insert naming an existing `case_id` whose `case_inputs`
  differ is rejected. A collision is a loud integrity error, which makes the
  identifier length a question of taste rather than of correctness.
- **Absent, null, and empty are different.** A key with no value is omitted; a
  key that is explicitly null is present with `null`. `auth_role` null (unsigned)
  and `container_memory_gb` null (no ceiling) are values, not absences.
- `tool` and `suite` are **not** hash inputs. The tool prefixes the identifier,
  and the suite prefixes the path.
- **The `environment` document depends on what the case is for** — see
  [*Two identities, two questions*](#two-identities-two-questions). The encoding
  is identical either way, so one function serves both and the caller decides
  what it is identifying.

**Changing the input list re-identifies everything.** That is the intended
behaviour, not a hazard: a case measured under a different set of inputs is a
different case. `CASE_HASH_V1` is bumped alongside the change, so a hash always
states which input list produced it.

## Two identities, two questions

A measurement and a preparation are hashed over different input sets, and the
asymmetry is deliberate rather than a special case.

| | Hashed for a measurement | Hashed for a preparation |
| --- | --- | --- |
| Target bucket, region, prefix | yes | yes |
| The capsule's own config | yes | yes — **its own**, never the consumer's |
| Tool slice, platform slice | yes | yes |
| Machine type, vCPUs, memory, container ceiling, timeout | yes | **no** — recorded, not hashed |
| `auth_role` | yes | **no** — recorded, not hashed |
| Output target | yes | **no** — a preparation's artifact is its output |

**A measurement's identity answers "are these comparable?", which is a question
about the environment. A preparation's identity answers "do we already have this
artifact?", which is a question about content.** A faster machine produces the
same hints, so the execution environment is recorded on a preparation's row and
stays out of its hash — the same treatment `image_uri` gets for every case.

Preparations are excluded from comparisons anyway
([`model.md`](model.md) § *Not every attempt is a measurement*), so their
identity never needed to carry comparability. Once it does not, reuse falls out:
a sweep of s3-fast-list across concurrency 4, 8 and 16 names **one** preparation
rather than three, and does not build hints at three different parallelisms.

Two things follow:

- **A prerequisite does not inherit the consumer's config.** A row sweeping
  concurrency sweeps the *listing*. Any sweep over any measurement axis
  collapses to one preparation.
- **A preparation's duration is an observation, not a comparable measurement.**
  Two attempts of one preparation case may have run on different machines and
  the row says which. Asking how preparation cost varies *with* the machine is a
  different question, asked by running the preparation command as a subject in
  its own right — `purpose = 'measurement'`, hashed the ordinary way.

## What is recorded but not hashed

| Value | Why it is not identity |
| --- | --- |
| `executor_env` (project, provisioning, boot disk, network) | Estate detail. Moving projects does not change how fast a bucket lists, and re-identifying every case because an account was reorganised is over-invalidation with no measurement behind it. |
| Provisioning model | SPOT changes how likely an attempt is to survive, not what it measures. A preemption is a failed attempt, not a different case. |
| `service_account`, `secret_resource` | What `auth_role` resolved to; the role name carries the meaning. |
| `image_uri`, `image_set_sha256` | You need to know exactly what ran and be able to reproduce it, but the slices identify it. Two attempts of one case may have run on different images, and the row says which. |
| `produced_by` | Which attempt made the artifact a case consumed. The artifact's content digest is what identifies it; *which run* produced those bytes is a debugging question. |
| `signed`, `visible_memory_gb` | Derived from `auth_role` and the ceiling, both already hashed. A derived value is not a second input. |
| `heap_percent` | A methodology constant nine of eleven subjects cannot feel. Hashing it would re-identify every Go, Rust and Python case when a share they ignore is changed, which is the law — *a field either changes the identity or it is not an input* — read backwards. It reaches the two managed runtimes as a declared axis of their capsules instead. |
| `executor` | One executor exists. Recorded so a second one is distinguishable when it arrives; hashed then, not before. |

`network` and `subnetwork` are arguable — an egress path could matter — but they
follow from the executor's project and location, so they stay in `executor_env`
until a run crosses VPCs.

### The output target is an input

Where a subject's output goes changes what is being measured, so it is hashed
like any other thing the harness chooses. s3kor issues one `write(2)` per key
against unbuffered stdout — over a million syscalls on a corpus this size — and
s5cmd's object channel is unbuffered, so its throughput is bounded by whatever
drains the pipe. A tool streaming to a pipe and the same tool writing to a file
are not running the same race.

It also splits comparisons that `product` alone would merge: two subjects can
both emit Parquet while one writes a directory dataset and the other a single
file to a pipe.

### The role name is hashed; its resolution is not

`auth_role` is a logical name, and the **name** is what enters the hash, because
what a tool may see can change what it lists. What it resolves to — a service
account and a secret version — is recorded instead.

That leaves one residual risk worth stating: repointing an existing role at
different credentials changes the measurement without changing the identity. A
role is therefore immutable once used.

## What identity cannot cover

Identical inputs give an identical hash, and the hash is the study's claim that
two runs are comparable. One input breaks that claim, and it is the largest one:
**the corpus.**

`target_bucket` names the bucket; it does not name what was in it. These are
public buckets that grow — the first campaign found 1,067,164 objects in
`noaa-ghcn-pds` on one afternoon, and that number is a fact about that
afternoon. Two attempts of one case, same hash, six weeks apart, listed
different corpora. No amount of hashing fixes this, because the only way to know
what was in the bucket is to list it, which *is* the measurement.

So the corpus is an **uncontrolled input, and agreement is what stands in for
control**. A set of attempts is comparable when its subjects agree on what they
found — the twelve-way agreement on 1,067,164 is not a nice-to-have result, it
is the evidence that those twelve timings describe the same work. `verify`
computes that agreement from the evidence rather than trusting the hash, and a
disagreement invalidates the comparison rather than one subject.

Two consequences:

- **A comparison is a set of attempts that agree, not a set of attempts that
  share inputs.** Same-hash attempts from different weeks are the same *case*
  and may not belong in the same *comparison*.
- **`recorded_at` is load-bearing**, not bookkeeping. It is how a reader sees
  that two attempts of one case are separated by six weeks, which is the signal
  to check agreement before putting them side by side.

The same logic covers everything else the study runs on someone else's
infrastructure and cannot pin: S3's own load, throttling, and whatever the
provider's network was doing that hour. None of it is an identity input, all of
it can move a timing, and agreement plus a visible timestamp is the honest
answer rather than a hash that overstates what it knows.

It also bounds artifact reuse. Hints describe a key distribution, and the bucket
that distribution came from has been growing ever since; the digest cannot tell
you the corpus moved, because the bytes are identical and increasingly wrong. So
**reusing a preparation across groups is a decision, not a default** — free and
obviously right within one launch, explicit across launches.

## The tool and platform slices

The toolbox is one image holding all eleven tools, so its digest is the wrong
granularity: bump rclone, rebuild, and every tool's hash would change — new
prefixes and lost comparability for ten tools that did not change.

Two digests carry that instead. **Both are defined over stage closures, not
stage bodies**, because the pinned base of a stage is part of what ran:

- **tool slice** — that tool's artifact, capsule recipe, build inputs, adapter
  bundle, the transitive `FROM` closure of its build stages *including the
  pinned digests on those `FROM` lines*, and the lines of the final stage that
  install or configure it.
- **platform slice** — the `runtime_base` digest, the APT snapshot pin, the
  worker's pinned Python requirements, the harness revision, and the remainder
  of the final stage.

The closure requirement is load-bearing, and the obvious implementation misses
it. Capturing a stage body *after* its `FROM` line would leave three externally
pinned bases outside both slices: `rust@sha256:cf9dd0…` (s3-fast-list),
`node@sha256:2cf067cf…` (s3p), and `eclipse-temurin@sha256:2f1da100…`, which
reaches swath through a second stage, `swath_jre`. A JRE bump would then change
nothing about swath's identity, which is the unrepairable direction of error.

**Attribution is what keeps the roster additive.** Hash the final stage whole
and every tool's install line lands in the platform, so adding a twelfth tool
re-identifies all eleven. With per-tool lines attributed to their own slices,
adding a tool leaves the platform digest and every existing slice
byte-identical.

Erring coarse is correct when attribution is unclear: over-invalidating costs
re-runs, while under-invalidating means two different binaries share an identity
and a comparison silently mixes them, which cannot be repaired afterwards.

### How attribution is decided

Three rules, and a refusal when they do not cover the file.

**Build stages resolve by closure, not by table.** `TOOL_STAGES` names one stage
per tool; the slice is that stage plus every stage reachable from it by `FROM`,
including the `FROM` lines themselves. Following the edge is what picks up
`swath_jre` without anyone remembering to list it. A stage reachable from more
than one tool — `runtime_base`, which nine of them build on — belongs to the
platform, because a change there really does affect all of them.

**In the final stage, `COPY --from=<stage>` attributes itself.** The stage names
the tool. That covers most of the final stage mechanically, with nothing to keep
in sync.

**Everything else in the final stage is platform unless marked.** The marker is
a `# slice: <tool>` comment on the line before the instruction. Defaulting to
platform is the safe direction: a tool-specific line left unmarked lands in the
platform digest, so changing it re-identifies every case — over-invalidation,
which costs re-runs. The opposite default would leave the line in no tool's
slice and silently under-invalidate.

A marker applies to an instruction, so the final stage keeps per-tool work in
separate instructions rather than one omnibus `RUN`. The `aws` symlinks belong
to aws-cli, the shim to s3p, `/home/s7cmd` to s7cmd, and `/data` and
`/home/s3study` to the platform. `/aws` is the interesting one — it is
`subject_workdir` for aws-cli *and* s5cmd, so the shared-stage rule applies
unchanged and it lands in the platform. The cost of splitting is one image layer
per fragment.

**The build refuses a file these rules do not partition.** Every line of the
final stage lands in exactly one slice; a marker naming an unregistered tool, a
`COPY --from` referencing an unknown stage, or a tool whose stage is unreachable
is a build error. Attribution that silently drops a line is the failure this
whole section exists to prevent, so it is checked rather than assumed.

### The slices are inside the manifest

`tool_slice_sha256` and `platform_sha256` are per-tool keys of the image set,
covered by the toolbox manifest digest the image recomputes and verifies at
build time. That placement is the point: a digest the controller never checks
cannot bind evidence to what produced it, and the adapter bundle — which sits
outside the manifest — is exactly the edit that could otherwise change the
subject's behaviour without changing anything the controller verifies. The tool
slice closes that.

## Open questions

- **The `executor` vocabulary.** What names exist, and how a name resolves to
  the code that renders and submits a job. `executor` is a hash input, so the
  vocabulary is an identity question rather than a naming one.
- **Where the role table lives.** `auth_role` → service account + secret version
  is deployment configuration, not plan content. It needs a file, a schema, and
  a validation point.
