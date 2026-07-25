"""Union-scope verification: multi-shard fan-out receipts.

Ports the ``--scope union`` branch of ``harness/verify-listing.sh``,
including reading the shard set from ``union-verify.md``'s shard table
rather than inferring it from directory layout (the aws-cli union pulls a
fifth shard from outside the fanout dir). Ported as-is; its shape is
revisited only in Phase 6.

Lands in U3 (verifier port).
"""
