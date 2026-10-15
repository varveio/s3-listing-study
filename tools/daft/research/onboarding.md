# Daft continuation checklist

**Research prepared; integration not implemented; runtime not qualified.**
Execution is deferred. This checklist makes a later authorized implementation
session actionable; it creates no scheduled work and authorizes no installation,
subject execution, Docker build, benchmark or cloud resource.

## Preserve the selected subject

Use stable **v0.7.26**, source
`aad22b926e340b09449b4bcc61d95d9964473d31`, and the
[reviewed source derivation](report.md). Select public `daft.set_runner_native()`
before runner initialization, then `daft.from_glob_path("s3://bucket/**", ...)`
and a complete public `iter_rows()` drain to JSONL. The base comparable fields
are key and size. An Arrow export is a separate arm, with distinct conversion
costs and dependencies. Do not substitute private `io_glob`, Ray execution,
`collect()`, preview output, moving main or an object-content reader.

Resolve these decisions before writing an adapter:

- Define a byte-preserving URI-to-key mapping, including slash, escaping and
  Unicode cases. The source emits a full path, not an already qualified S3 key.
  Include the [source-derived repeated-prefix cycle risk](report.md#source-derived-repeated-prefix-cycle-risk)
  in deferred qualification: a one-child `a//b` shape can revisit the same
  directory request. This is not a reproduced hang.
- Decide how optional/null size is represented and when missing size makes an
  attempted comparison incomplete; never manufacture a size. `num_rows` is
  built as null on the selected source path and is not a promised object metric.
- Declare the marker filter policy prominently. Native glob excludes valid S3
  marker-style objects; do not silently claim an unfiltered inventory, drop
  reference rows to hide differences, or patch upstream filtering under the same
  subject identity. Determine which fixtures/products are comparable.
- Select the official wheel for an explicit Python/platform/architecture, record
  its checksum, and lock the complete dependency closure. The source manifest
  requirement is not evidence of what a wheel contains. Keep artifact provenance
  and installed/build identity separate from the frozen source reference.
- Bind native runner choice, anonymous/region/endpoint settings, connection and
  native execution configuration to the invocation. Do not treat the accepted
  `results_buffer_size` argument as an effective knob: the selected native
  runner ignores it, as traced in the [source report](report.md#public-operation-and-content-boundary).
  Native internal channels have their own implementation-defined buffering.
  Public fanout/page-size knobs were
  not identified; preserve and disclose backend defaults rather than inventing
  a concurrency axis. Record CPU visibility because IO width depends on it.

## Implement the repository seams together

Follow [onboarding](../../../docs/operating/tool-onboarding.md),
[capsule authoring](../../../docs/operating/capsule-authoring.md),
[tool structure](../../../docs/operating/tool-structure.md), and the
[executable contract](../../../benchmark/docs/capsule-contract.md).
The paths below describe future changes; none exist as Daft integration today.

| Seam | Required implementation or decision |
| --- | --- |
| `tools/daft/data/` | Add canonical tool identity and atomic source-backed claims first. Keep research support distinct from runtime receipts. |
| `tools/daft/docs/`, README | Derive mechanism/running prose from the ledger, preserve exclusions and actual qualification state. |
| `tools/daft/adapter/command.py` | Declare a truthful mode/product/field surface, supported controls and full upstream executable invocation. Keep pagination/traversal upstream. |
| `tools/daft/adapter/normalize.py` | Count and normalize the real retained JSONL, preserve keys/nulls, reject malformed or partial records, and declare only key/size comparison coverage. |
| `tools/daft/build/` | Add pinned artifact recipe, dependency lock and thin public-API export wrapper if required, then `image.json` with real executable/workdir facts. No ready executable is specified by this proposal. |
| [Shared Dockerfile](../../../benchmark/build/Dockerfile) and [context allowlist](../../../benchmark/build/Dockerfile.dockerignore) | Add an isolated Daft install stage and copy only its runtime closure; allowlist every needed lock/wrapper input. Preserve artifact verification. |
| [Image builder](../../../benchmark/src/benchmark/build_image.py) | Register `TOOL_STAGES`; add every supporting lock and wrapper to `SUPPORT_INPUTS`, so executed bytes affect tool/build identity. Check artifact validation and recipe-slice attribution. |
| [Contract roster](../../../benchmark/src/benchmark/contract.py), [plan defaults/exclusions](../../../benchmark/plans/tools.yaml) | Add Daft only when integration is ready. Preserve the intent of existing campaigns with explicit exclusion/opt-in policy; onboarding must not silently alter their subjects. |
| [Command adapter tests](../../../benchmark/tests/test_command_adapters.py), [normalizer tests](../../../benchmark/tests/test_adapters.py) | Update both currently explicit rosters (`TOOLS`, `PORTED`), mode/empty/error cases, and truthful payload coverage. Add content cases for the actual Daft shape; do not claim historical receipts that do not exist. |
| [Build tests](../../../benchmark/tests/test_build_image.py), [plan tests](../../../benchmark/tests/test_plan.py) | Keep exact roster, build-input/recipe identity and historical-plan guards truthful; adjust fixture expectations where justified, never weaken validation to pass. |

A wrapper under `build/` is not covered by the two-file adapter-bundle hash.
[Build selection](../../../benchmark/src/benchmark/runtime/build_selection.py)
hashes capsule build inputs and declared artifact into `tool_build_sha256`;
`adapter_bundle_sha256` covers command/normalizer. The shared image builder must
also include the wrapper and lock in its support-input digest. Reseal changed
metadata and check both bindings; do not leave an executable wrapper outside the
image identity. See [image identity](../../../benchmark/docs/identity.md).

## Check static integration before qualification

The [testing guide](../../../benchmark/docs/testing.md) and
[CLI definitions](../../../src/s3_listing_study/cli.py) are the authorities for
checks. In an implementation session with dependencies already provisioned,
start with `git diff --check` and `uv run s3-listing-study check-links`. After a
full capsule exists, use `uv run s3-listing-study validate-capsule --tool daft`
and the source-anchor checker as appropriate; the research-only directory is
not expected to satisfy a runnable-capsule validator today. Run the relevant
command/normalizer/build/plan tests, for example:

```sh
uv run pytest benchmark/tests/test_command_adapters.py benchmark/tests/test_adapters.py benchmark/tests/test_build_image.py benchmark/tests/test_plan.py
```

Static success means the declared integration is internally consistent. It does
not establish that the package imports, executable starts, public API matches,
listing finishes, requests are metadata-only or content is complete.

Only in a separately authorized execution phase, use the ordinary
[toolbox builder](../../../benchmark/src/benchmark/build_image.py) from a clean
revision and follow the [harness runbook](../../../benchmark/docs/running.md).
First qualify the actual container/package and public API, native runner and
complete output drain; a help/version check alone cannot qualify this library
workload. Retain real empty/known-content and adversarial-key attempts, request
shape/auth/endpoint evidence, exit/status/count/timing/memory, and content
verification including explicit exclusions. Failed/partial attempts remain data.
Then independently review the capsule and any predeclared comparative plan.

The status transitions are **research prepared → integration implemented and
statically checked → actual container/content qualification recorded**. Advance
each state only from its own evidence. A later diagnostic comparison needs its
own authorization, matching product/resources and committed run records; no
step here makes Daft a calibrated benchmark result or changes the published
release. All `tools/` changes still require independent and owner PR review.
