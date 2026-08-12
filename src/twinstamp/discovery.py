"""Immediate-child discovery with a bounded accepted-child set."""

from __future__ import annotations

from typing import TypeVar

from twinstamp.profiles import EvidenceProfile, foreign_profile_name
from twinstamp.resolution import DiscoveredUnit, UnrecognizedEvidenceUnit
from twinstamp.stores import ChildPrefixReader

U = TypeVar("U")


class ChildLimitExceeded(RuntimeError):
    """Raised when discovery accepts more immediate child units than its limit.

    The ``limit`` attribute is the configured maximum.  This is a hard failure,
    rather than a truncated discovery result, so callers cannot mistake a
    partial namespace scan for a complete slot.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(f"slot prefix exceeds {limit} evidence-unit children")
        self.limit = limit


def discover_units(
    store: ChildPrefixReader,
    prefix: str,
    profile: EvidenceProfile[U],
    *,
    max_children: int = 256,
) -> tuple[DiscoveredUnit[U], ...]:
    """Discover unique immediate child prefixes below ``prefix``.

    Args:
        store: Reader that exposes delimiter-style child prefixes.
        prefix: Caller-owned slot prefix to inspect.
        profile: The slot's single accepted unit-key grammar.
        max_children: Maximum accepted child prefixes before raising
            :class:`ChildLimitExceeded`.

    Returns:
        Units sorted by child key.  Invalid and other-profile keys are retained
        as :class:`UnrecognizedEvidenceUnit` values for the validator to assess.

    Raises:
        ValueError: If ``max_children`` is less than one.
        ChildLimitExceeded: If accepted immediate children exceed the limit.
    """

    if max_children < 1:
        raise ValueError("max_children must be positive")
    found: set[str] = set()
    for child in store.iter_child_prefixes(prefix):
        relative = child.removeprefix(f"{prefix}/").rstrip("/")
        if not relative or "/" in relative:
            continue
        found.add(relative)
        if len(found) > max_children:
            raise ChildLimitExceeded(max_children)
    discovered: list[DiscoveredUnit[U]] = []
    for key in sorted(found):
        unit = profile.parse(key)
        if unit is None:
            foreign = foreign_profile_name(profile, key)
            discovered.append(
                DiscoveredUnit(
                    key,
                    UnrecognizedEvidenceUnit(
                        key,
                        "foreign_profile" if foreign is not None else "invalid_unit_key",
                        foreign,
                    ),
                )
            )
        else:
            discovered.append(DiscoveredUnit(key, unit))
    return tuple(discovered)
