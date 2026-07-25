"""Registry access: read ``data/registry.toml``.

Replaces ``harness/registry-lookup.sh`` (178 lines). Preserves that script's
two invariants: all lookup ambiguity is fatal (schema validation catches it,
not first-match resolution), and a receipt can prove which registry it came
from.

Lands in U1 (registry as data).
"""
