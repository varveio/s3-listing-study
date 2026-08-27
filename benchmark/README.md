# Benchmark harness

This directory is the sole production boundary for comparative measurement. It
owns declarative plans, toolbox construction and smoke checks, GCP Batch job
submission/reconciliation, subject execution and metric capture, verification,
and reports. Tool research, build facts, adapters, and historical groundwork
receipts remain in `tools/`; those receipts are capsule evidence, not benchmark
results.

## Layout

This directory is a self-contained component: source, tests, build inputs, and
plans each have their own place, mirroring how every capsule keeps its build
facts under `tools/<tool>/build/`.

- `plans/` contains the precommitted per-bucket plans and shared allocation/tool
  tables.
- `build/` holds the toolbox build inputs: `Dockerfile` builds one linux/amd64
  toolbox directly from all eleven checked-in capsule recipes (it consumes no
  parent image or retired image job), alongside its context policy and the
  pinned worker requirements. The replay server is an external digest-pinned
  plan input rather than an image this repository rebuilds; fixture bytes are
  separate immutable staged inputs.
- `tests/` holds this component's test suite. Repository-wide gates stay in the
  top-level `tests/`.
- `docs/` holds the design. Read `architecture.md` first; the other three are
  reference and say so:
  - `architecture.md` — why the harness is shaped this way: the three places a
    fact may live, the ownership question that decides which, what the planner
    does with a dependency, and the refuse-rather-than-guess rule.
  - `identity.md` — what makes two runs the same measurement or different ones:
    the case hash, the tool and platform slices, and the one input no hash can
    cover.
  - `model.md` — the state model: attempts, slots, the object layout, and the
    tables that bind a case to a job and its evidence.
  - `capsule-contract.md` — what a capsule declares to the harness and what the
    harness promises it. The Python boundary between `benchmark/` and `tools/`.
- `docs/running.md` is the operator runbook: prerequisites, submission, the job
  state machine, monitoring, the recovery commands, verification, and reporting.
  A committed bounded bundled-fixture replay canary qualifies submit,
  poll/status, report, and receipt export. The current plan's staged-fixture
  path, recovery, content verification, and real-S3 remain `VERIFIED: no`.
- `src/benchmark/` is the importable package — the only part of this directory
  the toolbox image contains:
  - `build_image.py` validates recipe, artifact, executable, and adapter
    identities before building the toolbox.
  - `campaign.py` creates and manages benchmark-owned Batch jobs in `campaign.db`.
  - `local_campaign.py` freezes a seeded complete-block order and runs real-S3
    canaries serially through the same worker in local Docker, retaining the
    ledger and attempt evidence on local disk. Replay orchestration is not yet
    implemented there and is refused explicitly.
  - `measure.py` runs exactly one selected subject and captures its raw outputs
    and metrics. It is the image entrypoint.
  - `verify.py` is the explicit real-S3 content-comparison path; replay is
    row-count-only and is refused there without staging raw products.
  - `report.py` binds `result.json` summaries to controller state and renders
    row counts, timing, RSS, and replay-server evidence without reading listings.
  - `receipt.py` exports one settled group as a deterministic factual draft,
    including frozen requests and bound result/verification identities.
  - `replay_fixture.py` generates the small synthetic canary fixture outside the
    checkout and computes the content identity required by staged Parquet.
  - `runtime/` is the contract layer the eleven capsule adapters import
    (`benchmark.runtime.*`); it runs both inside the image and orchestrator-side
    during verification.

## Toolbox and provenance

External base images are digest-pinned in `Dockerfile`; downloaded artifacts use
Dockerfile checksum preconditions copied from the capsule recipes. Compiler and
package-manager work occurs in isolated build stages. The final image records a
schema-5 metadata document containing each tool's version, artifact locator and
digest, consolidated build-input digest, capsule recipe digest, executable/workdir,
adapter digest, and the tool and platform slices, plus the executed
toolbox-Dockerfile digest, harness revision, and aggregate toolbox-manifest
digest. There are no
per-tool parent-image or legacy shared-base identities.

Build a clean revision from the repository root:

```sh
uv run python benchmark/src/benchmark/build_image.py \
  --harness-revision "$(git rev-parse HEAD)" \
  --tag benchmark-toolbox:local
docker run --rm benchmark-toolbox:local --help
```

`.github/workflows/benchmark-toolbox.yml` performs the same self-contained build
and help-smokes all eleven executables. s4cmd remains excluded from comparative
and replay plans; carrying its executable permits the explicitly bounded
real-S3 canary without reviving it as an active subject. That is harness/toolbox smoke. The committed
smoke receipts under `tools/*/receipts/` are earlier capsule research observations
and are not regenerated by this workflow.

## Local Docker campaign

The local executor is for bounded canaries and diagnostic repeatability work,
not a substitute for the fresh-VM publication rule in `docs/methodology.md`.
It consumes the same plan and capsule contracts, runs the same in-image
`measure.py`, journals attempts in `campaign.db`, and leaves local evidence at:

```text
RESULTS_ROOT/<suite>/<bucket>/<attempt_id>/
```

It resolves Docker's allowed CPUs into physical-core sibling groups and refuses
a requested vCPU count that would split one. Cases execute serially. The seed,
resolved block order, image ID, plan digest, host topology, exact Docker argv,
and result path are frozen before the first subject starts.

Build a clean toolbox revision, then inspect the exact schedule without writing
state:

```sh
uv run python benchmark/src/benchmark/local_campaign.py run \
  --plan benchmark/plans/local/s3-canary/noaa-nws-rtofs-pds.yaml \
  --image benchmark-toolbox:local \
  --results-root /absolute/path/to/results \
  --suite local-rtofs-canary --group rtofs-s3-canary \
  --location ACTUAL-HOST-LOCATION --seed 982451653 \
  --allow-retired-s4cmd-s3-canary --dry-run
```

The real invocation is identical without `--dry-run`. A signed row requires
`S3_STUDY_AWS_CREDENTIAL` in the caller's environment as newline-separated
`KEY=VALUE` credentials. Docker receives only `--env=S3_STUDY_AWS_CREDENTIAL`;
the value is never placed in argv, the ledger, the schedule, or controller logs.
The exact eleven-tool plan is a one-attempt-per-tool canary over the mutable
`noaa-nws-rtofs-pds` bucket. Its durations are factual observations from those
runs, never a ranking or variance estimate.

## Campaign image set

Before a campaign, publish the toolbox through an explicitly authorized registry
operation, then emit the schema-5 image-set JSON from the same build tool at the
same revision:

```sh
uv run python benchmark/src/benchmark/build_image.py \
  --harness-revision "$(git rev-parse HEAD)" \
  --image-set /secure/images.json \
  --image-uri "us-docker.pkg.dev/p/r/toolbox@sha256:<64 hex>"
```

The set is the image's own metadata projected to the fields the controller
accepts, so it is derived rather than transcribed. The image URI must be
immutable:

```json
{
  "schema_version": 5,
  "image_uri": "us-docker.pkg.dev/p/r/toolbox@sha256:<64 hex>",
  "toolbox_manifest_sha256": "<64 hex>",
  "toolbox_recipe_sha256": "<64 hex>",
  "harness_revision": "<40 hex git commit>",
  "tools": {
    "aws-cli": {
      "tool_version": "2.36.1",
      "tool_build_sha256": "<64 hex>",
      "tool_artifact_kind": "release-archive",
      "tool_artifact_locator": "https://awscli.amazonaws.com/...zip",
      "tool_artifact_sha256": "<64 hex>",
      "recipe_sha256": "<64 hex>",
      "build_inputs_sha256": "<64 hex>",
      "adapter_bundle_sha256": "<64 hex>",
      "subject_workdir": "/aws",
      "tool_slice_sha256": "<64 hex>",
      "platform_sha256": "<64 hex>"
    }
  }
}
```

The real document contains the exact active-tool roster. Submit only after the
toolbox smoke has passed:

```sh
uv run python benchmark/src/benchmark/campaign.py submit \
  --suite s3-listing-study --plan benchmark/plans/buckets/noaa-ghcn-pds.yaml \
  --project my-project --location us-central1 \
  --results-bucket my-results --image-set /secure/images.json \
  --anonymous-worker-sa anonymous-worker@my-project.iam.gserviceaccount.com \
  --authenticated-worker-sa auth-worker@my-project.iam.gserviceaccount.com \
  --secret-resource projects/varve-oss/secrets/s3-listing-study-aws-credentials/versions/latest

uv run python benchmark/src/benchmark/campaign.py poll --watch
uv run python benchmark/src/benchmark/report.py --state campaign.db --group g20260816-000000
```

The controller records intent before creating a job. A retry receives a fresh
ordinal, job name, and result prefix; no code adopts a job or image from a
different launch. A result marker is uploaded last, and `report` refuses
unbound or inconsistent results. The full operator runbook — submit, poll,
retry, cancel, report, and explicit real-S3 verification — is
[`docs/running.md`](docs/running.md).

## Minimum rigor and deliberate limitations

The harness preserves raw output, binds every result to the recorded job request
and immutable toolbox identity, and publishes `result.json` only after the other
attempt artifacts. Routine
replay reporting reads that marker only. These controls make an attempt
auditable; row count is not a content-correctness verdict.

### Attempt evidence is create-only

The worker computes its final completion code, uploads attempt artifacts, and
uploads the final `result.json` marker with
`ifGenerationMatch=0`. A second execution cannot merge with or replace a
deterministic attempt prefix. Raw listing products remain retained under that
prefix for manual investigation, but routine replay reporting does not fetch them.

### Replay reporting is row-count-only

For real S3, verification compares completed attempts with one another. A
`PASS` means their exposed fields agree; it does not prove either listing is
complete against an independently sealed manifest. The source bucket can change
between attempts, so `mtime`-only differences are `DRIFT` and other differences
remain `FAIL` because the verifier cannot infer their cause.

Replay records the worker's in-container `row_count` after the timed child exits.
The worker uploads the untouched product and logs, then uploads `result.json`
last. Campaign reporting binds and reads only that summary: it does not generate
or require a correctness manifest, normalize rows, or issue a content verdict.
Replay diagnostics use the `minimal-replay` evidence profile: retained products
and captures are recorded by name and size, not content-hashed. Comparative and
preparation attempts retain the stronger digest-bearing evidence needed by the
verifier and dependency chain. `result.json.postprocessing_seconds` records
each applicable phase separately; all remain outside the subject wall clock.

### Replay qualification precedes replay measurement

A replay diagnostic canary validates the recorded backend binding, readiness,
server evidence, in-container row count, raw upload, and result/report path. It
produces neither comparative timing nor rate data. Replay
capacity is **UNCALIBRATED** while the plan's `replay.capacity_status` says
`uncalibrated`; no replay measurement row is eligible. Set it to `calibrated`
only after a real diagnostic capacity canary has a committed receipt.

### Malformed or partial evidence is refused

The verifier rejects ambiguous attempt leaves, missing result markers, binding or
artifact-hash mismatches, failed/timed-out/unclean subjects, normalizer failures,
and normalized rows containing SQL `NULL`. Replay reporting separately refuses
missing readiness, inactive request counters, increasing error counters, and
missing calibrated interval/resource samples through the same validator the
worker uses. Nullable object metadata uses the
literal `-`; a field is compared only when both tools expose it. This avoids
NULL-blind anti-joins and prevents a tool that cannot report a field from creating
a false mismatch, while also making absence of that field non-evidence. Non-UTF-8
keys are outside the declared benchmark corpus and verifier scope.
