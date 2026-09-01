# Public results

Each directory under `results/` is one immutable release of this study's public
result data. A release is generated, never hand-edited.

## Release contract

- **Immutable.** Once a release directory is committed, its files are not
  revised in place. A correction is a new release whose `manifest.json` names
  what it supersedes, plus an erratum note in the superseded release.
- **Canonical versus original.** `attempts.jsonl` is the canonical *public*
  dataset. It is not the original evidence: the campaign ledger, the provider
  request, the per-attempt `result.json`, the console logs and the listing
  products stay private. Every row's `evidence[]` names what exists, with a
  digest and the reason it is not published.
- **Generated.** `summary.csv`, the charts and their CSVs are derived from
  `attempts.jsonl`. Never edit them; regenerate.
- **Bounded claims.** `manifest.json.claim_ceiling` states in machine-readable
  form what the release may be used to claim. Prose and figures are bounded by
  it.

## Files in a release

| File | Role |
| --- | --- |
| `manifest.json` | Identity, status, claim ceiling, commit, counts, checksums, disclosures |
| `attempts.jsonl` | One compact JSON object per attempt — the canonical dataset |
| `summary.csv` | Flat scalar view of the same rows, for spreadsheets |
| `fixtures.json` | Per replay fixture: source, digest, object count, shape, latency, availability |
| `subjects.json` | Per tool: versions, image and slice digests, modes seen, capsule path |
| `charts/*.svg`, `charts/*.csv` | Deterministic figures and the exact rows behind each one |
| `checksums.sha256` | SHA-256 of every other file in the release |

`results/latest.json` points at the most recent release. Durable claims should
cite an immutable release path, not `latest`.

## Schema versions

- Row schema: `1` (`attempts.jsonl`, field `schema_version`)
- Exporter: `1` (`manifest.json.source.exporter_version`)
- Derived-rate formula: `1`
  (`derived.wall_keys_per_second = row_count / wall_seconds`)

A reader that understands version *N* must refuse a file at *N+1* rather than
reinterpret it.

## Nulls, failures, and diagnostics

An unavailable metric is `null` — never `0`, never `"-"`, never an empty
string. Failed, cancelled and evidence-less attempts are rows, not omissions.
`classification.publication_status` never says `measurement` unless the
campaign's own gate says the row is a publishable measurement: `purpose ==
measurement`, `capacity_status == CALIBRATED`, and `replay_timing ==
TIMING_VALID`.

## Correction policy

1. Never rewrite a committed release.
2. Publish a new release with `status: erratum` (or a normal release naming the
   supersession) and record what changed.
3. Add the erratum pointer to the superseded release's manifest in the same
   commit as the new release.

## Regenerating and validating

```
uv run python -m benchmark.public_export \
    --state <campaign.db> \
    --release benchmark/publication/2026-09-scale-diagnostics.yaml \
    --output results
uv run python -m benchmark.public_validate --release-dir results/2026-09-scale-diagnostics
```

The exporter needs the private ledger and the private evidence store. The
validator does not: it reads only the committed files, and CI runs it on every
change.

## Releases

- [`2026-09-scale-diagnostics/`](2026-09-scale-diagnostics/) — Scale-study replay diagnostics
  (`diagnostic`)
