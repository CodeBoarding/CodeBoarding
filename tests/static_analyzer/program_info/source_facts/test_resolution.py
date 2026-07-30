from pathlib import Path

from static_analyzer.engine.models import SymbolInfo
from static_analyzer.program_info.source_facts import enrich_and_resolve_source_facts


def symbol(name: str, qname: str, file: str, start: int, end: int) -> SymbolInfo:
    return SymbolInfo(name, qname, 12, Path(file), start, 0, end, 80)


def test_alias_target_and_exact_qname_resolve_to_unique_edges(extract):
    facts = extract(
        "/tmp/app.py", "from domain.models import User as U\ndef load(value: U) -> domain.models.User: ...\n"
    )
    owner = symbol("load", "app.load", "/tmp/app.py", 1, 1)
    user = symbol("User", "domain.models.User", "/tmp/models.py", 0, 5)
    resolved = enrich_and_resolve_source_facts((facts,), {"load": owner, "user": user})
    assert resolved.type_edges == (("app.load", "domain.models.User"),)
    assert resolved.import_edges == ()
    assert resolved.diagnostics == ()
    assert [fact.name for fact in owner.type_use_evidence] == ["U", "domain.models.User"]
    assert owner.import_evidence == ()


def test_ambiguous_simple_name_reports_sorted_candidates(extract):
    facts = extract("/tmp/app.py", "def load(value: User): ...\n")
    owner = symbol("load", "app.load", "/tmp/app.py", 0, 0)
    left = symbol("User", "alpha.User", "/tmp/a.py", 0, 1)
    right = symbol("User", "zeta.User", "/tmp/z.py", 0, 1)
    resolved = enrich_and_resolve_source_facts((facts,), {"owner": owner, "right": right, "left": left})
    assert resolved.type_edges == ()
    assert len(resolved.diagnostics) == 1
    diagnostic = resolved.diagnostics[0]
    assert diagnostic.code == "ambiguous-type"
    assert diagnostic.spelling == "User"
    assert diagnostic.candidates == ("alpha.User", "zeta.User")


def test_unresolved_type_and_import_have_exact_diagnostics(extract):
    facts = extract("/tmp/app.py", "from nowhere import Missing\ndef load(value: Unknown): ...\n")
    owner = symbol("load", "app.load", "/tmp/app.py", 1, 1)
    resolved = enrich_and_resolve_source_facts((facts,), {"owner": owner})
    assert resolved.type_edges == ()
    assert resolved.import_edges == ()
    assert [(d.code, d.spelling, d.candidates) for d in resolved.diagnostics] == [
        ("unresolved-import", "Missing", ()),
        ("unresolved-type", "Unknown", ()),
    ]


def test_primitive_types_do_not_enter_resolution(extract):
    facts = extract("/tmp/app.py", "def load(value: int) -> str: ...\n")
    owner = symbol("load", "app.load", "/tmp/app.py", 0, 0)
    resolved = enrich_and_resolve_source_facts((facts,), {"owner": owner})
    assert facts.type_uses == ()
    assert resolved.type_edges == ()
    assert resolved.diagnostics == ()
