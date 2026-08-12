# simple/ — a happy-path sketch of this study's bench machinery

This directory is **not part of the study**. It is an unrun demonstration
that the *bench-machinery* half of this pipeline -- submit, run, collect,
compare -- can be built in plain Python using standard means (subprocess,
sqlite3, the real cloud SDKs). Nobody should run this: no CI wires it into
the real pipeline, no test suite exists, and every round was written to a
closed, reviewed allowlist of scope (see the "Round N" sections below).

## Architecture: two models, one seam

Round 3 redraws the boundary the sketch stands on. **Bench machinery**
(submit a job, run a subject, capture what happened, compare two listings,
report) is mostly infrastructure plumbing -- generic across all eleven
tools, replaceable by standard means without losing anything real; this is
what `simple/` sketches. **Tool/domain model** (one capsule per tool's exact
CLI shape, the byte-framed contract a normalizer emits into, a plan's
case-expansion and fingerprinting rules) is *warranted complexity*: real,
hard-won knowledge about eleven tools' sharp edges (s4cmd's fixed-width
columns splitting multi-byte keys, s5cmd's relative-vs-absolute key
reconstruction, swath's refusal to stream Parquet) that a generic bridge
cannot guess its way to. Rounds 1-2 guessed it anyway (`simple/tools/`, now
deleted) and got real tools' argv and column shapes wrong in exactly the
ways this predicts. Round 3 binds `simple/` to the ORIGINAL repo's domain
model instead: `adapters.py` bridges to `tools/<tool>/adapter/{command,
normalize}.py` via `common/command_adapter.py`'s loader and each capsule's
own `normalize.py <mode> [prefix]` CLI; `campaign.py` reads cases straight
from `manager/bench/plan.py`'s `Plan.load()`. Nothing about a tool's shape
is duplicated in `simple/` anymore -- see `adapters.py`'s docstring for
exactly where the line falls.

## Mapping: real subsystem -> sketch file

| Real subsystem | Real LOC | Sketch file | Sketch LOC |
|---|---|---|---|
| `worker/engine.py` + `worker/upload.py` + `worker/summary.py` (attempt execution, artifact capture, row counting, create-only GCS upload) | 1,468 + 432 + 145 = 2,045 | `measure.py` | 340 |
| `common/command_adapter.py` + `manager/normalizer_cli.py` (the seam, not reproduced) + `tools/<tool>/adapter/*.py` x 11 (the domain model, left in place, bound to) | 199 + 3 = 202 (seam only; 2,653 domain model, untouched) | `adapters.py` | 112 |
| `manager/campaign/*.py` (fingerprinting, Batch job rendering, submission, polling, retry) + `manager/campaign/provider.py` (172, the real Batch SDK usage this mirrors) | 3,537 | `campaign.py` | 526 |
| `manager/bench/plan.py` (plan schema, case generation, inheritance -- READ, not reimplemented) | 1,090 | (import only; 0 sketch lines) | 0 |
| `manager/verify/*.py` (hex-staged DuckDB set math: missing/extra/duplicate/mismatch, drift taxonomy) | 2,264 | `verify.py` | 380 |
| report rendering (byte-identical `verify.md` templates) | not isolated | `report.py` | 134 |
| `common/contract.py` (byte-framed record contract -- the capsules' own concern now) + one hashing/exit-code seam | 315 | `contract.py` | 36 |
| `manager/campaign/report.py` (1,096; google-cloud-storage usage this mirrors) | 1,096 | `gcs.py` | 62 |
| `tools/<tool>/build/Dockerfile` x 11 | 336 (all 11) | `Dockerfile` | 31 |
| `.github/workflows/images*.yml` (registry-aware build planner) | 785 | `ci.yml` | 74 |
| **Total (rough, comparable slices only)** | **~18,285** (whole `src/`) | **Total, this directory** | **1,695 + this file** |

The real-LOC column shows *shape*, not an exact multiplier -- docs and
tests aren't counted, and the domain-model row (2,653 lines) is read, never
reimplemented, so it costs `simple/` nothing while being exactly what runs.

## Example command sequence

None of this has ever been run -- the GCP project, buckets, and image below
are placeholders -- but this is the intended chain, run from a checkout root
with the real `s3_listing_study` package installed (`pip install -e .`):

```sh
# Build and push the one image every tool runs in (see Dockerfile for what it does NOT stage).
docker build -t "$IMAGE:$(git rev-parse --short HEAD)" -f simple/Dockerfile simple
docker push "$IMAGE:$(git rev-parse --short HEAD)"

# Submit every case x rep in a REAL plan as a GCP Batch job.
python simple/campaign.py submit --project my-proj --location us-central1 \
    --plan bench/buckets/noaa-ghcn-pds.yaml \
    --results-bucket my-results-bucket --image "$IMAGE:abc123" \
    --secrets secrets.yaml

# Watch until every job reaches a terminal Batch state.
python simple/campaign.py poll --project my-proj --location us-central1 --watch

# See what happened, straight from campaign.db, latest submission per case.
python simple/campaign.py status

# A FAILED case gets a fresh, honestly-numbered submission.
python simple/campaign.py retry --project my-proj --location us-central1 \
    --plan bench/buckets/noaa-ghcn-pds.yaml \
    --results-bucket my-results-bucket --image "$IMAGE:abc123"

# Close the loop: compare every succeeded case's latest submission against
# the aws-cli case's, in one pass, writing verify.json into each leaf.
python simple/campaign.py verify --plan bench/buckets/noaa-ghcn-pds.yaml \
    --reference-case s3api-v2-text

# Or run one comparison by hand.
python simple/verify.py --tool s5cmd --mode recursive --bucket noaa-ghcn-pds \
    --attempt-dir gs://my-results-bucket/s5cmd-recursive-1/ \
    --reference-attempt-dir gs://my-results-bucket/aws-cli-s3api-v2-text-1/

# Summarize every latest-submission attempt as one Markdown table.
python simple/report.py --attempts-root gs://my-results-bucket
```

`campaign.db` is inspectable directly:
`sqlite3 campaign.db "SELECT * FROM submissions"`.

## Round 3: binding to the real domain model, plus a soundness fix

Round 2 asked "does this sketch reach a real number"; round 3 asks "is the
number the real tool's number." Deleting the guesses (`tools/`,
`manifest.py`) and binding to the real capsules/plan resolver is the whole
answer -- these are correctness/operational-fitness gaps, not added rigor:

- **`adapters.py`** compiles argv through the SAME `command.py` the real
  worker loads and normalizes through the SAME `normalize.py` CLI the real
  study runs -- round 1/2's from-scratch guesses at eleven tools' argv and
  column shapes are gone. A capsule's own emit boundary already refuses an
  unframeable key and already emits canonical mtime; round 2's
  `assert_framing_safe`/mtime machinery is deleted, not reproduced.
- **`campaign.py` reads `Plan.load()`** instead of a hand-rolled flat plan
  schema: case resources, `env` (heap sizing), `reps`, `timeout_s`, and
  `fingerprint` all come from the real resolver now, against the real
  `bench/buckets/*.yaml`. (Caught while testing against a real plan: two
  tools can share one `case_id` -- `manager/bench/plan.py`'s IDs don't carry
  the tool name -- so job ids and retry's case lookup are keyed off
  `(tool, case_id)`, not `case_id` alone.)
- **Cross-attempt comparison replaces the manifest.** `manifest.py` is
  deleted; `verify.py --reference-attempt-dir` compares two attempts'
  normalized listings against each other. A PASS is tool-vs-tool
  AGREEMENT, not correctness against ground truth -- there is no blessed
  answer anymore. The campaign's primary validity signal is `row_count`
  (below); cross-attempt comparison is the on-demand deep diff a
  disagreement calls for. **Named limitation:** on a mutable bucket, two
  attempts far apart in time conflate bucket motion with real tool
  differences -- the real study re-lists to attribute drift, which this
  sketch does not attempt.
- **`row_count`** (`measure.py`, mirroring `worker/summary.py`): every
  attempt records how many rows its own listing normalized to, independent
  of any comparison -- the number `report.py` shows for every attempt,
  verified or not, that `verify.py`'s deep diff double-checks when it looks wrong.
- **google-cloud-storage / google-cloud-batch replace `gsutil`/`gcloud`
  subprocesses** (owner correction; `gcs.py`, `campaign.py`): typed
  `list_blobs`/`blob.exists()`/`create_job`/`get_job`, no parsed CLI text
  or tempfile `--config` dance.
- **Verifier soundness (codex-review catch):** `compute_diff`'s anti-joins
  used SQL `NOT IN`, NULL-blind -- one NULL key on either side silently
  empties every discrepancy list and returns a false PASS. Fixed two ways:
  `assert_no_null_fields` refuses (a distinct exit code) before any join
  runs, and the anti-joins are rewritten as `NOT EXISTS`, which is
  null-safe. A malformed row is now a refusal, never a PASS.
- **Failed subjects can't contaminate a comparison:** `verify.py` refuses
  (a distinct exit code) a leaf whose `result.json` shows `exit_code != 0`
  or `timed_out` -- a crashed run is not a listing finding. `report.py`
  never mixes vocabularies: `job_state` (Batch), `exit` (the subject), and
  `verdict` (verify.json, or `-`) are three columns; the summary's average
  wall time counts only `exit == 0` + PASS/DRIFT rows, and says so.
- **Batch rendering fixes, all mirroring `manager/campaign/batch.py`:**
  `docker_options` -> container `options` (Batch's `memoryMib` only
  *schedules*; without this a memory-sweep case measures nothing); N4
  machine types get a Hyperdisk boot disk (~line 199; `pd-balanced` cannot
  provision one); `valid_job_id` reserves `-rN` headroom and forces a
  leading letter / trailing alphanumeric, where round 2's plain truncation
  could produce an invalid id once retried.
- **The verify -> report loop is closed:** `verify.py` uploads `verify.json`
  back INTO a `gs://` leaf by default; `campaign.py verify` runs every
  succeeded case's latest submission against one reference case in-process,
  in one pass; `report.py` reads through the same leaf-resolution helpers
  whether `--attempts-root` is local or `gs://`.

## What this sketch deliberately does NOT handle

- **Evidence sealing / TwinStamp.** No physical-execution profile, no
  create-only (`ifGenerationMatch=0`) upload precondition, no
  tamper-evident receipt chain. Every GCS write here is a plain overwrite.
- **Duplicate-*submission* detection.** Disjoint attempt leaves make
  duplicate *launches* harmless, but there is no reconciliation between
  "jobs `campaign.py` believes it submitted" and "leaves actually observed
  in GCS" -- a case submitted twice by mistake is two full attempts.
- **Byte-identical report rendering.** `report.py` just prints Markdown; no
  frozen template, no acceptance test pinning byte-for-byte output.
- **Non-UTF-8 keys.** Every DuckDB comparison here is VARCHAR (UTF-8 text);
  `common/contract.py`'s bytes-based `Record` is the real answer.
- **Exact byte-order set math.** `manager/verify/compare.py` hex-stages
  every field so `ORDER BY`/equality reproduce `LC_ALL=C sort` byte order
  for arbitrary bytes; this sketch compares plain VARCHAR values.
- **Full secret scanning.** `scan_for_secrets` is a bounded leak gate over
  stdout/stderr with three regexes; `common/secret_scan.py` scans every
  byte an attempt could publish, with a broader pattern set.
- **Race-condition / SIGTERM / full allocation fidelity.** No term-grace
  period, no disk sampler thread, no interpreter-identity capture, no
  network/subnet pinning, no provisioning-model choice (SPOT is hardcoded).
- **A registry-aware CI build planner.** `ci.yml` builds and pushes one
  image on every push touching `simple/`, unconditionally.
- **Tests.** None. Nothing here is asserted against real tool output beyond
  the ad hoc checks run while writing each round.

## Files

- `README.md` — this file.
- `contract.py` — the one shared seam: verify's exit-code ladder, `sha256_of`.
- `gcs.py` — thin google-cloud-storage wrapper (`gs://` parsing, list/exists/read/write).
- `adapters.py` — the bridge to the real tool capsules (see "Architecture").
- `measure.py` — the worker: compiles one case's command via `adapters.py`,
  runs it, scans stdout/stderr for secrets, counts rows, uploads to its own
  `uuid4` leaf (artifacts, then the atomic `result.json` marker, last).
- `campaign.py` — reads a real `Plan`, submits/polls/cancels/retries/verifies
  Batch jobs via the SDK, tracks every submission as its own `campaign.db`
  row keyed by `(base_job_id, submission)`.
- `verify.py` — resolves two jobs' attempt leaves, checks
  completeness/binding/subject success on both, normalizes both via
  `adapters.py`, diffs with DuckDB, writes `verify.json` into the actual leaf.
- `report.py` — opens `campaign.db` read-only, prints a Markdown summary
  table (`job_state`/`exit`/`row_count`/`verdict` as separate columns).
- `Dockerfile` — one image for every tool.
- `ci.yml` — build, smoke-test, then push (never the other order).
