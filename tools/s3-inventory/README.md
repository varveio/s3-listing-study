# S3 Inventory / S3 Metadata

> **Not a tool under test — an alternative that changes when live listing is
> useful.** We include it so readers can see when a scheduled inventory may fit
> better than a live listing tool.

|  |  |
|---|---|
| **Repo** | n/a — AWS managed services |
| **Language** | n/a |
| **License** | n/a |
| **Tier** | 4 — not a subject |
| **Testability** | Requires per-bucket enablement and (for Inventory) up to 48h to first delivery |
| **Source** | https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-inventory.html |

## What they are

**S3 Inventory** — scheduled daily/weekly manifests of every object in a bucket,
delivered to a destination bucket as CSV, ORC, or Parquet. Up to 48h to first
delivery; eventually consistent. AWS frames it explicitly as "a scheduled
alternative to the ... List API." Queryable directly with Athena, Redshift
Spectrum, or DuckDB. Claimed to now support S3 Express One Zone (April 2026).

**S3 Metadata** (GA 2025-01-27) — read-only managed Iceberg tables. A journal
table (change log, claimed fresh "within minutes") plus a live inventory table
(claimed ~1h refresh after backfill). Queryable via Athena/Spark/Trino. Must be
pre-enabled per bucket.

> One inherited claim needs care: an internal review flagged "S3 Metadata is
> minutes-fresh" as an **overclaim**. Per AWS's own docs, the *journal* table is
> near-real-time; the *live inventory* table lags ~1h after backfill. Those are
> different tables with different freshness, and the distinction gets flattened
> in casual retellings — including, apparently, in ours.

## Why this page exists

The practical framing:

**If a fresh Inventory or Metadata table is available and queryable, it is
strictly cheaper than running any tool in this study.** Every tool here exists
for buckets where that isn't an option — not enabled, too stale, or not yours.

The survey material this repo inherited puts it this way: *most use cases
that think they need a fast bucket lister actually need a daily inventory*, and
it calls this "the hardest-to-internalise lesson." Our comparison should keep
the option of not running a live listing tool visible.

## The cost model — widely mis-stated

It is routinely got wrong by an order of
magnitude, in a direction that flatters this entire tool category:

| | Cost for 1B objects |
| --- | --- |
| Live LIST | **~$5** — $0.005 per 1000 requests × 1000 objects per request ≈ 1M requests |
| S3 Inventory | **~$2.50** — ~$0.0025 per million objects |

The folklore figure of thousands of dollars comes from mis-costing LIST as one
request *per object*. It isn't.

So Inventory's edge on API dollars is real but **modest** — about 2×, on a bill
that is single-digit dollars either way. Its decisive advantages are elsewhere:
no client compute, no hours of wall-clock, no throttling load on the account, and
Parquet delivered ready to query.

This cuts both ways:

- **When scheduled data is enough**: if you can tolerate 24h staleness, you may
  not need to run any of these live listing tools.
- **For the tool category**: the API bill is *not* the reason to prefer
  Inventory, so "listing is expensive" is a bad argument for it; halving API calls is only one small part of the tradeoff for any tool in
  this study, including Swath. In the example above, that saves $2.50 on a $5
  API bill; the larger differences are freshness, compute, wall-clock time, and
  operational load.

## What to verify

1. The cost arithmetic above.
2. The freshness SLAs in practice — particularly the journal-vs-live-inventory
   distinction flagged in the overclaim note above.
3. Whether Express One Zone support actually landed as claimed (April 2026).

## Provenance

AWS documentation and announcements, plus an internal review that corrected the
freshness framing. Nothing here has been verified by us.

## Receipts

_None yet._
