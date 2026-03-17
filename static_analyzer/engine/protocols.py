"""Protocols for engine components — breaks circular dependencies.

This module defines structural (duck-typed) interfaces that engine
components depend on.  ``LanguageAdapter`` implements these protocols,
but consumers import from here instead of from ``language_adapter``,
eliminating the circular import chain.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SymbolNaming(Protocol):
    """Methods needed by SymbolTable to build and query symbol names."""

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str: ...

    def build_reference_key(self, qualified_name: str) -> str: ...

    def is_class_like(self, symbol_kind: int) -> bool: ...

    def is_callable(self, symbol_kind: int) -> bool: ...


class EdgeBuildAdapter(Protocol):
    """Methods needed by edge-building strategies to query adapter config."""

    @property
    def references_batch_size(self) -> int: ...

    @property
    def references_per_query_timeout(self) -> int: ...

    def should_track_for_edges(self, symbol_kind: int) -> bool: ...

    def is_class_like(self, symbol_kind: int) -> bool: ...

    def is_callable(self, symbol_kind: int) -> bool: ...
