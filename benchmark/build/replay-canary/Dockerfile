# A tiny, deterministic fixture layer over the pinned replay server. The fixture
# is identity-bound separately by the plan and exists only for runner canaries.
FROM ghcr.io/varveio/swath-replay@sha256:c5f4239abbce7772faa2d58c661192bd6a890e460158b5e56fcb8c51b573cb63

COPY benchmark/fixtures/replay-canary/part-00000.parquet /fixtures/runner-replay-canary/
