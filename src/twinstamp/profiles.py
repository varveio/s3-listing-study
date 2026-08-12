"""Structurally distinct evidence-unit identity profiles."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

U = TypeVar("U")


class EvidenceProfile(Protocol[U]):
    """Define one evidence-unit identity grammar for a result slot.

    ``parse`` returns ``None`` for a noncanonical or foreign key; ``render``
    produces the canonical key for a valid unit.  Profiles used together should
    have mutually non-parseable grammars so a foreign profile is observable.
    """

    name: str

    def parse(self, key: str) -> U | None: ...

    def render(self, unit: U) -> str: ...


@dataclass(frozen=True, slots=True)
class PhysicalExecutionUnit:
    """One physical invocation, identified by an invocation-unique UUIDv4.

    Separate launches receive separate units even when they arise from one
    submission, preserving physical duplicate evidence for reconciliation.
    """

    execution_id: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, uuid.UUID) or self.execution_id.version != 4:
            raise ValueError("physical execution ID must be a UUIDv4")


@dataclass(frozen=True, slots=True)
class LogicalAttemptUnit:
    """One backend-defined logical scheduler coordinate.

    The three non-negative coordinates distinguish documented task, retry, and
    runnable positions.  This profile relies on the backend not assigning the
    same complete coordinate to multiple physical invocations.
    """

    task_index: int
    retry_index: int
    runnable_index: int

    def __post_init__(self) -> None:
        coordinates = (self.task_index, self.retry_index, self.runnable_index)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in coordinates):
            raise TypeError("logical attempt coordinates must be integers")
        if min(coordinates) < 0:
            raise ValueError("logical attempt coordinates must be non-negative")


class PhysicalExecutionProfile:
    name = "physical-execution"

    def parse(self, key: str) -> PhysicalExecutionUnit | None:
        try:
            parsed = uuid.UUID(key)
        except ValueError:
            return None
        if str(parsed) != key or parsed.version != 4:
            return None
        return PhysicalExecutionUnit(parsed)

    def render(self, unit: PhysicalExecutionUnit) -> str:
        key = str(unit.execution_id)
        if self.parse(key) != unit:
            raise ValueError("physical execution unit does not round-trip")
        return key


_LOGICAL_KEY = re.compile(
    r"logical-task-(0|[1-9][0-9]*)-retry-(0|[1-9][0-9]*)-runnable-(0|[1-9][0-9]*)"
)


class LogicalAttemptProfile:
    name = "logical-attempt"

    def parse(self, key: str) -> LogicalAttemptUnit | None:
        match = _LOGICAL_KEY.fullmatch(key)
        if match is None:
            return None
        try:
            return LogicalAttemptUnit(*(int(value) for value in match.groups()))
        except ValueError:
            return None

    def render(self, unit: LogicalAttemptUnit) -> str:
        key = (
            f"logical-task-{unit.task_index}-retry-{unit.retry_index}"
            f"-runnable-{unit.runnable_index}"
        )
        if self.parse(key) != unit:
            raise ValueError("logical attempt unit does not round-trip")
        return key


PHYSICAL_EXECUTION = PhysicalExecutionProfile()
LOGICAL_ATTEMPT = LogicalAttemptProfile()


def foreign_profile_name(expected: EvidenceProfile[Any], key: str) -> str | None:
    """Return the other built-in profile whose grammar accepts ``key``, if any.

    ``expected`` is excluded from the comparison.  The result helps discovery
    retain a structurally recognizable foreign-profile child as an anomaly.
    """

    for profile in (PHYSICAL_EXECUTION, LOGICAL_ATTEMPT):
        if profile.name != expected.name and profile.parse(key) is not None:
            return profile.name
    return None
