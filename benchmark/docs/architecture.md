# How the benchmark is put together

Why the harness is shaped the way it is: where a fact is allowed to live, who
owns it, and what the planner does with a case that cannot run yet.

This is the page to read first. The three beside it are reference:
[`identity.md`](identity.md) for what makes two runs the same or different,
[`model.md`](model.md) for the ledger, and
[`capsule-contract.md`](capsule-contract.md) for what a capsule must declare.

## Three places a fact can live

Everything the harness knows about a run is in exactly one of three places, and
which one is not a matter of taste:

| Place | Holds | Read by |
| --- | --- | --- |
| **The ledger** — one SQLite file | What was submitted, under what identity, where its evidence went, and how it settled | The controller, and anyone asking what happened |
| **The object store** | The evidence itself: the listing, the logs, the measurement | `verify` and `report`, on demand |
| **The subject's argv** | Everything about *how the tool was asked to list* | Only the tool |

The boundaries are one-way. The ledger never holds evidence; the object store
never holds controller state; the argv holds nothing the ledger cannot
reconstruct, because the `config` blob that produced it is a column.

### One question decides ownership

**Does the harness have to do something different because of this value?**

If yes, the harness owns it: it is a column, the harness acts on it, and it is
an identity input. Choosing a service account, sizing a machine, setting a
cgroup ceiling, picking a region — each one changes what the harness *does*.

If no, the capsule owns it. Mode, concurrency, page size, output format: the
harness forwards them and never looks inside. Its interest ends at "these bytes
differ, so this is a different case."

That single question settles arguments that otherwise run forever, because it
has an observable answer. It is applied in full in
[`capsule-contract.md`](capsule-contract.md).

## What the ledger holds, and what it refuses to

The ledger is **a record of attempts and their outcomes**, and deliberately not
a cache of anything derivable from evidence.

It holds: identity, every input the identity was computed from, the resolved
environment, where the evidence was written, the provider request that was
frozen, and the state that request reached.

It does **not** hold results, metrics, or verdicts. Those live in the evidence
objects and are recomputed by `verify` and `report` every time. A stored verdict
is a second answer to a settled question, and the two can disagree — at which
point you have to decide which to believe, with nothing to decide it on.

The same rule appears everywhere in this design, so it is worth naming once:
**never store a second answer to a settled question.** `attempt_id` is generated
from its parts rather than written. `verify` binds to recorded rows rather than
re-resolving a plan. Comparability is read off columns rather than cached in a
fingerprint.

The exceptions are deliberate and each has a stated reason: a value whose
*derivation rule may change* is stored, because history outlives the code that
wrote it. `job_name` and `result_prefix` are stored for exactly that reason.

## What the object store holds

Evidence, under a prefix computed from a row rather than discovered by listing.
Two properties matter:

- **Writes are create-only.** A deterministic prefix plus overwrite semantics
  means a second execution of one attempt silently merges into the first. An
  `ifGenerationMatch=0` precondition turns that into a loud failure.
- **The evidence names itself.** The row says where the evidence is; the
  evidence says which row it belongs to. Either direction alone fails quietly —
  a misfiled object under a correct-looking prefix reads as a valid measurement
  of the wrong thing. Derived verification records are the one carve-out:
  `verify.json` recomputes and overwrites by design, because it is a conclusion
  about evidence rather than evidence.

The layout and the reasoning are in [`model.md`](model.md).

## What lives in argv, and never surfaces

A capsule compiles the subject's complete argv, and the harness does not parse
it. That is the point: eleven tools have eleven vocabularies, and a harness that
understood them would need changing every time one of them did.

The cost is that a value pinned inside `build_command` is **invisible to
analysis**. It is covered by `adapter_bundle_sha256`, so identity stays correct
— but no report can show it and no plan can sweep it. That is fine for a flag
that is part of what the tool *is*, and unacceptable for an axis a comparison is
read along.

So the opacity has one exception, and it is a naming rule rather than a schema:
**an axis compared across tools has a reserved key name in `config`**, and any
capsule with such a knob declares it under that name instead of pinning it in
argv. Today that is `mode` and `concurrency`; page size is the obvious next one.
The meanings stay per-tool — `-c 8` and `--checkers 8` do not mean the same
thing, and no schema can make them — but the *name* is shared, which is what
makes one query answer the question for eleven subjects. See
[`capsule-contract.md`](capsule-contract.md).

## What the planner does

The planner turns intent into submitted attempts. In order:

1. **Resolve.** Read the plan, apply the three-layer inheritance, resolve shapes
   to machine types, and ask each capsule what it can issue. Contacts nothing.
2. **Expand dependencies.** A capsule declares that one of its modes requires
   another mode first. A row asking for the dependent mode expands into a
   preparation case and the measurement that consumes it.
3. **Mint identity.** Hash each case's inputs. A case whose inputs are all known
   gets a `case_id` now.
4. **Book what it cannot mint.** A case waiting on an artifact that does not
   exist yet cannot be hashed, so the launch records a **slot** for it instead.
5. **Submit**, journaling intent before calling the provider.
6. **Resolve slots** as the attempts they wait on settle: digest the artifact,
   mint the identity, insert the row, submit.

Steps 2 and 4 are the only ones that are not obvious, and they exist for one
reason each.

### Dependencies, and why they do not make this a workflow engine

Some measurements need something built first. s3-fast-list's hinted listing —
what the tool is actually built for — cannot run until a full listing has emitted
the key distribution its hints are cut from.

The dependency is **declared by the capsule**, because "this mode needs that
mode first" is a fact about the tool, exactly like which modes exist. The plan
says nothing; a row asking for the hinted mode is asking for whatever that mode
requires. The declaration is static, so `plan_cli` can still show a reviewer
that a plan yields twenty-two attempts rather than eleven without running
anything.

What keeps this honest is **what the consumer takes**: the artifact's *content
digest*, never the producing attempt's ID. Two preparations yielding identical
bytes produce a genuinely identical measurement, which should carry one
identity. Lineage is recorded separately, as context.

The same rule decides **who may produce it**: any successful attempt in the same
group whose shape says it publishes those bytes — tool, mode, config, target and
slices — rather than one nominated attempt id
([`model.md`](model.md) § *What a slot waits for is a shape, not a name*).
Nomination could not survive a retry, which settles under a new ordinal nothing
named; and it made a plan carrying both a `list` row and a `list-hinted` row list
the bucket twice with byte-identical argv, discarding one result. A standalone
preparation is minted only when the plan carries no candidate producer of its
own, and producer steps are expanded ahead of the slots that consume them so a
launch dying mid-expansion cannot leave a slot nothing in its group can pay.

This is the rule that must not bend: **an attempt may not depend on state
another attempt left behind unless that state is inside the hash.** A
preparation may therefore be a separate attempt exactly when its entire effect
is a content-addressable artifact. An effect no digest captures — a warmed
cache, mutated remote state — has to live inside the measured case's own
execution, where it is part of what was measured.

Two structural limits keep the dependency graph finite and knowable in advance:
a declared prerequisite names a mode of the **same capsule**, and the chain is
**declared statically**, so its full shape is readable before anything is
submitted. Chains are short — s3-fast-list's hinted path is one link, and nothing
has needed two — but the bound that matters is the declaration, not the number.

**A step is only a link if its artifact is shared.** s3-fast-list's `ks-tool
split` cuts the key distribution into ranges in under a second, and exactly one
measurement reads the result, so it runs as that measurement's untimed inline
setup exec rather than as an attempt of its own: same declaration discipline,
same offline expansion, one slot fewer and one VM fewer. The rule above is what
decides which a step is — a preparation is a separate attempt exactly when its
entire effect is a content-addressable artifact *other cases can bind to*.
See [`capsule-contract.md`](capsule-contract.md) § *A setup exec is not a chain
link*.

What the harness must never acquire is a graph discovered at run time: a step
that decides what comes next based on what it found. That is the line between a
reconciler and a workflow engine, and a statically declared chain stays on the
right side of it — the planner can print the whole expansion offline, which no
workflow engine can promise.

So a slot may wait on a slot, provided the declaration said it would.

### Why a slot is booked rather than remembered

A dependent case cannot be an `attempts` row, because that table is keyed by
identity and this case has none yet. A slot is *intent whose identity is still
incomplete* — one step earlier than `SUBMITTING`, which is intent whose identity
is complete and whose provider call has not happened.

Booking it durably buys two things, and the second is why it exists:

- A planner that dies between "the preparation succeeded" and "the measurement
  was submitted" leaves a record of what is owed.
- **A preparation that fails makes a comparison short one subject, and the slot
  is what notices.** Without slots, a failed bootstrap listing produces a
  clean-looking comparison quietly missing s3-fast-list's hinted arm — evidence
  that looks fine and is not, which is the failure this whole design is built
  against.

## Refuse rather than guess

The harness refuses in preference to guessing, everywhere, because every silent
fallback this study has hit produced evidence that looked correct:

- A stratum a capsule cannot issue is refused, not ignored. Six receipts in the
  first campaign recorded `authenticated` and ran unsigned.
- An undeclared config key is refused, not dropped. A misspelled knob would
  otherwise produce a sweep whose cells are all identical.
- A ledger whose `schema_version` is unrecognised is refused, never silently
  upgraded.
- A Dockerfile the slice rules cannot fully attribute is a build error.
- Submitting a case that already has a successful attempt is refused, rather
  than being treated as a no-op or an implicit repeat.

The pattern: a refusal costs an operator a minute, and a silent fallback costs a
campaign — usually discovered long after the numbers have been quoted.

## The limits worth knowing

Two things this architecture does not and cannot give you.

**Identity cannot cover the corpus.** These are public buckets that grow. Two
attempts of one case, same hash, six weeks apart, listed different data. What
stands in for control is *agreement* — subjects that agree on what they found
were measuring the same work. See [`identity.md`](identity.md).

**The ledger is not reconstructible from the evidence.** Losing it does not
destroy the evidence, but it costs the binding: `report` refuses results it
cannot tie back to a recorded row. Back it up.
