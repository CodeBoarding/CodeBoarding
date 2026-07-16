"""Concise constructors for typed program-graph test fixtures."""

from static_analyzer.constants import Language, NodeType
from static_analyzer.program_graph import ProgramNode, ProgramNodeKind


def make_symbol(
    qualified_name: str,
    node_type: NodeType | int,
    file_path: str,
    line_start: int,
    line_end: int,
    col_start: int = 0,
    *,
    language: str | Language = "",
    reference_worthy: bool = True,
) -> ProgramNode:
    return ProgramNode(
        node_id=qualified_name,
        kind=ProgramNodeKind.SYMBOL,
        language=str(language),
        name=qualified_name.rsplit(".", maxsplit=1)[-1],
        file_path=file_path,
        symbol_type=NodeType(node_type),
        line_start=line_start,
        line_end=line_end,
        col_start=col_start,
        reference_worthy=reference_worthy,
    )
