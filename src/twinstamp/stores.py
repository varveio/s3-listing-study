"""Object-store read contracts and pure ambiguous-create observations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    content: bytes
    version: str | int | None = None


class ObjectStoreReader(Protocol):
    """The manager-side read surface needed by reconciliation."""

    def iter_child_prefixes(self, prefix: str) -> Iterable[str]: ...

    def read_object(self, key: str, *, max_bytes: int) -> StoredObject | None: ...


class AmbiguousCreateState(StrEnum):
    CONFIRMED = "confirmed"
    CONFLICT = "conflict"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class AmbiguousCreateResolution:
    state: AmbiguousCreateState
    observed_version: str | int | None


def resolve_ambiguous_create(
    *, expected_digest: str, observed_digest: str | None, observed_version: str | int | None
) -> AmbiguousCreateResolution:
    """Classify generation/version and digest read-back after an ambiguous create."""

    if observed_digest is None or observed_version is None:
        return AmbiguousCreateResolution(AmbiguousCreateState.UNRESOLVED, observed_version)
    if observed_digest == expected_digest:
        return AmbiguousCreateResolution(AmbiguousCreateState.CONFIRMED, observed_version)
    return AmbiguousCreateResolution(AmbiguousCreateState.CONFLICT, observed_version)


def fixed_publication_order(artifact_keys: Iterable[str], marker_key: str) -> tuple[str, ...]:
    """Return the canonical artifact order followed by the marker-last seal."""

    artifacts = tuple(sorted(artifact_keys))
    if not marker_key or marker_key in artifacts or len(set(artifacts)) != len(artifacts):
        raise ValueError("publication keys must be unique and the marker must be distinct")
    return (*artifacts, marker_key)
