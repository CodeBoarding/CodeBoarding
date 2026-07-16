"""Per-language ProgramGraph storage."""

from collections.abc import Callable
from dataclasses import dataclass, field

from static_analyzer.program_graph import ProgramGraph


@dataclass
class LanguageResults:
    program_graph: ProgramGraph | None = None
    source_files: list[str] = field(default_factory=list)

    def merge_graph(self, graph: ProgramGraph) -> None:
        if self.program_graph is None:
            self.program_graph = graph
        else:
            self.program_graph.merge(graph)

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        if self.program_graph is not None:
            self.program_graph.visit_paths(fn)
        self.source_files = [fn(path) for path in self.source_files]
