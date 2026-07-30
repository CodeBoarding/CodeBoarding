from pathlib import Path

import pytest

from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.program_info.source_facts import FileSourceFacts


@pytest.fixture
def extract(tmp_path: Path):
    def run(name: str, source: str) -> FileSourceFacts:
        path = tmp_path / name
        path.write_text(source)
        return SourceInspector().extract_source_facts(path)

    return run


def import_view(facts: FileSourceFacts) -> list[tuple]:
    return [
        (
            fact.path,
            tuple((binding.name, binding.alias, binding.target, binding.is_wildcard) for binding in fact.bindings),
            fact.relative_level,
            fact.is_static,
            fact.is_side_effect,
            fact.is_global,
            fact.evidence.spelling,
            fact.evidence.node_kind,
        )
        for fact in facts.imports
    ]


def evidence_view(facts: FileSourceFacts) -> list[tuple]:
    return [
        (
            fact.name,
            fact.evidence.span.start_line,
            fact.evidence.span.start_column,
            fact.evidence.span.end_line,
            fact.evidence.spelling,
            fact.evidence.language,
            fact.evidence.provenance,
        )
        for fact in facts.type_uses
    ]
