from tests.static_analyzer.program_info.source_facts.conftest import evidence_view, import_view


def test_plain_alias_from_relative_and_wildcard_imports(extract):
    facts = extract(
        "sample.py",
        "import os, pkg.mod as pm\nfrom ..core import Item as Alias, *\n",
    )
    assert import_view(facts) == [
        ("os", (("os", "", "os", False),), 0, False, False, False, "import os, pkg.mod as pm", "import_statement"),
        (
            "pkg.mod",
            (("pkg", "pm", "pkg.mod", False),),
            0,
            False,
            False,
            False,
            "import os, pkg.mod as pm",
            "import_statement",
        ),
        (
            "core",
            (("Item", "Alias", "core.Item", False), ("*", "", "core", True)),
            2,
            False,
            False,
            False,
            "from ..core import Item as Alias, *",
            "import_from_statement",
        ),
    ]
    assert facts.diagnostics == ()


def test_annotations_types_declarations_and_spans(extract):
    facts = extract("typed.py", "@sealed\nclass Box:\n    def put(self, value: Widget) -> Result:\n        pass\n")
    assert [(item.name, item.evidence.spelling, item.evidence.span.start_line) for item in facts.annotations] == [
        ("sealed", "@sealed", 0)
    ]
    assert evidence_view(facts) == [
        ("Widget", 2, 25, 2, "Widget", "python", "tree-sitter"),
        ("Result", 2, 36, 2, "Result", "python", "tree-sitter"),
    ]
    declarations = {item.name: item for item in facts.declarations}
    assert declarations["Box"].annotations == facts.annotations
    assert declarations["Box"].visibility.value == "unknown"
    assert declarations["put"].annotations == ()
    assert declarations["put"].evidence.span.start_line == 2


def test_comments_strings_and_calls_do_not_create_syntax_facts(extract):
    facts = extract("quiet.py", '# from fake import Bad\ntext = "import wrong"\nimportlib.import_module("dynamic")\n')
    assert facts.imports == ()
    assert facts.type_uses == ()
    assert facts.annotations == ()
    assert facts.diagnostics == ()


def test_results_are_deterministic_across_fresh_inspectors(extract):
    source = "from z import B\nfrom a import A\ndef f(x: Z) -> A: ...\n"
    first = extract("one.py", source)
    second = extract("two.py", source)
    assert [fact.path for fact in first.imports] == ["z", "a"]
    assert [fact.name for fact in first.type_uses] == ["Z", "A"]
    assert [(fact.path, fact.bindings) for fact in first.imports] == [
        (fact.path, fact.bindings) for fact in second.imports
    ]
