from tests.static_analyzer.program_info.source_facts.conftest import evidence_view, import_view


def test_grouped_alias_blank_and_dot_imports(extract):
    facts = extract(
        "main.go", 'package p\nimport (\n  "fmt"\n  alias "example.com/lib"\n  _ "driver/sql"\n  . "tools/all"\n)\n'
    )
    assert import_view(facts) == [
        (
            "driver/sql",
            (("sql", "_", "driver/sql", False),),
            0,
            False,
            False,
            False,
            facts.imports[0].evidence.spelling,
            "import_declaration",
        ),
        (
            "example.com/lib",
            (("lib", "alias", "example.com/lib", False),),
            0,
            False,
            False,
            False,
            facts.imports[1].evidence.spelling,
            "import_declaration",
        ),
        (
            "fmt",
            (("fmt", "", "fmt", False),),
            0,
            False,
            False,
            False,
            facts.imports[2].evidence.spelling,
            "import_declaration",
        ),
        (
            "tools/all",
            (("all", ".", "tools/all", True),),
            0,
            False,
            False,
            False,
            facts.imports[3].evidence.spelling,
            "import_declaration",
        ),
    ]
    assert {fact.evidence.spelling for fact in facts.imports} == {facts.imports[0].evidence.spelling}


def test_go_types_preserve_qualified_generic_container_identifiers(extract):
    facts = extract(
        "typed.go",
        "package p\ntype Box struct { values []pkg.Item }\nfunc Put(v map[string]*Result) chan Output { panic(0) }\n",
    )
    names = [row[0] for row in evidence_view(facts)]
    assert names == ["pkg.Item", "Result", "Output"]
    assert all(fact.evidence.language == "go" for fact in facts.type_uses)
    assert all(fact.evidence.provenance == "tree-sitter" for fact in facts.type_uses)


def test_go_comments_strings_and_malformed_import(extract):
    facts = extract("bad.go", 'package p\n// import "fake"\nvar s = `import "wrong"`\nimport name\n')
    assert facts.imports == ()
    assert len(facts.diagnostics) == 1
    assert facts.diagnostics[0].code == "unparsed-import"
    assert facts.diagnostics[0].span.start_line == 3
