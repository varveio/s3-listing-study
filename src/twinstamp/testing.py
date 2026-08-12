"""Dependency-free store test double for core clients and conformance tests."""

from __future__ import annotations

from collections.abc import Iterable

from twinstamp.stores import ObjectReadError, ObjectReadIssue, StoredObject


class MemoryObjectStore:
    """In-memory reader recording listing and bounded-read calls for assertions."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.list_calls: list[str] = []
        self.read_calls: list[tuple[str, int]] = []

    def iter_child_prefixes(self, prefix: str) -> Iterable[str]:
        self.list_calls.append(prefix)
        root = f"{prefix}/"
        return sorted(
            {
                root + name.removeprefix(root).split("/", 1)[0] + "/"
                for name in self.objects
                if name.startswith(root) and "/" in name.removeprefix(root)
            }
        )

    def read_object(self, key: str, *, max_bytes: int) -> StoredObject | None:
        self.read_calls.append((key, max_bytes))
        content = self.objects.get(key)
        if content is None:
            return None
        if len(content) > max_bytes:
            raise ObjectReadError(ObjectReadIssue.TOO_LARGE, key)
        return StoredObject(content)
