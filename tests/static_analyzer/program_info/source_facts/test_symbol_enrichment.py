from pathlib import Path

from static_analyzer.engine.models import SymbolInfo
from static_analyzer.program_info.source_facts import enrich_and_resolve_source_facts


def make(name: str, qname: str, start: int, end: int) -> SymbolInfo:
    return SymbolInfo(name, qname, 12, Path("/tmp/module.py"), start, 0, end, 80)


def test_narrowest_containing_symbol_receives_type_evidence(extract):
    facts = extract("/tmp/module.py", "class Outer:\n    def inner(value: Widget):\n        pass\n")
    outer = make("Outer", "module.Outer", 0, 2)
    inner = make("inner", "module.Outer.inner", 1, 2)
    widget = SymbolInfo("Widget", "domain.Widget", 5, Path("/tmp/domain.py"), 0, 0, 1, 0)
    resolved = enrich_and_resolve_source_facts((facts,), {"outer": outer, "inner": inner, "widget": widget})
    assert resolved.type_edges == (("module.Outer.inner", "domain.Widget"),)
    assert outer.type_use_evidence == ()
    assert [fact.name for fact in inner.type_use_evidence] == ["Widget"]


def test_decorator_immediately_preceding_declaration_is_attached(extract):
    facts = extract("/tmp/module.py", "@deprecated\ndef execute():\n    pass\n")
    execute = make("execute", "module.execute", 1, 2)
    enrich_and_resolve_source_facts((facts,), {"execute": execute})
    assert execute.visibility == "unknown"
    assert execute.modifiers == ()
    assert len(execute.annotations) == 1
    assert execute.annotations[0].name == "deprecated"
    assert execute.annotations[0].evidence.span.start_line == 0


def test_primary_and_alias_mapping_enriches_symbol_once(extract):
    facts = extract("/tmp/module.py", "def run(value: Item): ...\n")
    run = make("run", "module.run", 0, 0)
    item = SymbolInfo("Item", "domain.Item", 5, Path("/tmp/item.py"), 0, 0, 1, 0)
    resolved = enrich_and_resolve_source_facts((facts,), {"run": run, "short-run": run, "item": item})
    assert resolved.type_edges == (("module.run", "domain.Item"),)
    assert len(run.type_use_evidence) == 1


def test_import_without_containing_source_symbol_remains_syntax_fact(extract):
    facts = extract("/tmp/module.py", "from domain import Item\n")
    item = SymbolInfo("Item", "domain.Item", 5, Path("/tmp/item.py"), 0, 0, 1, 0)
    resolved = enrich_and_resolve_source_facts((facts,), {"item": item})
    assert len(facts.imports) == 1
    assert resolved.import_edges == ()
    assert resolved.diagnostics == ()
