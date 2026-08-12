"""Caller-owned coordinates for result slots and submissions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from twinstamp.profiles import EvidenceProfile

U = TypeVar("U")


def _nonempty(value: str, label: str) -> None:
    if not value:
        raise ValueError(f"{label} must not be empty")


@dataclass(frozen=True, slots=True)
class ResultSlot(Generic[U]):
    """One intentionally requested answer within a caller-owned work item."""

    work_item: str
    slot: str
    prefix: str
    profile: EvidenceProfile[U]

    def __post_init__(self) -> None:
        _nonempty(self.work_item, "work_item")
        _nonempty(self.slot, "slot")
        _nonempty(self.prefix, "prefix")


@dataclass(frozen=True, slots=True)
class Submission:
    """One deliberate provider-job generation for a result slot."""

    key: str

    def __post_init__(self) -> None:
        _nonempty(self.key, "submission key")
