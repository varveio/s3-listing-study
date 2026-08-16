# `simple/` production candidate

This directory is the smaller candidate for the study's benchmark machinery:
submit, execute, collect, compare, and report. It deliberately keeps the real
domain model: `Plan.load()`, `common/command_adapter.py`, and every
`tools/<tool>/adapter` capsule. It does not reimplement tool CLI knowledge.

The candidate has focused automated coverage and local adapter/process tests.
It has **not** run a real GCP Batch campaign, so it must not yet be described as
the machinery that produced a study result.

## Inputs and image shape

A campaign requires a dated campaign ID and an image-set JSON file. Schema 1 is:

```json
{
  "schema_version": 1,
  "images": {
    "aws-cli": {
      "image_uri": "us-docker.pkg.dev/p/r/aws-cli@sha256:<64 hex>",
      "tool_parent_image": "us-docker.pkg.dev/p/r/aws-cli-parent@sha256:<64 hex>",
      "tool_version": "2.36.1",
      "tool_build_sha256": "<64 hex>",
      "adapter_bundle_sha256": "<64 hex>",
      "harness_revision": "<40 hex git commit>",
      "subject_workdir": "/aws"
    }
  }
}
```

Every plan tool needs an entry and every URI must be digest-pinned. The image
set digest, tool/build/adapter/harness identity, case fingerprint, resources,
exact subject argv, target, campaign/job/run/submission, outcome, monotonic
timing, rusage, and cgroup OOM deltas are bound into the ledger, Batch request,
and worker result where that layer can observe them.

`Dockerfile` builds one worker layer over one immutable tool parent. Use the
repository root as context so it can stage the selected capsule and the stable
`s3_listing_study.common` package:

```sh
uv run python simple/build_image.py \
  --tool aws-cli \
  --tool-parent "$TOOL_PARENT_AT_SHA256" \
  --harness-revision "$(git rev-parse HEAD)" \
  --tag "$DERIVED_IMAGE"
docker run --rm "$DERIVED_IMAGE" --help
```

The container runs as uid/gid 10001 and contains only the selected tool, its
capsule, common contract code, and the small worker. The build writes immutable
tool/build/adapter/harness/parent/workdir metadata which the worker checks before
exec. The build wrapper refuses a dirty checkout, validates the selected
capsule, and checks that the immutable parent's tool-build label and working
directory equal its registration; the Docker build also checks the registered
subject executable exists. `.github/workflows/simple-images.yml` exposes that
same build-and-smoke path.
It does not authenticate to a registry and cannot publish.

## Campaign example

Authenticated rows require both an authenticated worker service account and a
non-empty `authenticated` mapping in `secrets.yaml`. Mapping values are Secret
Manager versions; the required keys are `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`, with optional `AWS_SESSION_TOKEN`. They reach the
subject through `--pass-env`; unknown, missing, duplicated, or uninjected keys
make the worker fail before exec. Plan-provided environment is limited to the
same `JAVA_TOOL_OPTIONS`/`NODE_OPTIONS` allowlist as the existing worker.

```sh
python simple/campaign.py submit \
  --project my-project --location us-central1 \
  --campaign-id 2026-08-16-canary \
  --plan bench/buckets/noaa-ghcn-pds.yaml \
  --results-bucket my-results \
  --image-set /secure/canary-images.json \
  --anonymous-worker-sa anonymous-worker@my-project.iam.gserviceaccount.com \
  --authenticated-worker-sa auth-worker@my-project.iam.gserviceaccount.com \
  --secrets /secure/secrets.yaml \
  --network projects/my-project/global/networks/study \
  --subnetwork projects/my-project/regions/us-central1/subnetworks/study \
  --zone us-central1-a --provisioning SPOT

python simple/campaign.py poll --project my-project --location us-central1 --watch
python simple/campaign.py verify \
  --plan bench/buckets/noaa-ghcn-pds.yaml --reference-case s3api-v2-text
python simple/report.py --state campaign.db
```

Submission intent and the exact Batch request are committed to SQLite before
`create_job`. A restart redrives an unsettled intent and adopts an existing job
only when its normalized immutable request and provider parent are exact.
Retries have new job IDs and disjoint destination prefixes, but preserve the
original service accounts, secrets, network, placement, provisioning, and
container request. A submission with a complete `result.json` is a recorded
outcome—even when the subject exited nonzero—and is never retried.

A latest `FAILED`, `NOT_CREATED`, or `COLLISION` provider outcome must either be
retried or explicitly accepted before the report is final:

```sh
python simple/campaign.py accept-failure --job-id c-example
```

The worker runs as a Linux child subreaper, sends TERM at timeout, then KILL
after a grace period, and checks both the process group and descendants that
escaped it with `setsid()`. Native directory
outputs are recursively retained and uploaded. Capsule `count_rows()` receives
file-backed stdout and the native root; verification uses the capsule CLI's
`--input` or `--dataset` path and never loads a complete listing into Python.
Both comparison sides get independent duplicate checks.

`report.py` treats neither a scheduler row nor an unbound JSON file as evidence.
It binds identity, target, image, resources, timing, RSS, process-tree, and OOM
evidence to SQLite and the recorded Batch request. It then reruns verification
read-only before showing a stored verdict, rather than trusting `verify.json`'s
claimed diff or hashes. Rows retain their case/run/resource identity; the report
does not average timings across heterogeneous cases. It returns nonzero until
every provider effect is settled, every successful job has a bound result, and
any latest failed outcome has been explicitly accepted.

## Deliberate omissions

- No TwinStamp evidence sealing, create-only GCS writes, canonical-JSON
  adversary handling, or full create-only publication protocol.
- No byte-identical Markdown reports.
- No non-UTF-8 or exact byte-order comparison; DuckDB comparison is VARCHAR.
- No full artifact secret scanner; the worker has a bounded stdout/stderr leak
  gate and keeps secret values out of results.
- No disk-usage sampler or registry-aware build planner.
- Cross-attempt `PASS` means agreement, not truth. A mutable bucket can create
  drift between attempts.

Those are conscious proportionality choices. Verification refusal/FAIL returns
nonzero, and DRIFT deliberately returns exit 2, so publication automation can
gate on the result.
