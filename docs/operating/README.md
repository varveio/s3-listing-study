# Operating & extending the study

Machinery docs — how to provision a runner, reproduce a run, and add or shape a
tool. These are for operators and contributors. Readers who want to understand
the study and its findings should start at [`../README.md`](../README.md).

- [`runner-security.md`](runner-security.md) — the execution profiles for
  third-party images: cooperative GCP Batch with a bounded task identity, and
  the stricter bridge/metadata-denial gate for local Docker. **Read before
  executing any subject or trusted reference container.**
- [`tool-structure.md`](tool-structure.md) — the authoritative contract for a
  runnable tool directory: what every layer and Markdown file owns.
- [`image-builds.md`](image-builds.md) — the shared-runtime, tool-parent and
  execution-image chain: what a change to each rebuilds, how CI decides what to
  build from what the registry is missing, which events publish and which only
  validate, and what the published tags mean.
- [`tool-onboarding.md`](tool-onboarding.md) — the sequence for adding a new
  subject and building its capsule, and for re-deriving one when its upstream
  releases a new version.
- [`capsule-authoring.md`](capsule-authoring.md) — how to actually produce a
  capsule from a derivation: what order to build it in, how to split the work
  across agents, how the evidence rules bind while writing, and the
  verification loop that runs before a capsule is called done.
- [`artifact-availability.md`](artifact-availability.md) — what receipt payloads,
  manifests, and images are retrievable from a clone today, what is only
  hash-bound, and the remaining release gate.
- [`tool-research-brief.md`](tool-research-brief.md) — the frozen prompt the
  per-tool groundwork agents ran (a source-first pass, anonymous smoke runs,
  reconciliation, and independent review): the pre-registration of the
  groundwork method. Groundwork is complete; this prepared the benchmark and is
  not the benchmark.
