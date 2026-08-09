# Runnable tool directory structure

This is the authoritative structure and content contract for runnable tools in
this study. It explains what each part of `tools/<tool>/` is for, which reader
it serves, and where the boundaries lie between current explanation, canonical
data, historical research, executable integration, and evidence. Throughout,
a runnable tool directory in this shape is called a *capsule*.

All eleven runnable tools implement this contract: ten were converted in the
migration wave completed 2026-07-20, and one — `swath` — was written directly in
this shape and carries no migration stratum. The migration stratum described
below therefore exists **where present**, not on every capsule: a born-canonical
capsule has no frozen pre-restructure page, no conservation audit, and no
`legacy_origins` on any claim. A tool's current README must describe the files
that actually exist in its directory.

This contract does not apply to contextual entries such as `pure-storage` and
`s3-inventory`. A useful contextual entry may remain a directory containing
only a README.

## Design rules

1. **One public landing page.** The tool-directory root contains a concise
   `README.md`, not a mixture of documentation, JSON, shell, and Docker files.
2. **Group by function.** A reader chooses `data/`, `docs/`, `adapter/`,
   optional `build/`, `research/`, or `receipts/` according to what they need
   to do.
3. **Keep one source for each fact.** Current structured identity and claims
   live in JSON. Markdown explains them and links to them; it does not reproduce
   the full machine-readable ledger.
4. **Separate current explanation from history.** `docs/` describes the current
   understanding. `research/` preserves how that understanding was derived and
   reviewed.
5. **Preserve evidence in its native form.** Receipts and raw artifacts are not
   converted merely to make the tree look uniform.
6. **Uniformity has limits.** `build/` and adapter fixtures exist only where
   they serve a real function. Contextual entries do not receive empty capsule
   directories.

## Directory map

```text
tools/<tool>/
  README.md                  public orientation and reader routes
  data/                      canonical machine-readable current facts
    tool.json                identity, tested subject, and study states
    claims.json              atomic current claims and their evidence
  docs/                      current human explanation
    mechanism.md             how the tool works
    running.md               how this study built, ran, and checked it
  adapter/                   shared Python integration
    command.py               typed friendly parameters to exact subject argv
    normalize.py             converts native output to the five-field contract
    fixtures/                synthetic adapter QA, only where applicable
  build/                     optional tool-owned build inputs
    Dockerfile               local subject build recipe, where needed
    image.json               derived-image inputs, where needed
  research/                  preserved derivation and review history
    tool-page.md             frozen full pre-restructure landing page
    claims-migration.md      audit map from the old ledger to claims.json
    report.md                detailed source-and-run research
    reconciliation.md        historical claim-by-claim reconciliation
    codex-review.md          preserved independent-review record
  receipts/                  immutable observations and raw run records
```

The shortest reader routes are:

| Reader | Start | Continue to |
| --- | --- | --- |
| Curious evaluator | `README.md` | `docs/` for explanation; `data/claims.json` for the full ledger |
| Run operator | `docs/running.md` | `adapter/`, optional `build/`, and `receipts/` |
| Mechanism reviewer | `docs/mechanism.md` | source evidence in `data/claims.json` and derivation in `research/` |
| Evidence auditor | `data/` | `research/claims-migration.md`, `research/`, and `receipts/` |

## Markdown content contracts

### `README.md` — public landing page

The README answers: what is this tool, what did this study learn, what remains
unknown, and where should I go next?

Its normal order is:

1. Three short introductory sentences: what the tool *does* — it lists a
   bucket — stated before its principal output format, with the upstream
   project linked and the distinctive listing or concurrency mechanism glossed
   in plain terms; any remaining upstream-ownership or fork context; and stable
   study status. While the repository remains in its current groundwork state,
   the third sentence is exactly: "This study's groundwork is complete; no
   benchmark comparison has been run."

   That wording is deliberately true for run and blocked subjects alike — a
   per-tool sentence claiming the tool was built or run would be false for
   credential-blocked subjects. Change the shared sentence only when the
   repository's actual study phase changes.
2. **At a glance:** the tested-subject facts stated inline — fork, patch, built
   revision, and how it was run — with the canonical record in `data/tool.json`
   linked second rather than in place of the facts; exercised coverage;
   correctness/verifier state; and whether benchmark results exist. One guarded
   concrete smoke observation may appear here, clearly labelled as a single-run
   groundwork fact, not a benchmark result.
3. **How it works:** at most eight lines summarising how the tool lists,
   parallelises, retains state, and emits output, with a link to
   `docs/mechanism.md` for the full account. This section follows **At a
   glance**.
4. **Modes and study coverage:** upstream purpose separated from what this
   study actually exercised.
5. **What we learned:** three to five high-signal findings. Each bullet is a
   bold plain-English claim, then a one-to-two-sentence explanation, then a link
   to the owning doc section and the stable claim ID as a code span. One note at
   the top of the section states that claim IDs resolve in `data/claims.json`;
   individual bullets do not repeat that pointer.
6. **Limitations and open questions:** coverage gaps, harness or verifier
   blockers, benchmark questions, and tool risks kept distinct.
7. **Navigate this directory:** reader-intent descriptions of every functional
   directory that exists. The word "capsule" is internal jargon and does not
   appear on a public tool page.
8. **Provenance:** firsthand study work distinguished from the inherited
   secondhand seed, with links to the frozen page and reconciliation.
9. **Evidence boundary:** a short distinction between source support,
   receipt-backed runtime confirmation, smoke observations, and benchmarks.
10. The maintainer disclosure on Swath's page.

Keep setup commands, exact revision/authentication tables, the full claim
ledger, source-anchor walls, receipt inventories, and schema explanations out
of the landing page. Those details remain one or two links away.

### `docs/mechanism.md` — current technical explanation

This page answers how the tool lists, parallelizes, retains state, emits output,
and fails. It normally contains:

- a short scope, tested-subject, and evidence-label preface;
- architecture and task/request model;
- listing, pagination, and concurrency behavior;
- state, buffering, and memory behavior;
- output and normalization-relevant behavior;
- errors, retries, interruption, and resume behavior; and
- material source-supported limitations or risks, clearly distinguished from
  run observations.

Headings should follow the tool rather than a rigid template. Keep run diaries,
benchmark conclusions, duplicated landing-page summaries, and inherited claims
presented as observations out of this page.

### `docs/running.md` — operator and reproduction guide

This page answers what was built or selected, which modes ran or were blocked,
what the harness could verify, and how to reproduce the supported work. It
normally contains:

- a pointer to canonical tested identity in `data/tool.json`;
- build, install, or image-selection procedure;
- the adapter and harness contract;
- mode-by-mode exercised and blocked coverage;
- receipt-backed facts about individual runs;
- verifier, output-capture, or normalization limitations;
- current reproduction commands; and
- explicitly deferred coverage.

Keep general mechanism essays, unlabelled comparisons, the complete claim
ledger, and reconstructed commands or evidence out of this page. When run facts
and a later verification or direct-capture path are not bound to the same
execution, say so explicitly.

### Current-doc conventions

`docs/mechanism.md` and `docs/running.md` describe current understanding, so
they follow the canonical vocabulary and leave review history to `research/`:

- Cite canonical claim IDs, never review-round labels such as "Round 2, F1".
  Define the reference notation once per capsule — in the page preamble, or by
  pointing at the sibling current doc that defines it — and cite tersely, claim
  `some-id`, rather than repeating a "canonical claim …" formula. A
  correction, once accepted, is stated as current truth; its history lives in
  the claim's `disposition` field and in `research/`.
- For a sentence represented by a canonical claim, cite the claim ID and let
  `data/claims.json` own its structured source, documentation, observation, and
  run anchors. Keep inline `[SRC]`, `[DOC]`, and similar anchors for narrative
  that is not represented by a canonical claim. This is the end-state rule for
  new or materially edited current prose; the migration-preserved pages sealed
  in PR #22 are not churned solely to remove otherwise-correct duplicate
  anchors.
- Use the canonical status vocabulary (`unverified`, `unverifiable`,
  `supported`, `confirmed`), not the legacy `VERIFIED: no` or "Unaddressed".
- Give each caveat exactly one owning location — normally `running.md` or the
  claim's qualification. Everywhere else the caveat appears as a short clause
  with a link, not a re-derivation.
- `mechanism.md` carries no smoke-status or mode-coverage column; coverage is
  summarised in the README and detailed in `running.md`.

### `research/*.md` — preserved derivation

`research/tool-page.md` is the complete pre-restructure landing page with a
standard dated warning. Its prose stays frozen; only the warning and necessary
Markdown link targets may differ.

The existing `report.md`, `reconciliation.md`, and `codex-review.md` retain
their original roles and wording:

- `report.md` records the detailed source-first and run investigation;
- `reconciliation.md` records how inherited material compared with groundwork
  evidence; and
- `codex-review.md` records the earlier independent review and its resolutions.

These are audit history, not current navigation or canonical claim status.

### `research/claims-migration.md` — conservation audit

A conservation audit is derivation history, not current explanation, so it lives
in `research/`. This is a human-auditable map, not a second claim ledger. It
contains:

- a short description of the legacy source and evidence/disposition semantics;
- exactly one row for every declared legacy origin; and
- every atomic `data/claims.json` ID derived from that origin.

Every **status-bearing** legacy origin is declared and mapped. Pure identity
rows (repository, language, license, tested variant) and study-bookkeeping
rows (tier) may instead be conserved canonically — in `data/tool.json` or the
`tools/README.md` catalog — and are then **named in the map preamble with their
conservation location**. A correction or contradiction is always
status-bearing, wherever it appears: it gets a declared origin and an atomic
claim carrying its disposition. An identity-table row that purely restates a
ledger row is conserved by that row and needs no separate label.

The validator checks the map in both directions. New analysis and full claim
field renderings do not belong here.

## Canonical data

`data/tool.json` records the stable tool identity, upstream and tested-subject
relationship, provenance-bearing revision/version fields when known, explicit
study states, benchmark eligibility, and evidence roots. It does not become a
general metadata dumping ground. Fork and upstream identities remain explicit
rather than being flattened into one repository field.

Tested version, revision, and upstream-base values are optional; when present,
each has a resolvable provenance reference. Prefer the strongest committed
receipt that itself records the fact; fall back to `research/` prose only where
no receipt records it. Never overstate: a caller-supplied receipt field is
weaker than a tool self-report or a build log, so a field recorded only as
caller-supplied metadata keeps its research-prose reference rather than being
aimed at a receipt that does not independently record it.

`data/claims.json` is the canonical current claim ledger. Each record has:

- one atomic proposition;
- one evidence-strength status;
- one historical disposition relative to the inherited wording;
- a concise qualification;
- every legacy origin it conserves; and
- structured evidence that supports that exact proposition.

Evidence strength and correction history are separate. A source anchor can
support a mechanism but cannot confirm runtime behavior. A run receipt can
confirm its exact exit or resource fact but does not imply correctness when the
verifier was blocked. Unverified and unverifiable propositions carry explicit
reasons rather than unrelated positive evidence.

The controlled values are:

| Field | Values and meaning |
| --- | --- |
| `status` | `confirmed` requires evidence from the exact receipted run or build; `supported` requires source, documentation, observation, or run evidence; `unverified` is testable but not settled; `unverifiable` cannot be settled from surviving public evidence. The latter two carry only `none` evidence with a reason. |
| `disposition` | `retained`, `corrected`, or `contradicted`; this describes the relationship to inherited wording and never substitutes for evidence strength. |
| evidence `kind` | `source`, `documentation`, `run`, `observation`, or `none`. Each is a closed record shape and may contain only fields belonging to that kind. |

Source evidence identifies `tested-variant` or `upstream`; anchors from a fork
use the former and must agree with `tool.json`. Every claim lists all legacy
origins it conserves. The expected-origin set includes deterministic `PROSE`
labels for status-bearing propositions outside tables, and
`research/claims-migration.md` lists every atomic claim derived from each origin.
Statements and qualifications are plain text rather than embedded Markdown.

Canonical JSON validates against `schemas/tool.schema.json` and
`schemas/claims.schema.json`. No generated Markdown, HTML, or catalog JSON is
committed beside it. A future viewer may render the JSON at read time while
always linking back to the source record.

## Executable integration and builds

`adapter/command.py` implements the shared typed command contract. It exports
`build_command(CommandRequest) -> tuple[str, ...]`, returning the complete
subject argv including the executable. The shared image build bundles exactly
one selected adapter; the in-image driver loads it and compiles argv from the
scheduler's typed logical request. The adapter
never runs Docker or the tool; the attempt engine owns execution, credentials,
timeouts, and measurement. Its argparse boundary prints JSON only for operator
inspection and normal `--help`, not as a scheduler transport.
Optional concurrency is a typed logical field, never an environment lookup.
Adapters reject it unless they explicitly declare a supported range.

`adapter/normalize.py` converts the tool's native output into the historical
verifier's normalized stream. Each module keeps its library `normalize`
function clean and uses the shared argparse boundary in
`s3_listing_study.normalizer_cli`. That stream is an executable compatibility
boundary, not a stored canonical result.

`adapter/fixtures/` is allowed only for synthetic adapter QA that already
exists. Observed captures remain receipts. The current classified exceptions
are documented in the migration playbook.

`build/Dockerfile` exists only when the study carries a local subject build
recipe; its header explains deviations from upstream and points to relevant
research. `build/image.json` may instead record declarative inputs for the
single shared `harness/derived-image/Dockerfile`. Tool capsules never copy or
fork that shared attempt-runner recipe. Tools using an upstream image without
derivation or another installation path do not receive an empty `build/`
directory.
Construction is slug-only through `s3-listing-study build-derived-image`; the
command validates capsule containment and binds four named contexts — the
registered `subject` image, the capsule's `adapter` and `selection` directories,
and the study's pinned `python` interpreter, which the engine runs on so that a
subject image need not ship one. `adapter_bundle_sha256` binds a documented
canonical byte manifest of `command.py` and `normalize.py` and is the sole
adapter identity in a new `result.json`.

`image.json` declares, besides the adapter paths and that digest: the
digest-pinned `subject_image`; `subject_version`, the release that digest
contains, which names the derived image and is distinct from `data/tool.json`'s
`tested.version`; `python_libc`, selecting the pinned interpreter build for the
subject's base (`musl` for Alpine, otherwise `gnu`); `subject_workdir`; and
`executable`, which must equal the adapter's `fixed_command_prefix`. With no
`--tag`, the derived image is named
`s3-listing-study/<tool>:<subject_version>-h<harness version>-<digest prefix>`,
so it cannot be mistaken for the upstream image it wraps.

## Receipts and raw formats

`receipts/` contains run records and observations: invocations, environment,
image identity, exit status, timing and memory capture, verifier results,
stdout/stderr, hashes, and capability investigations. Its internal legacy
shape is not normalized during this restructure.

Machine-readable current records should be JSON, but raw evidence stays in the
format actually produced: Markdown receipts, JSON metadata, logs, Parquet,
stdout, stderr, hashes, or other native artifacts. Converting or deduplicating
those files would change the evidence rather than improve its organization.

## Duplication boundary

Some repetition is necessary and intentional:

- the README states a high-level finding;
- a current doc explains it;
- `claims.json` records the exact proposition and evidence state; and
- a source anchor or receipt supports it.

Avoid repeating full ledgers, command manuals, identity tables, source-anchor
walls, research narratives, or generated views. Prefer a short explanation and
a precise link to the owning layer.

## Lifecycle

A capsule's layers do not share one mutability rule or one end-of-life. Treat
them by this table:

| Layer | Mutability | End of life |
| --- | --- | --- |
| `receipts/` | Immutable evidence | Permanent while the capsule describes the subject they record; removed only with a subject retirement (below) |
| `research/report.md`, `reconciliation.md`, `codex-review.md` | Append-only derivation records | Retirable only through a deliberate promote-or-waive pass at repo finalization |
| Migration stratum — `research/tool-page.md`, `research/claims-migration.md`, `legacy_origins` in `claims.json`, the validator's conservation checks, and the playbook | Frozen once proven | Sealed at wave end; removed only with the whole subject under subject retirement (below), never on its own |
| `README.md`, `docs/`, `data/` | Living | Kept current |

Receipts are the product every `confirmed` claim cites, so they are never
deleted to tidy a directory, never rewritten, and never dropped because a claim
that cited them changed. There is exactly one exception, added 2026-08-02 after
the first occurrence: **subject retirement.** When a capsule is retargeted to a
new upstream version and the previous subject was never released, the owner may
retire that subject wholesale — its receipts, its derivation records, and its
migration stratum together. The rule is all-or-nothing and its rationale is that
run records for a subject the capsule no longer describes are worse than absent:
a reader finds them under `receipts/` and reasonably reads them as evidence
about the current subject. Four conditions bind it, and all four are checkable after the fact:

1. **The criterion is result-independent.** Retirement is permitted because the
   subject was superseded and never released — never because of what its
   evidence showed. A retirement argued from an inconvenient result is
   prohibited, and the argument must be recorded before the deletion.
2. **A tombstone survives.** The capsule records what was retired, when, why,
   and the commit at which the records remain reachable. Deletion may remove the
   files; it may not remove the fact that they existed.
3. **No dangling references.** No claim, page or index may cite the removed
   evidence afterwards, and the reference audit is part of the retirement.
4. **All-or-nothing.** Retiring *some* of a subject's receipts while keeping
   others is not permitted — that is exactly the tidying the never-delete rule
   exists to forbid.

Condition 1 is the one that matters and the one a reader cannot verify from the
diff alone, which is why it is written down: the rule is deliberately easier to
audit than to abuse, but it is not self-enforcing, and an owner who wanted to
bury a result could satisfy conditions 2 through 4 while lying about 1. `swath` is the worked example: its v0.1.0-era receipts,
research and stratum were retired together when the capsule moved to v0.2.0.

The derivation records otherwise retire only once their conclusions are
conserved in the ledger and provenance no longer points at them — a
promote-or-waive pass, not a bare deletion. The migration stratum exists solely to prove the restructure lost
nothing; deleting the frozen tool page would turn every `legacy_origins` value
into an unresolvable label, so it is sealed rather than removed.

## Changing or adopting this structure

Changes under `tools/` use an owner-reviewed PR. During migration, receipts
remain byte-identical except for the documented synthetic-fixture exceptions;
historical research receives only approved link-target repairs; and every old
claim maps to canonical atomic claims.

Use the sealed Runnable-tool capsule migration playbook for the historical
conversion procedure, exceptions, commands, stop conditions, and review return
format. Validate the living capsule contract with:

```sh
uv run s3-listing-study validate-capsule --tool <tool>
```

The gate is `s3_listing_study.capsule`, part of the packaged CLI: it validates
`data/tool.json` and `data/claims.json` against the Draft 2020-12 schemas in
`schemas/`, checks evidence references, verifies the root layout and the README
contract, and resolves local Markdown links and fragments. Run it from the
project environment (`uv sync`) — it needs `jsonschema`.

The completed migration's conservation, research-preservation, and receipt
checks are a frozen regression, separate from the living contract. It checks
legacy-claim conservation, preserved research, receipt immutability, and the two
synthetic-fixture reclassifications against a commit where
`tools/<tool>/README.md` is still the historical pre-capsule page — since PR #22
merged, the last pre-migration commit on `main`, not `main` itself:

```sh
uv run s3-listing-study validate-capsule --tool <tool> \
  --migration-base f5beafd4d8e83a605af38aa7e22a75d94cbaa50b
```

CI runs the living contract for every runnable tool as a separately named check;
`--base` remains an alias for `--migration-base` only so the sealed playbook's
recorded commands continue to work.

The playbook explains **how to convert** an existing directory. This document
defines **what the resulting directory means**.
