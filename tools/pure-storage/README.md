# Pure Storage / Joshua Robinson — "67 billion objects in 1 bucket"

> **Status: `UNVERIFIABLE` with the resources we have.** Not a tool — a published result, on
> hardware we do not have. See [Why this is untestable](#why-this-is-untestable).

|  |  |
|---|---|
| **Repo** | None — a blog post, not a released tool |
| **Language** | Go (rewritten from Python) |
| **License** | n/a |
| **Version reviewed** | n/a |
| **Tier** | 4 — not testable |
| **Testability** | **None.** Requires Pure FlashBlade hardware. |
| **Source** | <https://joshua-robinson.medium.com/listing-67-billion-objects-in-1-bucket-806e4895130f> — and a second copy at <https://blog.everpuredata.com/purely-technical/listing-67-billion-objects-in-1-bucket/>. Both recorded because a dead Medium link is exactly the failure mode here. |

## Mechanism — what we believe it did

Known-distribution partitioning. The bucket used uniformly-distributed
random-string keys, so the keyspace could be partitioned trivially as `a*`, `b*`,
… `z*` — 26 parallel listings, no discovery required.

This is a simple strategy when the layout supports it: it needs no runtime
discovery because the writer already did the work. It only applies when you know
the keyspace is uniform on the leading character, which most real buckets aren't.

## Claimed numbers

| Metric | Value |
| --- | --- |
| Rate | 430K keys/sec |
| Scale | 67 billion objects |
| Wall-clock | 43 hours (Go) |
| Prior implementation | 169 hours (Python, asyncio + ProcessPoolExecutor) |

**These numbers are not comparable to the AWS runs in this study**, so we keep
them in a separate table with their hardware context.

## Why this is untestable

It ran on **Pure FlashBlade hardware, not AWS S3**. We do not have a FlashBlade.
There is no released tool to obtain, and no way to reproduce the environment.

430K keys/sec is a striking number and it
circulates in this space as though it were an S3 result — Swath's own prior-art
notes came close to making that mistake, catching it only in a parenthetical.
We mark it `UNVERIFIABLE` and keep it out of the comparison tables because we
cannot recreate its hardware environment.

## What survives as usable

Two things worth carrying forward:

1. **The strategy is real and correct** — known-distribution partitioning works
   trivially when you control key layout at write time. Hash-prefixing keys turns
   enumeration into an N-way trivially parallel job, permanently. For a bucket
   you own, this can be a good fit. AWS's own performance guidance has long
   recommended it.

2. **The Python→Go 3.93× speedup is a testable hypothesis.** A ~4× speedup on a
   nominally *I/O-bound* workload suggests that at high list rates, client-side
   deserialization and bookkeeping dominate rather than the network. Our tool set
   spans Python (aws-cli, s4cmd), Go (s5cmd, rclone, mc, s3kor), Rust (s3ls-rs,
   s3-fast-list), Node (S3P), and Java (Swath), giving us several implementations
   to compare. See [`../../docs/open-questions.md`](../../docs/open-questions.md).

That second point is why the page is kept despite being
untestable: the *result* is unreproducible, but the *hypothesis it suggests* is
one we can test on our own box.

## Provenance

The Medium post above (mirrored on the Pure Storage blog). Never executed by us
and never executable by us.

One claim on this page is **not** from that post: the recommendation to hash-prefix
keys at write time is attributed separately to AWS's own performance guidance.

## Receipts

_None, and none possible._
