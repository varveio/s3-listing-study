"""Object-store read contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Bytes returned by a bounded object read, with an optional store version.

    Adapters expose a generation, ETag, or analogous immutable observation in
    ``version`` when their store supports one; the core does not interpret it.
    """

    content: bytes
    version: str | int | None = None


class ObjectReadIssue(StrEnum):
    """Portable failures produced while enforcing a bounded immutable read."""

    INVALID_SIZE = "invalid_size"
    TOO_LARGE = "too_large"
    CHANGED = "changed"


class ObjectReadError(RuntimeError):
    """A store adapter could not return one bounded, version-pinned object."""

    def __init__(self, issue: ObjectReadIssue, key: str) -> None:
        super().__init__(f"{key}: {issue.value}")
        self.issue = issue
        self.key = key


class ChildPrefixReader(Protocol):
    """Read surface required by discovery and reconciliation."""

    def iter_child_prefixes(self, prefix: str) -> Iterable[str]: ...


class ObjectStoreReader(ChildPrefixReader, Protocol):
    """Manager-side read surface for discovery and bounded object validation.

    ``iter_child_prefixes`` must expose immediate child prefixes for a supplied
    namespace.  ``read_object`` returns ``None`` when absent and must enforce
    ``max_bytes`` rather than silently returning an unbounded object. Semantic
    size/version failures raise :class:`ObjectReadError`; provider transport
    failures may propagate. Store adapters supply consistency and version
    semantics; reconciliation performs no mutation.
    """

    def read_object(self, key: str, *, max_bytes: int) -> StoredObject | None: ...
