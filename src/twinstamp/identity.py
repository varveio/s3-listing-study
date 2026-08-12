"""Caller-owned coordinates for result slots and submissions."""

from __future__ import annotations

from dataclasses import dataclass


def _nonempty(value: str, label: str) -> None:
    if not value:
        raise ValueError(f"{label} must not be empty")


@dataclass(frozen=True, slots=True)
class Submission:
    """An immutable key for one deliberate provider-job generation of a slot.

    A curated retry receives a new submission rather than overwriting this one;
    provider-native automatic retries remain within the submission.  ``key``
    must be nonempty.
    """

    key: str

    def __post_init__(self) -> None:
        _nonempty(self.key, "submission key")
