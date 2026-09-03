# Swath v0.3.2 — patch-release delta record

Subject: swath **v0.3.2**, git tag `v0.3.2`, commit
`acf0d509f238832ffe2f0fb608951be33e99ae6f` (short `acf0d50`), released 2026-09-03.
Previous subject: v0.3.1, `7b9a5e2`, whose diff-driven re-derivation is
[`../report.md`](../report.md). This record describes how the capsule was moved from 0.3.1 to
0.3.2 and what bounds that move.

## What changed upstream

`git diff --stat 7b9a5e2..acf0d50` touches 50 files. Outside `docs/`, `site/`, the explainer and
release housekeeping (`CHANGELOG.md`, `docs/ops/dev/RELEASE_NOTES.md`, `gradle.properties`), the
change is one pull request, varveio/swath#209 (fixes #206), in 19 files under `swath-core` and
`swath-model`:

- `pipeline/Channel.java` and `pipeline/Pipeline.java`: the shared channel's wakeup discipline is a
  relay (one `signal` per released page, an admitted sender relays onward while budget remains)
  instead of a `signalAll` broadcast; the admission predicate, the over-budget single item and the
  50 ms backstop are unchanged.
- `model/PageBatch.java`, new `model/PageTally.java`, `output/RowTally.java`,
  `output/dataset/DatasetOutputStage.java`, `runtime/SortOutputStage.java`,
  `output/DiscardOutputStage.java`, `output/OutputStage.java`: per-page row tallies are computed on
  the fetch worker and merged by the consumer in constant time; the stdout formatter keeps its
  per-entry tally.
- `observability/RunMetrics.java`: the `channel_receive` client-cost span.
- Tests for the above.

No CLI option, exit code, output byte, checkpoint format, or resume rule changed. The 0.3.2
release notes say so and the diff agrees.

## Method: a mechanical bound, then a re-read of what the bound excludes

[`tool-onboarding.md`](../../../../docs/operating/tool-onboarding.md) § Re-deriving says a subject
change is re-derived blind. As at 0.3.1 this record deviates, and more narrowly: for a patch
release whose functional change is one pull request, the 0.3.1 ledger was kept and the move was
bounded mechanically.

1. Every `source` anchor in [`claims.json`](../../data/claims.json) was intersected with the list
   of files that differ between `7b9a5e2` and `acf0d50`. Of 418 anchors over 119 files, **409
   cite files that are byte-identical at the two commits**; those anchors keep citing `7b9a5e2`
   and their claims stand as read at 0.3.1, because the cited lines are the same bytes. The
   anchor checker verifies each anchor against a checkout at the commit it cites, so a
   mixed-commit ledger is checkable: run it with two `--source-root`s, one at `7b9a5e2` and one at
   `acf0d50`.
2. The **nine anchors on seven changed files** were re-read at `acf0d50` and re-anchored there:
   `output-is-streaming` (three anchors: `OutputStage.writeBatch` still writes each entry straight
   through the formatter; `RowTally`'s four counters; the discard stage's per-page path, now a
   constant-time merge), `discard-sink-measures-listing-engine` (javadoc unchanged; qualification
   extended: the tally it retains is now computed on the fetch worker),
   `queue-size-is-entry-budget` (the budget contract paragraph, the unbounded backing queue, the
   weigher and the admission loop, which now also relays a signal; the proposition is unchanged),
   `api-calls-counter-is-trustworthy` (the two `RunMetrics` sites moved by five and twenty lines),
   `seed-endpoint-unreachable-fails-fast` (the `CHANGELOG.md` and `metrics-internals.md` lines
   moved). None of the five propositions changed status or disposition.
3. **Two claims were added** for the behaviour that is new at 0.3.2 and touched none of the old
   question set: `channel-wakeup-is-relay-not-broadcast` and `page-tally-computed-on-fetch-worker`,
   anchored at `acf0d50` and carrying this study's own measurement of the effect.
4. The **identity claims** were re-observed against the published 0.3.2 image (below):
   `published-image-is-anonymously-pullable`, `image-label-binds-to-source-commit`,
   `image-self-reports-v020`, `upstream-publishes-tagged-releases`, and the adapter round-trip
   `round-trip-count-and-cross-mode-agreement`.

What this bound does not do: it does not re-read the 409 unchanged anchors, so any claim that was
wrong at 0.3.1 is still wrong here; and it does not sweep the 0.3.2 tree for behaviours the old
question set never asked about, beyond the one pull request's own surface.

## Image identity, observed directly

- Index: `ghcr.io/varveio/swath@sha256:0bbc96c10b4b63d184cce76734679dd4a2f54a1c81c7c94c1000f7114eab8e43`, registry tag `0.3.2` (no `v`; `v0.3.2` is
  `manifest unknown`, as at every prior release). `linux/amd64` child
  `sha256:54ec1384abab6bbb7a84418315261539e3907d0b8c65cf31fa75ab66cc5118a7`; both per-arch config blobs carry
  `org.opencontainers.image.revision` = `acf0d509f238832ffe2f0fb608951be33e99ae6f` and
  `org.opencontainers.image.version` = `0.3.2`.
- `--version` self-report: `swath 0.3.2, Commit: acf0d509f238, Runtime: 25.0.4+7-LTS`.
- Nine `v`-prefixed tags now exist, v0.1.0 (2026-07-27) through v0.3.2 (2026-09-03).

Recorded on 2026-09-03 from direct `docker` calls on the study maintainer's workstation; no
receipt.

## Adapter round-trip on the 0.3.2 image

Every mode `adapter/command.py` declares was run on the 0.3.2 image over
`s3://noaa-normals-pds/normals-hourly/` and read back through `adapter/normalize.py`, exactly as
for 0.3.1. Results, digests and the retained stderr streams are in
[`../../receipts/observations-v0.3.2/adapter-modes/`](../../receipts/observations-v0.3.2/adapter-modes/);
the 0.3.1 observation is kept beside it for comparison. It is an observation, not a receipt, and
not a completeness check.

## The measurement that motivated the release

The study's own live-S3 rows on sentinel-cogs (1.07 billion objects, compressed TSV dataset,
`--concurrency 2048`, GCP `us-east1`), single runs a day apart:

| shape | 0.3.1 listing phase | 0.3.2 listing phase |
| --- | ---: | ---: |
| 32 vCPU, 16 writers | 5 m 12 s (`swath.7b028bd8c692.s1`) | 4 m 25 s (`swath.6bdbca2ec124.s1`, dev image `cc052c9`) |
| 64 vCPU, 32 writers | 5 m 32 s (`swath.31d7484be314.s1`) | 3 m 11 s (`swath.a668d615d1fd.s1`, dev image `cc052c9`) |

The 0.3.2 figures above were taken on the merged commit's dev image before the tag existed; the
release rows themselves are re-run on the published image and exported separately.
