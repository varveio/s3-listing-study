# obstore

Research-only candidate: Python bindings to Rust `object_store`, with a direct
streaming metadata-list API. No obstore subject has been installed, executed or
benchmarked for this onboarding proposal. This directory is not a runnable capsule
and is absent from the published diagnostic release.

[Continuation checklist](research/onboarding.md) maps the decided API boundary,
remaining artifact decisions and exact repository integration surfaces for a
future authorized agent. Research readiness is not runtime qualification.

The proposed released subject is **0.12.0** (`py-v0.12.0`), source commit
`bbf158a79dca6ee61f78b8f0d218dbda591bfe40`, published October 15, 2026.
The initial October 15 derivation snapshot was `abe772d39f446a7850fa61060bc7b0c3c9792cad`;
[the report](research/report.md#identity-and-scope) records the subsequent tag
move and workflow-only diff. Compiled wheel identity remains unverified.
Its locked Rust `object_store` dependency is **0.14.2**. As rechecked on 2026-10-15 at 16:45 UTC,
upstream main and that release point to the same commit.

[The source report](research/report.md) explains the exact API, one-chain S3
pagination, path-segment prefix matching, key restrictions, streaming versus
collection, authentication and endpoint configuration, and decisions required
before a future runnable capsule. Those are source findings, **not a run record**.
Completeness, throughput, memory at scale and endpoint compatibility remain
unverified by this study.

Obstore is worth onboarding as an S3 library candidate because it exposes native
metadata iteration and Arrow batches without a catalog write. This adds a useful
API boundary alongside DataChain and CLI listers. Varve maintains this study and
builds Swath; that affiliation grants Swath no different evidence standard.
