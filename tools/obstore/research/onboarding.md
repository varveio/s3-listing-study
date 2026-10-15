# Continuing obstore onboarding

**Research ready; not runnable or qualified.** This checklist schedules no work
and authorizes no installation, build, subject execution or benchmark. It gives
a future agent a concrete starting point after those actions are authorized.

Use the [source report](report.md) for frozen obstore 0.12.0 commit
`abe772d39f446a7850fa61060bc7b0c3c9792cad`, object_store 0.14.2 archive checksum,
dependency-source identity and their provenance limits. Recheck release/wheel
availability before final subject selection; a newer release needs its own read.

## Product decisions already made

- Base proposal: public `obstore.list()` with dictionary batches, an unprefixed
  `S3Store`, current objects, complete streamed metadata export. Preserve full key,
  size, nullable ETag and modification time. S3 listing's version field is null;
  there is no all-version mode or storage-class field to invent.
- Optional Arrow output is a separate declared arm with its own pinned dependency
  and conversion costs. `collect()` is a materializing arm, not the base mode.
- Path-segment prefix semantics differ from literal S3 substring prefixes. Returned
  keys pass through `Path::parse`: interior empty segments can error and leading
  or trailing slash can change identity. Resolve explicit qualification/refusal
  policy before launch; never silently discard bad keys, normalize collisions,
  substitute a filtered subset or label partial output successful.
- Hypothetical code in the report has not run. Native streaming, Python conversion
  and output formatting belong to the selected end-to-end product boundary.

## Files to implement in a later reviewed change

Follow the authoritative [onboarding sequence](../../../docs/operating/tool-onboarding.md),
[capsule authoring procedure](../../../docs/operating/capsule-authoring.md) and
[directory/build contract](../../../docs/operating/tool-structure.md).

| Integration surface | Future work |
| --- | --- |
| `tools/obstore/data/claims.json`, `data/tool.json` | Create the born-canonical ledger and subject identity; source-only claims stay supported/unverified as appropriate. Do not invent receipts or a migration stratum. |
| `tools/obstore/adapter/command.py`, `adapter/normalize.py` | Implement typed shared contracts, full absolute executable argv, complete streamed output and explicit failures/null handling. Derive key escaping and metadata normalization from real output later. |
| `tools/obstore/build/Dockerfile`, `build/image.json`, dependency lock and launcher | Select official wheel/platform/Python, checksum every artifact and lock the complete runtime closure. Decide launcher path and copy/runtime layout. Hash launcher and lock bytes as supporting build inputs; bind executable prefix and adapter digest to registration. |
| `benchmark/build/Dockerfile`, `benchmark/build/Dockerfile.dockerignore` | Add isolated install stage, copy the actual runtime closure and launcher, and include required inputs in build context. Keep capsule recipe and shared recipe consistent. |
| `benchmark/src/benchmark/build_image.py` | Register stage in `TOOL_STAGES` and all lock/launcher supporting bytes in `SUPPORT_INPUTS`; recompute hashes through the existing build machinery. |
| `benchmark/src/benchmark/contract.py` | Add to `TOOLBOX_TOOLS` only when runnable integration is ready; keep the registered set and stage map consistent. |
| `benchmark/plans/tools.yaml` | Register actual modes, field coverage and supported settings; preserve current-only scope and declare optional arms separately. |
| `benchmark/tests/test_command_adapters.py`, `benchmark/tests/test_adapters.py` | Update both tool rosters and relevant mode/field/command expectations; exercise malformed rows, nullable metadata, escaping, error propagation and declared unsupported requests. |
| Capsule README and `docs/` | Replace candidate-only navigation with runnable documentation only when the actual contracts exist; qualification status remains explicit. |

Artifact selection, wheel compatibility, dependency closure and launcher are
unresolved. Do not infer their identity from the source lockfile alone. Every
wrapper or dependency-lock change affects build-input hashing; adapter changes
affect adapter hashing. Commit explicit paths and build from the clean committed
revision required by `build_image.py`; never attest a dirty tree or hand-edit
placeholder image digests. The consolidated toolbox is the execution artifact,
not a separately published obstore parent image.

## Gates, in order

1. **Static implementation review:** use the linked authoring workflow's capsule
   validator, link and source-anchor commands, including both source repositories
   with no skipped anchors; run both shared adapter test files and required build
   registration checks. Static success does not prove the binary loads, the
   endpoint works or output is complete. Obtain independent and owner review.
2. **Later authorized container qualification:** after a digest-bound toolbox build,
   capture package/import and launcher help/version behavior where available.
   Exercise small independently known fixtures through the actual command and
   normalizer, retain errors and partial products, and compare exact keys and
   metadata. Separate anonymous/signed access, endpoint/addressing, segment-prefix,
   slash/Unicode/percent-key identity and marker cases. Do not execute the subject
   on the host. The report's known key risks must receive explicit outcomes.
3. **Benchmark readiness:** follow [running](../../../benchmark/docs/running.md),
   [testing](../../../benchmark/docs/testing.md) and
   [publishing](../../../benchmark/docs/publishing.md) for current evidence and
   execution contracts. Declare matched resources, CPU visibility, conversion and
   retry knobs, fixture identity, output scope and timing boundary before runs.
   Qualification is distinct from comparative evidence; research and static checks
   add no rows to the diagnostic release and establish no speed ranking.

No execution or automation is scheduled by this checklist.
