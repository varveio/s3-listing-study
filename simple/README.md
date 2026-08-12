# simple/ — a happy-path sketch of this study's pipeline

This directory is **not part of the study**. It is an unrun demonstration
that the shape of "run eleven S3 listing tools as GCP Batch jobs, upload
their output to GCS, and check it against a reference manifest" can be built
in a couple thousand lines of plain Python using standard means — subprocess
calls to `gcloud`/`gsutil`, DuckDB SQL, stdlib. It exists to make the *cost*
of this repo's ~18k lines of audit-grade Python legible: every one of those
lines is buying a specific correctness or auditability property this sketch
does not have.

**Nobody should run this.** There is no CI wiring it into the real pipeline,
no test suite, and several of the tool command lines under `tools/` are
educated guesses (marked `# approximate`) rather than checked behavior.

## Mapping: real subsystem -> sketch file

| Real subsystem | Real LOC | Sketch file | Sketch LOC |
|---|---|---|---|
| `worker/engine.py` + `worker/upload.py` (attempt execution, artifact capture, create-only GCS upload) | 1,468 + 432 = 1,900 | `measure.py` | 303 |
| `tools/<tool>/adapter/{command,normalize}.py` x 11 (per-tool argv + native-output normalization) | ~2,653 (all 11 tools, plus docs/data/receipts) | `tools/` package (registry + one module per tool) | 473 |
| `manager/campaign/*.py` + `manager/campaign/ledger.py` (case fingerprinting, Batch job rendering, submission, polling, retry, sqlite ledger) | 3,537 | `campaign.py` | 501 |
| `manager/verify/*.py` (hex-staged DuckDB set math: missing/extra/duplicate/mismatch, drift taxonomy) | 2,264 | `verify.py` | 349 |
| `manager/bench/plan.py` + `bench/tools.yaml` + `bench/buckets/*.yaml` (plan schema, case generation, inheritance) | 1,090 (plan.py alone) | `campaign.py` (`validate_plan`/`build_cases`) + `plan-example.yaml` | (included above) + 59 |
| the reference-manifest lineage (`data/registry.toml` binds a bucket to a manifest; provenance of the manifest itself is not vendored in this repo) | not isolated; no single file | `manifest.py` | 98 |
| report rendering (byte-identical `verify.md` templates) | not isolated; spread across `manager/verify` and `manager/reports` | `report.py` | 112 |
| `tools/<tool>/build/Dockerfile` x 11 (pinned multi-stage builds, one per tool) | 336 (all 11) | `Dockerfile` | 27 |
| `.github/workflows/images*.yml` (registry-aware build planner, matrix, content-addressed tags) | 785 | `ci.yml` | 72 |
| `common/contract.py` (the TAB-framed 5-field record contract, byte-exact key handling) | 315 | shared `assert_framing_safe`/`FramingViolation` in `tools/__init__.py`; no dedicated module | 0 |
| `common/secret_scan.py` (scans every published byte for credential material) | not isolated | `measure.py`'s `scan_for_secrets` (stdout/stderr only, bounded) | (included above) |
| **Total (rough, comparable slices only)** | **~18,285** (whole `src/`) | **Total, this directory (see `wc -l simple/*` / file list below)** | **see file list below** |

The real-LOC column undercounts the true cost in several places (docs,
receipts, and tests aren't counted; TwinStamp's evidence core is a separate
~unknown-LOC dependency this sketch doesn't touch at all) — it is there to
show *shape*, not to claim an exact multiplier. `campaign.py`'s state store
mirrors the real manager's choice of a database over a flat file
(`manager/campaign/ledger.py`) in miniature: campaign.db is one sqlite3 file
rather than campaign-state.json, for the same reason -- see "Round 2" below.
`tools/` (one module per tool plus a registry) deliberately mirrors the real
repo's `tools/<tool>/adapter/` capsule boundary: the original single-file
`tools.py` had the right *content* but the wrong *unit* -- per-tool adapter
knowledge is a different concern, with a different owner and change cadence,
from the runner/worker suite around it.

## Example command sequence

None of this has ever been run — the tool binaries, GCP project, and buckets
below are placeholders — but this is the intended chain from plan to report:

```sh
# Build a reference manifest once, from a live listing.
python simple/manifest.py --bucket noaa-ghcn-pds --prefix "" --output manifests/noaa-ghcn-pds.tsv

# Build and push the one image every tool runs in.
docker build -t "$IMAGE:$(git rev-parse --short HEAD)" -f simple/Dockerfile simple
docker push "$IMAGE:$(git rev-parse --short HEAD)"

# Submit every (tool, mode) case in the plan as a GCP Batch job.
python simple/campaign.py submit --plan simple/plan-example.yaml

# Watch until every job reaches a terminal Batch state.
python simple/campaign.py poll --plan simple/plan-example.yaml --watch

# See what happened, straight from campaign.db (sqlite3), latest submission per case.
python simple/campaign.py status

# A case that FAILED (spot preemption, a transient describe error) gets a
# fresh, honestly-numbered submission rather than a mutated retry-in-place.
python simple/campaign.py retry --plan simple/plan-example.yaml

# Verify one attempt's listing against a reference manifest: resolves the
# job's destination to its one attempt leaf, downloads it, and checks the
# leaf's own result.json against the expectations below before verifying.
python simple/verify.py --tool aws-cli --bucket noaa-ghcn-pds --mode s3api-v2-text \
    --attempt-dir gs://my-results-bucket/noaa-ghcn-pds-aws-cli-s3api-v2-text-1/ \
    --manifest manifests/noaa-ghcn-pds.tsv

# Summarize every latest-submission attempt + verdict as one Markdown table.
python simple/report.py --attempts-root /local/attempts
```

`campaign.py submit --dry-run` renders each job body without submitting, for
eyeballing what would go to `gcloud batch jobs submit`. `campaign.db` is
inspectable directly: `sqlite3 campaign.db "SELECT * FROM submissions"`.

## Which tools in `tools/` are checked vs. guessed

- **Checked against the real tool's documented output shape:** `aws-cli`
  (`s3api list-objects-v2 --output json`), `s5cmd` (`--json ls`), `rclone`
  (`lsjson`), `minio-mc` (`ls --json`) — these four are "right-ish" per the
  brief this sketch was written to.
- **Approximate** (marked `# approximate` in that tool's own module, guessed
  field names or column layout rather than a checked run): `s7cmd`,
  `s3-fast-list`, `s3kor`, `s4cmd`, `s3p`, `ps3`, `swath`. Several of these
  tools' real adapters (see `tools/<tool>/adapter/normalize.py`) exist
  precisely because their native output has a sharp edge — `s4cmd`'s
  fixed-width columns splitting multi-byte keys mid-character is the one
  `common/contract.py` calls out by name — and this sketch's guessed
  normalizer does not attempt to reproduce that edge, correctly or otherwise.

## The minimum rigor we kept

Everything else in "What this sketch deliberately does NOT handle" below
stays out — no secret scanning, no canonical JSON, no create-only upload
preconditions, no hex-staged byte comparison, no coordination journal. These
four crossed back in because each is a handful of lines that stops a
*wrong number from being reported as a right one*, not merely a property
that makes a right number more auditable after the fact — everything left
out only costs legibility or defense-in-depth, not correctness of the
verdict itself:

- **Disjoint attempt leaves** (`measure.py`'s `attempt_uuid`/`leaf_destination`;
  `verify.py`'s `resolve_leaf`/`list_leaves`). Every invocation uploads to its
  own `uuid4` leaf instead of a shared path, and a reader refuses (rather than
  picks a winner) when a job's destination holds zero or several leaves. A
  silent overwrite between two launches of the same case would poison the
  number itself — a retry's wall-clock time or listing could silently replace
  the first attempt's before anyone could compare them, with no trace either
  run happened.
- **`result.json` as completeness marker** (`measure.py`'s
  `write_result_atomic`/`upload`; `verify.py`'s `has_result_marker`).
  `result.json` is written atomically and uploaded last, so a leaf missing it
  is legible as torn/incomplete. Without this, a truncated attempt with zero
  extra keys reads identically to a genuinely complete PASS — the marker is
  what tells "matches" apart from "never finished".
- **Verify binding** (`verify.py`'s `check_binding`). A verdict is refused,
  not computed, if the selected leaf's recorded tool/bucket/prefix/mode
  disagree with what the caller expected. A verdict computed against the
  wrong case isn't a wrong report about a real result — it's a convincing
  result about nothing that happened, which is worse than no result at all.
- **Crash-safe ledger writes** (`campaign.py`'s `submissions` table). Round 1
  approximated this with a temp-file-plus-`os.replace()` dance around
  campaign-state.json; round 2 replaced the whole file with one sqlite3
  database (`campaign.db`) and deleted that dance outright -- sqlite's own
  journal now provides the crash-safety it approximated, natively, for every
  write instead of one whole-file swap. A corrupted ledger doesn't just lose
  bookkeeping — it can forget which jobs were ever submitted, which is how a
  re-run silently doubles a workload or resubmits work already in flight.

## Round 2: purpose-fitness additions

Round 1 asked "does a wrong number get reported as a right one"; round 2
asks the next question -- "does this sketch actually reach a real number in
the first place." These are functional/correctness gaps in the round-1
sketch, not additional rigor:

- **`manifest.py`.** Round 1's `verify.py` had PASS/FAIL/DRIFT machinery and
  nothing to run it against — no reference manifest existed short of typing
  one out by hand. A verifier that can never verify against a real snapshot
  isn't a smaller verifier, it's an unreachable one.
- **Framing safety** (`tools.assert_framing_safe`, wired into `manifest.py`
  and `verify.py`'s normalize step). A key containing the TSV's own
  delimiter or a line break, written without this check, doesn't fail loudly
  -- it silently shifts every column after it, which reads back as a
  plausible but wrong key/size/etag/mtime split. That is a false verdict
  manufactured by the framing itself, not a tool finding.
- **Curated retry** (`campaign.py retry`). Without it, a FAILED case (spot
  preemption, a transient `describe` error -- both routine on real Batch
  infrastructure) has no way back into the campaign except hand-deriving and
  resubmitting the whole rendered job body outside the tool entirely.
- **Credential wiring** (`credential_secret`, `--pass-env`). Without it,
  none of the four tools this study's own plan comments say have no
  unsigned request path can be pointed at a real bucket at all -- not a
  rigor gap, an inability to run against real infrastructure for a
  majority-relevant slice of the roster.
- **Pre-upload secret scan** (`measure.py`'s `scan_for_secrets`). Without
  it, a tool that crashes verbosely or dumps its own config to stderr
  publishes whatever credential material was in that output straight to the
  results bucket. A real accident this leak gate exists to catch, not a
  hypothetical.
- **File-sink native outputs** (`tools/`'s `native`/`NATIVE_FILE` module
  attributes, wired through `swath-parquet`). Without it, any mode that
  writes a file instead of streaming to stdout -- the roster's one such case
  -- reads as a tool that emitted nothing: verify.py would see an empty
  `stdout.log.gz` and report a FAIL for a tool that actually produced a
  correct listing.
- **sqlite ledger** (`campaign.py`'s `campaign.db`, see above). Mirrors the
  real manager's own choice of a database over a flat file. Roughly
  line-count-neutral versus round 1's JSON approximation; the trade-off is
  that `campaign.db` is no longer git-diffable or eyeballable in a text
  editor -- inspect it with `sqlite3 campaign.db "SELECT * FROM submissions"`.

## What this sketch deliberately does NOT handle

The real pipeline earns its line count by handling all of the following;
this sketch has none of it:

- **Evidence sealing / TwinStamp.** No physical-execution profile, no
  create-only ("never overwrite") upload precondition on individual GCS
  objects, no tamper-evident receipt chain. `measure.py`'s two-step upload
  order (artifacts, then `result.json`) makes torn attempts legible, but
  nothing stops a compromised or buggy caller from overwriting an object at
  the same URI outright — the real uploader's `ifGenerationMatch=0` closes
  that gap and this sketch does not.
- **Duplicate-*submission* detection.** Disjoint attempt leaves (above) make
  duplicate *launches* harmless, but there is still no case fingerprinting
  and no reconciliation between "jobs `campaign.py` believes it submitted"
  and "leaves actually observed in GCS" — a job submitted twice by a human
  mistake is still two full attempts, just two honestly-labeled ones instead
  of one silently clobbering the other.
- **Byte-identical report rendering.** The real verifier's output is a
  frozen template covered by acceptance tests pinning byte-for-byte output.
  `report.py` just prints Markdown.
- **Non-UTF-8 keys are OUT of scope.** `common/contract.py` carries S3 keys
  as raw `bytes`, never decoded, specifically so a key that isn't valid
  UTF-8 survives intact, and rejects (rather than escapes) keys containing
  TAB/NEWLINE/CR because the framing can't carry them. This sketch's DuckDB
  comparisons throughout (`manifest.py`, `verify.py`) read everything as
  VARCHAR (UTF-8 text) and will misbehave on a non-UTF-8 key -- framing
  safety (round 2) catches the TAB/NEWLINE/CR half of that contract, not the
  UTF-8 half. `common/contract.py`'s bytes-based `Record` is the real answer
  and is not reproduced here.
- **Exact byte-order set math.** `manager/verify/compare.py` hex-stages
  every field so `ORDER BY`/equality in DuckDB reproduces `LC_ALL=C sort`
  byte order for arbitrary bytes. This sketch compares plain VARCHAR values.
- **Full secret scanning.** `measure.py`'s `scan_for_secrets` is a bounded
  leak gate over stdout/stderr with three regexes; the real
  `common/secret_scan.py` scans every byte an attempt could publish
  (including file-sink native output, which this sketch's scan does not
  touch), with a broader pattern set and no claim that three patterns catch
  every credential shape.
- **Race-condition / SIGTERM handling.** No term-grace period, no disk
  sampler thread, no interpreter-identity capture.
- **Full Batch allocation fidelity.** `campaign.py`/`tools/` carry a
  minimal container-memory sweep, per-tool heap injection
  (`JAVA_TOOL_OPTIONS`/`NODE_OPTIONS`, see `tools.heap_env_for`), and
  Secret-Manager-backed credential wiring (`credential_secret`/`--pass-env`)
  -- each cheap once its real-repo source (`bench/tools.yaml`'s `heap:`
  block; `manager/campaign/batch.py`'s secret handling) was read for
  fidelity. Still missing: N4 Hyperdisk boot-disk handling, network/subnet
  pinning, and provisioning-model choice — `render_batch_job` has none of it.
- **A registry-aware CI build planner.** `ci.yml` builds and pushes one
  image on every push touching `simple/`, unconditionally; the real
  `images.yml` only builds what the registry doesn't already have,
  content-addressed per tool.
- **Tests.** None. Nothing here is asserted against real tool output.

## Files

- `README.md` — this file.
- `measure.py` — the worker: runs one tool, captures stdout/stderr/rusage,
  scans stdout/stderr for likely secrets before uploading anything, then
  uploads to its own `uuid4` leaf (artifacts first, `result.json` last, both
  written/uploaded atomically), with gzip checksums and attempt size folded
  into the marker. `--pass-env` copies named credential env vars from its own
  environment into the subject's, never into the published record.
- `tools/` — a package: `__init__.py` builds the `TOOLS` registry (and
  `heap_env_for`, `assert_framing_safe`/`FramingViolation`) from an explicit
  import list of `tools/<name>.py`, one module per tool (argv builder,
  normalize-SQL, `# approximate` markers where the shape is guessed).
  `swath.py` also exposes its Parquet-sink mode as the `swath-parquet`
  registry entry. `tools/__main__.py`: `python -m tools` previews every
  tool's argv for a sample bucket.
- `manifest.py` — builds a reference manifest TSV from a live aws-cli
  listing, normalized through `tools/aws_cli.py`'s own normalizer, sorted by
  key, with a `<output>.meta.json` sidecar (bucket, prefix, built_at,
  key_count, manifest sha256).
- `campaign.py` — reads and validates a plan, submits/polls/cancels/retries
  GCP Batch jobs via `gcloud`, tracks every submission as its own row in
  `campaign.db` (sqlite3), keyed by `(base_job_id, submission)`. Renders
  `credential_secret` plan entries as Batch secret env vars.
- `verify.py` — resolves a job's destination to its one attempt leaf, checks
  for the `result.json` completeness marker and case binding, normalizes the
  native output (stdout or a declared file-sink) and diffs it against a
  reference manifest TSV with DuckDB, prints PASS/FAIL/DRIFT (or a distinct
  refusal code, including a framing-violation refusal).
- `report.py` — opens `campaign.db` read-only, walks the latest submission
  per case, resolves each one's attempt leaf the same way `verify.py` does,
  prints a Markdown summary table.
- `Dockerfile` — one image for every tool.
- `ci.yml` — build-and-push-on-every-run GitHub Actions workflow.
- `plan-example.yaml` — a minimal example plan for `campaign.py`, including a
  commented-out `credential_secret` example and a two-row memory sweep.
