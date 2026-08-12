"""Structurally distinct evidence-unit identity profiles."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

U = TypeVar("U")


class EvidenceProfile(Protocol[U]):
    """Parse and render the one unit-key grammar selected for a slot."""

    name: str

    def parse(self, key: str) -> U | None: ...

    def render(self, unit: U) -> str: ...


@dataclass(frozen=True, slots=True)
class PhysicalExecutionUnit:
    """One physical invocation, preserved under its invocation-unique UUID."""

    execution_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LogicalAttemptUnit:
    """One backend-defined logical scheduler attempt coordinate."""

    task_index: int
    retry_index: int
    runnable_index: int

    def __post_init__(self) -> None:
        if min(self.task_index, self.retry_index, self.runnable_index) < 0:
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
        return str(unit.execution_id)


_LOGICAL_KEY = re.compile(
    r"logical-task-(0|[1-9][0-9]*)-retry-(0|[1-9][0-9]*)-runnable-(0|[1-9][0-9]*)"
)


class LogicalAttemptProfile:
    name = "logical-attempt"

    def parse(self, key: str) -> LogicalAttemptUnit | None:
        match = _LOGICAL_KEY.fullmatch(key)
        if match is None:
            return None
        return LogicalAttemptUnit(*(int(value) for value in match.groups()))

    def render(self, unit: LogicalAttemptUnit) -> str:
        return (
            f"logical-task-{unit.task_index}-retry-{unit.retry_index}"
            f"-runnable-{unit.runnable_index}"
        )


PHYSICAL_EXECUTION = PhysicalExecutionProfile()
LOGICAL_ATTEMPT = LogicalAttemptProfile()


def foreign_profile_name(expected: EvidenceProfile[Any], key: str) -> str | None:
    """Return the other built-in profile when its grammar accepts ``key``."""

    for profile in (PHYSICAL_EXECUTION, LOGICAL_ATTEMPT):
        if profile.name != expected.name and profile.parse(key) is not None:
            return profile.name
    return None
