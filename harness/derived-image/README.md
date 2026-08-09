# Shared derived attempt image

[`Dockerfile`](Dockerfile) is the single derived-image recipe, used unchanged by
every registered tool. Tool capsules never copy or fork it; they contribute
declarative inputs in `build/image.json` and nothing else.

## How one recipe serves every subject

Nothing in the Dockerfile names a tool. It opens with

```dockerfile
FROM subject AS attempt-builder
```

where `subject` is not an image on a registry but a **BuildKit named context**,
bound at invocation to the digest the capsule registered. The build binds four:

| Context | Bound to | Used for |
| --- | --- | --- |
| `subject` | `docker-image://<registered digest>` | both `FROM` stages |
| `python` | the pinned interpreter tree on the build host | `COPY --from=python` |
| `adapter` | `tools/<tool>/adapter/` | the tool's `command.py` and `normalize.py` |
| `selection` | `tools/<tool>/build/` | `image.json`, shipped as `selection.json` |

Because the subject is a context rather than a literal `FROM`, the same file
builds all eleven subjects with no templating and no generated Dockerfiles.
Reading the recipe alone therefore does not tell you which subject an image came
from; that comes from the registration and the build command below.

## The interpreter is a study input

The attempt engine runs *inside* the subject image, so it needs a Python there,
and only `aws-cli` ships one — Swath's image is a Temurin JRE, s5cmd's and
rclone's are Go binaries on minimal bases. The recipe therefore does not use the
subject's interpreter and does not install one: it binds a single pinned
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
CPython, digest-verified on the host by
[`../../src/s3_listing_study/python_runtime.py`](../../src/s3_listing_study/python_runtime.py)
and copied to `/opt/s3-listing-study/python/`. No package manager runs inside a
subject image and the subject's own package set is untouched.

One interpreter for every subject is also one fewer uncontrolled difference in
the comparison this repository exists to make.

`python_libc` in `image.json` selects which build is bound: an Alpine-based
subject (`s7cmd`) needs `musl`, everything else `gnu`. It is declared per capsule
rather than sniffed, because guessing wrong yields an interpreter that loads on
the build host and dies inside the subject. `validate_selection.py` runs on the
bound interpreter during the build and refuses a registration that declares the
other one.

## The payload is the whole package

The Dockerfile copies `src/s3_listing_study/` in one line. Only `attempt/` and
what it imports ever runs — the zipapp's entry point is `attempt.cli` — but the
rest rides along rather than being tracked in a hand-maintained file list that
drifts every time an import changes.

The cost is that an edit anywhere in the package changes the derived image's
digest, even when no in-image code changed. That is acceptable because a
campaign rebuilds every registered image from one harness commit before it runs
(owner's call, 2026-08-09), so attempts within a campaign share an image
revision by construction, and results from different platform strata are never
mixed anyway.

`validate_selection.py` imports the entry point during the build, so a payload
that does not import fails the build instead of surfacing inside a benchmark
attempt on a shipped image.

## Registration

Tool capsules record their declarative build inputs in `build/image.json`:

```json
{
  "tool": "swath",
  "subject_image": "ghcr.io/varveio/swath@sha256:e03f7be9c025…",
  "subject_version": "0.2.2",
  "python_libc": "gnu",
  "subject_workdir": "/opt/swath",
  "executable": ["/opt/java/openjdk/bin/java", "-jar", "/opt/swath/swath.jar"],
  "command": "adapter/command.py",
  "normalizer": "adapter/normalize.py",
  "adapter_bundle_sha256": "e202a8de6ab5…"
}
```

The field set is exact — an unknown or missing key is refused. `subject_image`
must be digest-pinned. `executable[0]` is a canonical absolute path; later
elements are literal argv tokens, so a JVM prefix works, and the whole array must
equal the adapter's own `fixed_command_prefix`. `subject_version` names the
release that digest contains; it is declared here rather than read from
`data/tool.json`, whose `tested.version` records what the study's receipts and
claims are anchored to — a related but distinct fact that legitimately lags the
pinned digest during a version bump.

## Building

Build from the repository root through the slug-only command:

```sh
s3-listing-study build-derived-image --tool swath
```

Subject image, interpreter, workdir, executable, and component paths are never
independent build arguments; the command resolves them from the registration.

With no `--tag`, the image is named from what was registered:

```text
s3-listing-study/swath:0.2.2-h0.1.0-e03f7be9c025
                └tool  └subject └harness └subject digest
```

A derived image is neither the subject nor the harness alone, so a name carrying
one version misreads as the other — `swath:0.2.2` has already been mistaken for
upstream's own `ghcr.io/varveio/swath:0.2.2` in a local image list. The tag is a
label for humans; **identity is the derived image's own digest**, which the
attempt engine requires as `--derived-image` and records in `result.json`. Two
builds differing only in adapter bytes share a name and are told apart there.

`--tag` still accepts an explicit name for a throwaway or CI-specific build.

## Adapter identity

`adapter_bundle_sha256` is the sole adapter identity recorded in new attempt
results. It hashes a canonical manifest: the ASCII header
`s3-listing-study-adapter-bundle-v1` plus NUL; then, in `command.py`,
`normalize.py` order, the UTF-8 filename plus NUL, an unsigned eight-byte
big-endian byte length, and the exact file bytes. Selection computes this hash
without importing the normalizer.

## Running

The final image fixes this entrypoint; schedulers append only the logical
request and never replace it:

```text
/opt/s3-listing-study/python/bin/python3 -I /opt/s3-listing-study/attempt.pyz \
  --tool swath --operation list --mode recursive-tsv \
  --bucket BUCKET --region REGION --prefix '' --scope full
```

Mount a unique empty attempt directory at `/output` and set
`S3_STUDY_ATTEMPT_OUT=/output`. The final stage keeps the subject image's own
user and workdir — the root-privileged builder stage is discarded — so that
directory must be writable by the subject's uid (Swath's is `10001:10001`).
