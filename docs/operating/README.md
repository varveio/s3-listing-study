# Operating & extending the study

Machinery docs — how to provision a runner, reproduce a run, and add or shape a
tool. These are for operators and contributors. Readers who want to understand
the study and its findings should start at [`../README.md`](../README.md).

- [`runner-security.md`](runner-security.md) — the mandatory execution boundary
  for third-party images: identity-free disposable runner, contained bridge,
  fail-closed checks, and the activation gate. **Read before executing any
  subject or trusted reference container.**
- [`tool-structure.md`](tool-structure.md) — the authoritative contract for a
  runnable tool directory: what every layer and Markdown file owns.
- [`tool-onboarding.md`](tool-onboarding.md) — the sequence for adding a new
  subject and building its capsule.
- [`artifact-availability.md`](artifact-availability.md) — what receipt payloads,
  manifests, and images are retrievable from a clone today, what is only
  hash-bound, and the remaining release gate.
- [`tool-research-brief.md`](tool-research-brief.md) — the frozen prompt the
  per-tool groundwork agents ran (a source-first pass, anonymous smoke runs,
  reconciliation, and independent review): the pre-registration of the
  groundwork method. Groundwork is complete; this prepared the benchmark and is
  not the benchmark.
