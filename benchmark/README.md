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
  toolbox directly from the eleven checked-in capsule recipes (it consumes no
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
  - `campaign.py` records campaign intent and attempts in `campaign.db`;
    `drivers/gcp_batch.py` owns GCP Batch rendering and lifecycle commands, while
    `submit --executor docker` dispatches the current bounded real-S3 experiment.
  - `measure.py` runs exactly one selected subject, counts its local output, and
    captures logs and metrics. Native-product upload is opt-in. It is the image
    entrypoint.
  - `verify.py` is the explicit real-S3 content-comparison path; replay is
    row-count-only and is refused there without staging raw products.
  - `report.py` binds `result.json` summaries to controller state and renders
    row counts, timing, RSS, and replay-server evidence without reading listings.
  - `receipt.py` exports one settled group as a deterministic factual draft,
    including frozen requests and bound result/verification identities.
  - `replay_fixture.py` generates the small synthetic canary fixture outside the
    checkout and computes the content identity required by staged Parquet.
  - `fixture_bundle.py` reproducibly captures a public bucket with an immutable
    Swath image, writes sorted Parquet, derives s3-fast-list hints, measures
    content and prefix shape, validates strict sorted replay startup, and can
    upload the complete bundle with create-only GCS writes.
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
and checks every executable in the toolbox. Including retired s4cmd in the image
supports its explicit one-shot real-S3 qualification; it remains excluded from
comparative and replay plans. This build check is harness/toolbox smoke. The
committed receipts under `tools/*/receipts/` are earlier capsule research
observations and are not regenerated by this workflow.

## Campaign execution

Logical plan rows describe the experiment, not its execution location. For the
current host, `campaign.py submit --executor docker` invokes the bounded local
session runner:

```sh
uv run python benchmark/src/benchmark/campaign.py submit \
  --executor docker --suite rtofs-canary \
  --plan benchmark/plans/experiments/repeatability/noaa-nws-rtofs-pds.yaml \
  --image benchmark-toolbox:local --results-root /absolute/path/to/results \
  --location us-east1-b --seed 982451653 \
  --allow-retired-s4cmd-s3-canary --dry-run
```

Remove `--dry-run` after inspecting the seeded order. Signed-only cases require
`S3_STUDY_AWS_CREDENTIAL` as newline-separated `KEY=VALUE` credentials. The
Docker path runs a bounded, synchronous, serial local session for independent,
non-replay real-S3 cases and writes its evidence locally. It shares plan
compilation, case identity primitives, the measurement worker, the ledger
schema, result evidence, verification, and reporting with Batch. It does not
share or claim the Batch `poll`, `retry`, or `cancel` lifecycle,
prerequisite-slot resolution, artifact transport, or replay-sidecar lifecycle.
Seeded repeats of independent real-S3 cases are supported locally. Replay and
dependent work runs on GCP Batch; those capabilities will not be added to the
local runner.

A Docker attempt and a Batch attempt for the same plan row are different cases.
The local `docker-<arch>-<sha12 of hardware facts>` machine-family label is
hashed into case identity; the executor name is not. The resulting cases occupy
disjoint strata and are never pooled across executors.

Real-S3 subjects use Docker's ordinary bridge networking. The executor does not
provision a host firewall or require a host-global network setup step. Every
subject drops all Linux capabilities and enables Docker's no-new-privileges
rule.

After the eleven one-shot canaries finish, compare their saved listings:

```sh
uv run python benchmark/src/benchmark/verify.py \
  --state /absolute/path/to/results/campaign.db \
  --group gYYYYMMDD-HHMMSS --include-docker-canaries
```

This reports cross-tool agreement, not independent correctness: the public
bucket can change while the tools run, so any disagreement must be investigated
before it is attributed. The option accepts Docker canaries only. Larger GCP
benchmark reports continue to compare worker-recorded row counts only.

## Preparing a replay fixture bundle

Large replay fixtures use one public, repeatable command rather than a session's
hand-written Docker and DuckDB invocations:

```sh
uv run python -m benchmark.fixture_bundle \
  --bucket noaa-nbm-grib2-pds --region us-east-1 \
  --output /absolute/evidence/noaa-nbm-grib2-pds-current \
  --swath-image ghcr.io/varveio/swath@sha256:<digest> \
  --replay-image registry.example/swath-replay@sha256:<digest> \
  --cpuset 12,13,14,15,28,29,30,31 --memory-gb 16 \
  --concurrency 128 --segments 1000 \
  --gcs-prefix gs://RESULTS/fixtures/noaa-nbm-grib2-pds/current-REV
```

The output is create-once: an existing output directory or GCS object is a
refusal, never an implicit resume or overwrite. The uploaded bundle directory
has a fixed contract (the local Parquet parts remain below `dataset/data/`):

```text
part-*.parquet
s3-fast-list-hints.input
s5cmd-shards.input
fixture.json
README.md
```

`fixture.json` retains the exact capture argv and image digests, Swath report,
part manifest and replay fixture digest, row/distinct/duplicate/marker counts,
prefix-depth shape, latency observations and fixed-p50 treatment, generated
hint and s5cmd-shard counts/digests, host allocation, and sorted replay readiness. The uploader
uses GCS generation precondition zero for every object. Replay plans continue to
address only `part-*.parquet`; fixture-backed s3-fast-list and s5cmd modes stage
their fixed companions from the same directory. The former bypasses the serial
bootstrap listing; the latter supplies a complete, disjoint top-level prefix
union to native `s5cmd run` without embedding thousands of prefixes in YAML.

This bundle construction is fixture provenance, not subject timing. It is run
once per immutable fixture. Every benchmark VM still downloads and verifies the
Parquet manifest before replay starts.

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

The harness binds every result to the recorded job request and immutable toolbox
identity, retains logs, and publishes `result.json` only after the other selected
attempt artifacts. Routine
replay reporting reads that marker only. These controls make an attempt
auditable; row count is not a content-correctness verdict.

### Attempt evidence is create-only

The worker computes its final completion code, uploads attempt artifacts, and
uploads the final `result.json` marker with
`ifGenerationMatch=0`. A second execution cannot merge with or replace a
deterministic attempt prefix. Raw listing products are omitted by default; pass
`campaign submit --retain-products` when manual content investigation or
verification needs them.

### Replay reporting is row-count-only

For real S3, verification compares completed attempts with one another. A
`PASS` means their exposed fields agree; it does not prove either listing is
complete against an independently sealed manifest. The source bucket can change
between attempts, so `mtime`-only differences are `DRIFT` and other differences
remain `FAIL` because the verifier cannot infer their cause.

Replay records the worker's in-container `row_count` after the timed child exits.
The worker uploads logs and then `result.json` last; native products are uploaded
only when the operator opts in. Campaign reporting binds and reads only that
summary: it does not generate
or require a correctness manifest, normalize rows, or issue a content verdict.
Replay diagnostics use the `minimal-replay` evidence profile. When products are
retained, they and captures are recorded by name and size rather than
content-hashed. Comparative verification requires an opt-in retained product;
dependency artifacts remain digest-bound. `result.json.postprocessing_seconds` records
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
