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
no test suite, and several of the tool command lines in `tools.py` are
educated guesses (marked `# approximate`) rather than checked behavior.

## Mapping: real subsystem -> sketch file

| Real subsystem | Real LOC | Sketch file | Sketch LOC |
|---|---|---|---|
| `worker/engine.py` + `worker/upload.py` (attempt execution, artifact capture, create-only GCS upload) | 1,468 + 432 = 1,900 | `measure.py` | 253 |
| `tools/<tool>/adapter/{command,normalize}.py` x 11 (per-tool argv + native-output normalization) | ~2,653 (all 11 tools, plus docs/data/receipts) | `tools.py` | 314 |
| `manager/campaign/*.py` (case fingerprinting, Batch job rendering, submission, polling, retry) | 3,537 | `campaign.py` | 346 |
| `manager/verify/*.py` (hex-staged DuckDB set math: missing/extra/duplicate/mismatch, drift taxonomy) | 2,264 | `verify.py` | 326 |
| `manager/bench/plan.py` + `bench/tools.yaml` + `bench/buckets/*.yaml` (plan schema, case generation, inheritance) | 1,090 (plan.py alone) | `campaign.py` (`validate_plan`/`build_cases`) + `plan-example.yaml` | (included above) + 51 |
| report rendering (byte-identical `verify.md` templates) | not isolated; spread across `manager/verify` and `manager/reports` | `report.py` | 106 |
| `tools/<tool>/build/Dockerfile` x 11 (pinned multi-stage builds, one per tool) | 336 (all 11) | `Dockerfile` | 27 |
| `.github/workflows/images*.yml` (registry-aware build planner, matrix, content-addressed tags) | 785 | `ci.yml` | 72 |
| `common/contract.py` (the TAB-framed 5-field record contract, byte-exact key handling) | 315 | informal comments in `tools.py`/`verify.py`; no dedicated module | 0 |
| **Total (rough, comparable slices only)** | **~18,285** (whole `src/`) | **Total, this directory (see `wc -l simple/*`)** | **1,678** |

The real-LOC column undercounts the true cost in several places (docs,
receipts, and tests aren't counted; TwinStamp's evidence core is a separate
~unknown-LOC dependency this sketch doesn't touch at all) — it is there to
show *shape*, not to claim an exact multiplier.

## Example command sequence

None of this has ever been run — the tool binaries, GCP project, and buckets
below are placeholders — but this is the intended chain from plan to report:

```sh
# Build and push the one image every tool runs in.
docker build -t "$IMAGE:$(git rev-parse --short HEAD)" -f simple/Dockerfile simple
docker push "$IMAGE:$(git rev-parse --short HEAD)"

# Submit every (tool, mode) case in the plan as a GCP Batch job.
python simple/campaign.py submit --plan simple/plan-example.yaml

# Watch until every job reaches a terminal Batch state.
python simple/campaign.py poll --plan simple/plan-example.yaml --watch

# See what happened, straight from campaign-state.json.
python simple/campaign.py status

# Verify one attempt's listing against a reference manifest: resolves the
# job's destination to its one attempt leaf, downloads it, and checks the
# leaf's own result.json against the expectations below before verifying.
python simple/verify.py --tool aws-cli --bucket noaa-ghcn-pds --mode s3api-v2-text \
    --attempt-dir gs://my-results-bucket/noaa-ghcn-pds-aws-cli-s3api-v2-text-1/ \
    --manifest /local/manifests/noaa-ghcn-pds.tsv

# Summarize every attempt + verdict as one Markdown table.
python simple/report.py --state campaign-state.json --attempts-root /local/attempts
```

`campaign.py submit --dry-run` renders each job body without submitting, for
eyeballing what would go to `gcloud batch jobs submit`.

## Which tools in `tools.py` are checked vs. guessed

- **Checked against the real tool's documented output shape:** `aws-cli`
  (`s3api list-objects-v2 --output json`), `s5cmd` (`--json ls`), `rclone`
  (`lsjson`), `minio-mc` (`ls --json`) — these four are "right-ish" per the
  brief this sketch was written to.
- **Approximate** (marked `# approximate` in `tools.py`, guessed field names
  or column layout rather than a checked run): `s7cmd`, `s3-fast-list`,
  `s3kor`, `s4cmd`, `s3p`, `ps3`, `swath`. Several of these tools' real
  adapters (see `tools/<tool>/adapter/normalize.py`) exist precisely because
  their native output has a sharp edge — `s4cmd`'s fixed-width columns
  splitting multi-byte keys mid-character is the one `common/contract.py`
  calls out by name — and this sketch's guessed normalizer does not attempt
  to reproduce that edge, correctly or otherwise.

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
- **Atomic ledger writes** (`campaign.py`'s `save_state`). Temp file + rename
  means a crash mid-write leaves the previous complete ledger, not a corrupt
  one. A corrupted `campaign-state.json` doesn't just lose bookkeeping — it
  can forget which jobs were ever submitted, which is how a re-run silently
  doubles a workload or resubmits work already in flight.

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
- **The contract's byte-fidelity guarantees.** `common/contract.py` carries
  S3 keys as raw `bytes`, never decoded, specifically so a key that isn't
  valid UTF-8 survives intact, and rejects (rather than escapes) keys
  containing TAB/NEWLINE/CR because the framing can't carry them. This
  sketch's DuckDB comparisons in `verify.py` read everything as UTF-8 text
  and will misbehave on a non-UTF-8 key.
- **Exact byte-order set math.** `manager/verify/compare.py` hex-stages
  every field so `ORDER BY`/equality in DuckDB reproduces `LC_ALL=C sort`
  byte order for arbitrary bytes. This sketch compares plain VARCHAR values.
- **Secret scanning.** The real engine scans captured native output for
  credential material before it can be published. Not present here.
- **Race-condition / SIGTERM handling.** No term-grace period, no disk
  sampler thread, no interpreter-identity capture.
- **Resource sizing rigor.** `campaign.py`/`tools.py` do carry a minimal
  version of the container-memory sweep and per-tool heap injection
  (`JAVA_TOOL_OPTIONS`/`NODE_OPTIONS`, see `tools.heap_env_for`), because it
  was cheap to wire through once `bench/tools.yaml`'s `heap:` block was read
  for fidelity. Still missing: N4 Hyperdisk boot-disk handling, network/
  subnet pinning, provisioning-model choice, and authenticated-credential
  Secret Manager wiring — `render_batch_job` has none of it.
- **A registry-aware CI build planner.** `ci.yml` builds and pushes one
  image on every push touching `simple/`, unconditionally; the real
  `images.yml` only builds what the registry doesn't already have,
  content-addressed per tool.
- **Tests.** None. Nothing here is asserted against real tool output.

## Files

- `README.md` — this file.
- `measure.py` — the worker: runs one tool, captures stdout/stderr/rusage,
  uploads to its own `uuid4` leaf (artifacts first, `result.json` last, both
  written/uploaded atomically), with gzip checksums and attempt size folded
  into the marker.
- `tools.py` — per-tool argv builders and DuckDB normalize-SQL, for all
  eleven tools (several marked `# approximate`), plus the heap-env sizing
  `bench/tools.yaml` records for swath/s3p.
- `campaign.py` — reads and validates a plan, submits/polls/cancels GCP
  Batch jobs via `gcloud`, tracks state in `campaign-state.json` (written
  atomically).
- `verify.py` — resolves a job's destination to its one attempt leaf, checks
  for the `result.json` completeness marker and case binding, normalizes the
  native output and diffs it against a reference manifest TSV with DuckDB,
  prints PASS/FAIL/DRIFT (or a distinct refusal code).
- `report.py` — walks `campaign-state.json`, resolves each job's attempt leaf
  the same way `verify.py` does, prints a Markdown summary table.
- `Dockerfile` — one image for every tool.
- `ci.yml` — build-and-push-on-every-run GitHub Actions workflow.
- `plan-example.yaml` — a minimal example plan for `campaign.py`.
