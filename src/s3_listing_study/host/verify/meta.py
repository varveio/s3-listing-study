"""``run.meta`` — the wrapper's record of what actually ran.

The verifier validates against this rather than trusting its own arguments:
without it, tool/mode/bucket/inputs are independent claims, and one mode's
output can be checked against another mode's scope and stamped into a third
mode's receipt with every artifact still looking consistent.
"""

from __future__ import annotations

from pathlib import Path

from .errors import VerifierError


def read_meta(path: Path) -> dict[str, str]:
    """Parse a ``run.meta``. First occurrence of a key wins, as the shell's awk does."""
    meta: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError as exc:
        raise VerifierError(f"run.meta not readable: {path} ({exc.strerror})") from None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key not in meta:
            meta[key] = value
    return meta


def resolve_payload_path(run_meta: Path, recorded: str) -> str:
    """Resolve a stream path under the contract that produced its ``run.meta``.

    Current records declare ``payload_path_base=run-meta-directory`` and store
    inline streams as sibling filenames. Historical records declare nothing;
    their original working-directory interpretation is preserved rather than
    guessing a new base and silently binding different bytes. Absolute external
    paths are absolute under either contract.

    Raises :class:`VerifierError` on a base this build does not implement — the
    caller turns that into the receipt-specific refusal the shell spells.
    """
    if recorded.startswith("/"):
        return recorded
    base = read_meta(run_meta).get("payload_path_base", "")
    if base == "run-meta-directory":
        return f"{run_meta.parent}/{recorded}"
    if base == "":
        return recorded
    raise VerifierError(f"unsupported payload_path_base: {base}")


def real(path: str | Path) -> str:
    """``readlink -f`` with the shell's fallback to the literal spelling."""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)
