# Snakemake real-campaign workflow

This phase-1 workflow consumes the campaign compiler's canonical schema-3
`campaign.json`. It expands the manifest's exact attempt rows before DAG
construction and covers them with one generic `attempt` rule. Tool modes,
product/zip resolution, image identity, worker provenance and stable job IDs
remain campaign data; the Snakefile has no per-tool branches.

The worker's UUID evidence tree remains authoritative. After the worker exits
successfully, the rule validates the local `result.json` against the planned
campaign coordinates and projected evidence destination. Snakemake then uploads
a small operational result pointer containing that exact `attempt_id`,
`artifact_uri`, `result_uri`, and the SHA-256 of the uploaded `result.json`,
namespaced by both frozen-input SHA-256 values. The pointer cannot classify an
attempt or stand in for `result.json`.

## Compile

Compile from a plan and a schema-3 image set without Batch project, location,
service-account, secret, network or ledger arguments:

```sh
run_dir=.snakemake/runs/2026-08-11-snake
mkdir -p "$run_dir"

uv run s3-listing-study compile-campaign \
  --bucket noaa-rtma-pds \
  --campaign 2026-08-11-snake \
  --image-set /path/to/current-images.json \
  --results-bucket example-results \
  --output "$run_dir/campaign.json"
```

A sealed current-image publication ledger may be supplied as
`--publication-manifest` instead of `--image-set`. The compiler mechanically
joins its immutable shared/tool/execution references and checkout revision with
the local registered selection's artifact and adapter identities, then applies
the same schema-3 registration validation.

The output is canonical JSON created with exclusive-create semantics. Repeating
the command accepts only byte-identical content and prints its SHA-256; the
newly created file is made read-only.

## Render and dry-run

`execution-profile.fixture.json` is a checked-in template, not deployable cloud
configuration. Fill a private copy with the actual identities, network, secret,
storage and patched executor identity, then canonicalize and freeze it
create-only:

```sh
uv run --project experiments/orchestration/snakemake python \
  experiments/orchestration/snakemake/freeze_execution_profile.py \
  --source /path/to/completed-execution-profile.json \
  --output "$run_dir/execution-profile.json"
```

The command refuses malformed profiles and refuses to replace different bytes.
Execution-profile schema 2 replaces the earlier schema-1 executor identity.
Existing ignored schema-1 profiles and targets containing their old execution
digest are obsolete; do not edit or overwrite them. Create a new run directory,
fill the schema-2 identity fields from the installed adapter, freeze a new
`execution-profile.json` with the command above, and record the new SHA-256 it
prints as the execution digest used by the marker target. This preserves the
create-only history while making code and dependency changes visible in target
identity.
The outer workflow accepts both frozen inputs only from the same
`.snakemake/runs/<run>/` directory beneath its working directory. That ignored,
in-repository placement lets Snakemake include their exact bytes in the remote
source archive. `S3_STUDY_RUN_DIR` must be spelled exactly as the canonical
three-component repository-relative path `.snakemake/runs/<single-name>`; an
absolute path, traversal, extra nesting, or alternate spelling is rejected
before DAG construction because it would not be portable inside the extracted
remote source archive. Nested `SUBPROCESS` execution does not inherit or require
that operator environment variable: it derives the same canonical run directory
from the already-frozen `campaign_path` and `execution_profile_path` values in
the deployed command, and requires those files to share one run directory. Keep
the generated frozen files uncommitted.

The real trial uses the existing results bucket as a separate diagnostic
orchestration-evaluation profile. These runs are diagnostics, not production-
profile benchmark evidence. The frozen profile remaps each
logical campaign attempt prefix to
`snakemake/evidence/<campaign>/results/<bucket>/<tool>/<case>/run-N/`; worker
object names remain confined by the experiment to `snakemake/**`. The worker
currently has bucket-wide `roles/storage.objectAdmin` (owner decision on
`origin/main` at `e0843b3`); this namespace split is an experiment invariant,
not an IAM prefix boundary. That role supplies object create/read/update/delete
and list operations, but not the bucket-metadata permission
`storage.buckets.get`. Both the outer runner and nested worker invoke the GCS
plugin's `Bucket.exists()`, so both identities also need a bucket-scoped grant
containing `storage.buckets.get` (for example `roles/storage.legacyBucketReader`
or a narrower custom role). This broader diagnostic identity does not alter the
authoritative production create-only profile in `docs/operating/runner-security.md`.
Snakemake source, manifests, profiles and mutable markers live under
`snakemake/orchestration/` in the same bucket. The authenticated worker remains
a distinct identity and needs the same diagnostic object and bucket-metadata
permissions plus Secret Manager access before an authenticated case can run.
That prerequisite is external to this experiment; this work makes no Terraform
changes.

```sh
uv run --project experiments/orchestration/snakemake python \
  experiments/orchestration/snakemake/render.py \
  --campaign "$run_dir/campaign.json" \
  --execution-profile "$run_dir/execution-profile.json"

campaign_sha256=$(sha256sum "$run_dir/campaign.json" | cut -d' ' -f1)
execution_sha256=$(sha256sum "$run_dir/execution-profile.json" | cut -d' ' -f1)
target="markers/2026-08-11-snake/$campaign_sha256/$execution_sha256/noaa-rtma-pds/aws-cli/s3api-v2-text/run-1.json"

S3_STUDY_RUN_DIR="$run_dir" \
uv run --project experiments/orchestration/snakemake snakemake \
  --snakefile experiments/orchestration/snakemake/Snakefile \
  --profile experiments/orchestration/snakemake/profiles/googlebatch \
  --dry-run \
  "$target"
```

This operator dry-run consults marker state through the configured GCS storage
provider. The local test suite replaces only that storage inventory lookup to
exercise profile parsing and DAG construction offline; its success is not live
GCS or Batch evidence.

The run directory is the launch's single configuration input. The profile reads
the two frozen files there, computes both expected digests, and derives the
executor project/region and default GCS storage settings directly from the
execution profile. There is no second ambient copy of those values and no
command-line `--config` to replace the profile's workflow config. Both digests
are mandatory and are checked before target construction.
The two frozen inputs are local rule inputs even with default GCS storage, and
are registered as workflow sources so remote execution deploys their exact
bytes alongside the Snakefile and runner script.
Marker names are always logical relative paths below `markers/`; with the fixed
default GCS prefix they resolve once beneath `snakemake/orchestration/`.
Given a known marker path, retrieve the marker with an exact object GET and then
retrieve its validated `result_uri` with another exact GET. Bucket listing is
reserved for duplicate audits beneath the attempt's evidence prefix, where more
than one UUID child would reveal repeated execution.
The existing evidence uploader still sends every GCS create with
`ifGenerationMatch=0`, so its objects remain write-once even though the current
worker identity has bucket-wide `objectAdmin`.

The local projection covers image digest, worker argv, stable `Attempt.job_id`,
machine type, CPU milli, scheduling memory, N4 boot disk, container memory and
swap options, retry zero, maximum duration, provisioning, zone, network,
service account and secret reference. Tests derive those fields directly from
the frozen campaign rows and execution profile for every resolved
`noaa-rtma-pds` attempt, then check the adapted executor's actual Google Batch
request against that projection.
The same canonical provider projection is carried as a rule parameter and the
executor compares it with effective job resources, threads, project, region,
runtime helper and GCS storage settings immediately before submission.
`--set-resources`, `--set-threads`, provider flags and storage flags therefore
cannot silently mutate a launch while retaining the frozen digests. Workflow
For the outer `DEFAULT` invocation, configuration paths remain derived from
`S3_STUDY_RUN_DIR`; `--config` cannot replace them or their hashes. The nested
`SUBPROCESS` invocation has no ambient run-directory input and validates the
canonical deployed paths and their exact hashes from frozen config.

## Runtime helper and executor

The local `googlebatch-study` executor is an upstream-shaped adaptation of
`snakemake-executor-plugin-googlebatch` 0.5.1. Snakemake still owns submission,
status polling, restart semantics, and cancellation. The adapter changes only
the provider request that the upstream executor could not express: it preserves
retry zero, the selected allocation and identity, cgroup options, and Secret
Manager mapping. It labels the Batch Job with the stable campaign
`Attempt.job_id`, while using a separate random Batch resource ID for provider
operations.
The execution profile schema records the installed Snakemake version, local
adapter version, upstream Google Batch plugin version, and a deterministic
SHA-256 over `__init__.py` and `executor.py` (path and byte lengths are framed
before hashing). Freezing and submission both validate that identity. Any code
or environment change requires a newly frozen profile and therefore a new
execution digest and marker namespace.

The current immutable subject images are not rebuilt. A small helper image
copies a locked Snakemake 9.25.1 plus GCS-provider runtime into the Batch
workdir. The unchanged subject image then invokes nested Snakemake with
`/usr/bin/python3` and `PYTHONPATH=/tmp/workdir/runtime` as its existing UID
10001. It reconstructs the deployed run directory from the two frozen config
paths rather than receiving `S3_STUDY_RUN_DIR` in the Batch environment. No
Batch runnable downloads packages, and no trailing command can hide
the nested invocation's exit status. The helper deliberately does not contain
`s3-listing-study`: `scripts/workflow.py` and `src/s3_listing_study` must come from
Snakemake's deployed source archive.

Build from the repository root, push to the existing Artifact Registry, and
resolve the pushed manifest digest:

```sh
docker build \
  --provenance=false \
  --sbom=false \
  -f experiments/orchestration/snakemake/runtime/Dockerfile \
  -t us-east1-docker.pkg.dev/varve-oss/s3-listing-study/snakemake-runtime:2026-08-11 \
  experiments/orchestration/snakemake/runtime
docker push \
  us-east1-docker.pkg.dev/varve-oss/s3-listing-study/snakemake-runtime:2026-08-11
docker buildx imagetools inspect \
  us-east1-docker.pkg.dev/varve-oss/s3-listing-study/snakemake-runtime:2026-08-11
```

Put the resulting `@sha256:...` URI in the frozen execution profile as
`executor.runtime_image`; tags are rejected. That value becomes a per-job
Snakemake resource, so there is no unfrozen CLI override and a runtime change
also changes the execution-profile digest and marker namespace. The checked-in
compute profile is `profiles/googlebatch/profile.v9+.yaml`; set only
`S3_STUDY_RUN_DIR` to the repository-relative frozen run directory before use.
The profile loads the execution settings from that directory rather than
accepting mutable project, bucket, or location overrides. Its effective shape
is:

```yaml
executor: googlebatch-study
jobs: 17
default-storage-provider: gcs
default-storage-prefix: >-
  gcs://s3-listing-study-results-29c02004/snakemake/orchestration/
googlebatch-study-project: varve-oss
googlebatch-study-region: us-east1
googlebatch-study-image-family: batch-cos
googlebatch-study-image-project: cos-cloud
```

The default storage prefix is the only remote root for marker paths. The
Snakefile fixes their logical names below `markers/`; do not repeat
`snakemake/orchestration/` in a marker path. Launch through the same frozen run
directory used by dry-run and no custom submit, poll, or watch wrapper. Always
provide exactly one marker target; the current diagnostic gate must never
default to `rule all` and all 17 attempts:

```sh
S3_STUDY_RUN_DIR="$run_dir" \
uv run --project experiments/orchestration/snakemake snakemake \
  --snakefile experiments/orchestration/snakemake/Snakefile \
  --profile experiments/orchestration/snakemake/profiles/googlebatch \
  "$target"
```

`scripts/` intentionally contains only the rule runner and its workflow support
module. Snakemake walks a script's directory when constructing the source
archive; keeping it narrow prevents the experiment's `.venv` and other
`.snakemake/` state from entering the archive. The Snakefile imports
`scripts/workflow.py` as ordinary Python, and deployed repository `src/` is
added to the import path before it imports manager modules.

The `ci published --json` command returns an image-build plan, not the sealed
publication ledger and not a campaign image set. Use the content of the
`ghcr.io/varveio/s3-listing-study:set-main` publication ledger with
`--publication-manifest`. The retired August 10 schema-2 root-image set is not
treated as current.
