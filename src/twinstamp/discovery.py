"""Immediate-child discovery with a bounded accepted-child set."""

from __future__ import annotations

from typing import TypeVar

from twinstamp.profiles import EvidenceProfile, foreign_profile_name
from twinstamp.resolution import DiscoveredUnit, UnrecognizedEvidenceUnit
from twinstamp.stores import ObjectStoreReader

U = TypeVar("U")


class ChildLimitExceeded(RuntimeError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"slot prefix exceeds {limit} evidence-unit children")
        self.limit = limit


def discover_units(
    store: ObjectStoreReader,
    prefix: str,
    profile: EvidenceProfile[U],
    *,
    max_children: int = 256,
) -> tuple[DiscoveredUnit[U], ...]:
    """Discover only unique immediate child prefixes below ``prefix``."""

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
