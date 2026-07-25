"""The two refusals the verifier can make, and how they reach the caller.

``harness/verify-listing.sh`` has exactly two death shapes and the port keeps
both. :class:`VerifierError` is ``die`` — the verifier could not run, so it
issues no verdict and exits 3. :class:`UnionError` is ``union_die`` — the same
refusal on the ``--scope union`` path, which additionally owes a durable
``union-verify.md`` carrying ERROR, because the README promises the union
verdict lands in that artifact even for the early deaths.
"""

from __future__ import annotations

ERROR_EXIT = 3
"""Neither a verdict nor a pass: the verifier declined to form an opinion."""


class VerifierError(Exception):
    """The verifier could not run. Reported as ERROR, never as a tool finding."""


class UnionError(VerifierError):
    """A union refusal, which must also leave an ERROR ``union-verify.md``."""
