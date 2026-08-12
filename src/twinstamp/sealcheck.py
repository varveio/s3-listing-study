"""Dependency-free, canonical marker parsing and single-read observation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from twinstamp.stores import ObjectReadError, ObjectReadIssue, ObjectStoreReader


class MarkerState(StrEnum):
    """Storage-level state of a marker, before domain validation."""

    ABSENT = "absent"
    PRESENT = "present"
    INVALID = "invalid"


class MarkerIssue(StrEnum):
    """Format-independent reasons a marker could not become a seal candidate."""

    INVALID_SIZE = "invalid_size"
    TOO_LARGE = "too_large"
    CHANGED = "changed"
    INVALID_UTF8_JSON = "invalid_utf8_json"
    DUPLICATE_KEY = "duplicate_key"
    NOT_AN_OBJECT = "not_an_object"
    NONCANONICAL = "noncanonical"


@dataclass(frozen=True, slots=True)
class MarkerObservation:
    """One bounded marker read and, when valid, its canonical JSON object."""

    key: str
    state: MarkerState
    document: dict[str, Any] | None = None
    version: str | int | None = None
    issue: MarkerIssue | None = None

    def __post_init__(self) -> None:
        present = self.state is MarkerState.PRESENT
        invalid = self.state is MarkerState.INVALID
        if present != (self.document is not None):
            raise ValueError("exactly a present marker must carry a document")
        if invalid != (self.issue is not None):
            raise ValueError("exactly an invalid marker must carry an issue")


class _DuplicateKey(ValueError):
    pass


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def parse_canonical_json_marker(
    content: bytes,
    *,
    key: str,
    version: str | int | None = None,
    trailing_newline: bool = True,
) -> MarkerObservation:
    """Parse canonical UTF-8 JSON without duplicate keys.

    Canonical form uses sorted keys and compact separators. ``trailing_newline``
    chooses whether exactly one final LF is part of the stored convention.
    """

    try:
        value = json.loads(
            content,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateKey:
        return MarkerObservation(
            key, MarkerState.INVALID, version=version, issue=MarkerIssue.DUPLICATE_KEY
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return MarkerObservation(
            key, MarkerState.INVALID, version=version, issue=MarkerIssue.INVALID_UTF8_JSON
        )
    if not isinstance(value, dict):
        return MarkerObservation(
            key, MarkerState.INVALID, version=version, issue=MarkerIssue.NOT_AN_OBJECT
        )
    try:
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError, RecursionError):
        return MarkerObservation(
            key, MarkerState.INVALID, version=version, issue=MarkerIssue.INVALID_UTF8_JSON
        )
    if trailing_newline:
        canonical += b"\n"
    if content != canonical:
        return MarkerObservation(
            key, MarkerState.INVALID, version=version, issue=MarkerIssue.NONCANONICAL
        )
    return MarkerObservation(key, MarkerState.PRESENT, value, version)


@dataclass(frozen=True, slots=True)
class CanonicalJsonMarker:
    """A named, bounded canonical-JSON marker convention."""

    name: str
    max_bytes: int
    trailing_newline: bool = True

    def __post_init__(self) -> None:
        if not self.name or "/" in self.name:
            raise ValueError("marker name must be one nonempty path component")
        if self.max_bytes < 1:
            raise ValueError("marker max_bytes must be positive")

    def observe(self, store: ObjectStoreReader, unit_prefix: str) -> MarkerObservation:
        """Read and parse the unit marker exactly once."""

        key = f"{unit_prefix}/{self.name}"
        try:
            stored = store.read_object(key, max_bytes=self.max_bytes)
        except ObjectReadError as exc:
            issue = {
                ObjectReadIssue.INVALID_SIZE: MarkerIssue.INVALID_SIZE,
                ObjectReadIssue.TOO_LARGE: MarkerIssue.TOO_LARGE,
                ObjectReadIssue.CHANGED: MarkerIssue.CHANGED,
            }[exc.issue]
            return MarkerObservation(key, MarkerState.INVALID, issue=issue)
        if stored is None:
            return MarkerObservation(key, MarkerState.ABSENT)
        return parse_canonical_json_marker(
            stored.content,
            key=key,
            version=stored.version,
            trailing_newline=self.trailing_newline,
        )
