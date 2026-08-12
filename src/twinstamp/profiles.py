"""Structurally distinct evidence-unit identity profiles."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, TypeVar

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


PHYSICAL_EXECUTION = PhysicalExecutionProfile()
