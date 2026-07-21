# Swath

[Swath](https://github.com/varveio/swath) lists an S3 bucket and emits the listing as TSV, JSON Lines, fixed-width text, or a Parquet dataset, driven by a work-stealing parallel scan in which idle workers steal and split key ranges so many ListObjectsV2 paginations run at once rather than one serial walk.
Swath is built and maintained by Varve, which also maintains this study, so we know it more deeply than the other tools — a familiarity we treat as a study limitation, not a licence to relax the run-record rules.
This study's groundwork is complete; no benchmark comparison has been run.

> **Varve builds Swath and maintains this study.** We apply the same harness,
> buckets, and run-record requirements to Swath as to every other tool, publish
> its results on the same terms whether or not they favour it, and welcome help
> from people who know the other tools better. Swath's earlier internal
> benchmark history is **not** used here — a number counts only once it is
> reproduced on this harness. See [Varve and Swath](#varve-and-swath).

## At a glance

The tested-subject facts are stated here; the canonical record is
[`data/tool.json`](data/tool.json).

| Question | Current answer |
| --- | --- |
| Tested subject | Built by the study from upstream's own Dockerfile at pinned revision `f1009db`, reporting version `0.1.0-SNAPSHOT`, run anonymously (`--no-sign-request`) with `--max-parallel-listings 8` and `--checkpoint none`. The image carries no source-SHA label, so the source-to-image binding is an agent-asserted build fact. Canonical identity: [`data/tool.json`](data/tool.json). |
| Exercised coverage | Four stdout listing modes — tsv, jsonl, aligned, and an un-seeded tsv variant — across the full bucket and three prefixes; parquet and sorted-parquet only as capability probes. No credentialed, edge-key, crash/resume, or benchmark run. |
| Correctness | The shared verifier PASSed every stdout run (`dups=0 missing=0 extra=0`) on this ASCII-keyed bucket — claim `smoke-output-complete-no-duplicates`. Parquet fidelity is unverified. |
| Smoke observation | The full-bucket recursive-tsv run self-reported eight concurrent listings in flight (`splits=7`, `steals=98`) — claim `full-run-reported-parallel-listings`. These are Swath's own self-reported counters from one groundwork run, not a benchmark result; they are internally consistent with the manifest-verified output of that same run but are not an independent wire capture. |
| Results | No benchmark or comparative result exists. |

## How it works

`swath list` always drives one work-stealing parallel-scan engine: the keyspace
is tiled into half-open key ranges, idle workers steal and split the busiest
peer's range at a probed midpoint, and each worker paginates its range with
`start-after` rather than a continuation token. A default shallow `delimiter=/`
seed discovers top-level prefixes; `--seed none` skips it and `--seed hints`
throws. Output streams as tsv, jsonl, aligned, or a Parquet dataset, with an
SQLite-checkpoint resume design and an AIMD controller for 503s. Full account:
[`docs/mechanism.md`](docs/mechanism.md).

## Modes and study coverage

Upstream's mode surface and what this study actually exercised are separate.

| Mode | Upstream purpose | What this study exercised |
| --- | --- | --- |
| `list` (tsv / jsonl / aligned) | Fully enumerate a bucket to a text stream. | Smoked anonymously across the full bucket and three prefixes; every run verifier-PASSed. |
| `list --seed none` | List with no up-front `delimiter=/` seed, parallelised by stealing alone. | Smoked on the full bucket and one prefix as a request-pattern variant. |
| `list --format parquet` / `--sort` | Write a multi-part (optionally globally key-sorted) Parquet dataset to a directory. | Run only as capability probes; the dataset output is uncapturable by the stdout-only wrapper, so fidelity is unverified. |
| Resume (`--checkpoint auto`) | Crash/Ctrl-C resume from an SQLite checkpoint. | Not run; smoke used `--checkpoint none`. |
| `inspect` / `diff` | Advertised subcommands. | Not run; both are unimplemented stubs. |

Swath has no `ls`-style shallow output mode and no hinted seed mode. Detailed
mechanism coverage is in [`docs/mechanism.md`](docs/mechanism.md); build and
mode-by-mode run coverage is in [`docs/running.md`](docs/running.md).

## What we learned

Each finding links its owning explanation and its canonical claim ID; claim IDs
resolve in [`data/claims.json`](data/claims.json).

- **The LISTs run genuinely in parallel, and it is scope-dependent.** The
  full-bucket run reported eight concurrent listings in flight; the small hourly
  prefix still hit that cap through steals alone, while un-seeded runs peaked
  lower — so scope size alone does not predict parallelism.
  [`Parallel scan engine`](docs/mechanism.md#parallel-scan-engine)
  · `full-run-reported-parallel-listings`, `peak-concurrency-is-scope-dependent`

- **Smoke settles the output, not the internal tiling.** Byte-exact PASSes prove
  no missing, extra, or duplicate rows for these clean single-shot runs; the
  disjoint-range, no-gap/no-overlap invariant stays an unverified design claim.
  [`The range model`](docs/mechanism.md#the-range-model)
  · `smoke-output-complete-no-duplicates`, `internal-tiling-is-disjoint`

- **There is no hinted mode, and the seed cost ran the opposite way.** `--seed
  hints` throws unimplemented, and on this flat-root corpus the up-front shallow
  seed made *fewer* API calls (339) than no seed (516) — the reverse of
  "discovery costs extra".
  [`Keyspace division`](docs/mechanism.md#keyspace-division)
  · `seed-hints-unimplemented`, `seed-cost-direction-at-smoke`

- **The AIMD 503 controller never engaged.** Every run recorded zero throttle
  events, zero AIMD votes, and zero errors on this clean public bucket; whether
  the controller is dead weight is a scale question smoke at T≤8 cannot reach.
  [`AIMD and retries`](docs/mechanism.md#aimd-and-retries)
  · `aimd-idle-at-smoke`, `aimd-necessity`

- **The headline durability and Parquet claims are unproven at smoke.**
  Crash-resume, exactly-once-under-kill, Parquet fidelity, and bounded memory at
  scale all stay unverified — none is reachable at this scale with
  `--checkpoint none` and a stdout-only wrapper.
  [`Deferred coverage`](docs/running.md#deferred-coverage)
  · `crash-resume-works`, `parquet-output-byte-exact`, `bounded-memory-at-scale`

## Limitations and open questions

### Coverage gaps

- No credentialed, edge-key (`EDGE_BUCKET=none`), crash/resume, or high-concurrency
  run. Byte-exact key fidelity is proven only for control-character-free keys —
  claim `text-sink-key-fidelity-ascii-only`.
- amd64 support is inferred from the Dockerfile but was never built or run; only
  arm64 has build+run evidence — claim `amd64-support-inferred`.

### Tool findings and risks

- **No OSS license** at `f1009db`, with `THIRD_PARTY_NOTICES.md` referencing a
  repository LICENSE that does not exist — a real gap for a repo slated to go
  public. Claim `no-license-dangling-reference`.
- **Pre-release posture:** `0.1.0-SNAPSHOT`, no releases or tags; `inspect` and
  `diff` are stubs. Claims `no-releases-or-tags`, `inspect-diff-are-stubs`.

### Benchmark questions

- The `--max-parallel-listings` sweep above the smoke cap of 8, probe-overhead
  versus scale, crash-resume and exactly-once under SIGKILL, Parquet fidelity and
  cost, bounded memory at scale, seed-pattern cost, the comparative arms, AIMD
  necessity, and an actual amd64 build+run. All currently unverified.

## Navigate this directory

| If you want to… | Go to… |
| --- | --- |
| Understand the scan engine, ranges, seeding, output, resume, and AIMD | [`docs/mechanism.md`](docs/mechanism.md) |
| See how the image was built and every mode that was or was not run | [`docs/running.md`](docs/running.md) |
| Inspect canonical identity, study states, and the full claim ledger | [`data/tool.json`](data/tool.json) and [`data/claims.json`](data/claims.json) |
| Integrate the subject with the shared harness | [`adapter/`](adapter/) |
| Audit how every old ledger row and prose claim became atomic claims | [`research/claims-migration.md`](research/claims-migration.md) and the preserved reconciliation in [`research/`](research/) |
| Read the historical pre-restructure landing page | [`research/tool-page.md`](research/tool-page.md) — frozen historical research, not the current entry point |
| Inspect the observations and immutable run records | [`receipts/`](receipts/) |

## Provenance

**Mixed provenance.** The mechanism claims, hypotheses, and weakness list were
seeded from Swath's own design documentation and remain inherited and — except
where a receipt is cited — unverified, not verified by anyone without a
stake in the answer. Layered on top is a firsthand source read of Swath at pinned
SHA `f1009db` plus the anonymous smoke runs, and a row-by-row reconciliation. The
seed was **not a run record**. See [`research/tool-page.md`](research/tool-page.md)
and [`research/reconciliation.md`](research/reconciliation.md).

## Evidence boundary

Source and documentation explain mechanisms and risks; only a committed receipt
confirms run-dependent behaviour, and Swath's self-reported counters are its own
account of its behaviour, not an independent wire capture. Smoke observations are
facts about single groundwork runs, not benchmark results.

## Varve and Swath

Before building Swath, Varve studied how existing listing tools approached the
problem, and that work informed Swath's design. We also know Swath's performance
envelope and tuning options more deeply than we know the other tools, which makes
us participants in the space we are studying. We wrote the comparison plan down
before the runs, use each tool's documented setup, put comparable effort into
tuning, and ask maintainers when we are unsure. Swath's earlier internal
benchmark history does not count as a result here; any number must be produced
again on this harness. The run records are published so readers can inspect and
improve the setup — this is a Varve-maintained project for the object-storage
community, not a sales comparison.
