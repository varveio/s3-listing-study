"""Small argparse actions shared by strict public CLI boundaries."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any


class UniqueStoreAction(argparse.Action):
    """Store one value while rejecting repeated singleton options."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        if not isinstance(values, str):
            raise argparse.ArgumentError(self, f"{option_string} requires exactly one value")
        seen: frozenset[str] = getattr(namespace, "_seen_singletons", frozenset())
        if self.dest in seen:
            raise argparse.ArgumentError(self, f"{option_string} may only be specified once")
        namespace._seen_singletons = seen | {self.dest}
        setattr(namespace, self.dest, values)
