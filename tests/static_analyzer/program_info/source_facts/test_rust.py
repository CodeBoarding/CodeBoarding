from tests.static_analyzer.program_info.source_facts.conftest import evidence_view, import_view


def test_rust_nested_self_alias_and_wildcard_use(extract):
    facts = extract("lib.rs", "use crate::io::{self, Read as Reader, nested::{Thing, *}};\n")
    assert import_view(facts) == [
        (
            "crate::io",
            (
                ("io", "", "crate::io", False),
                ("Read", "Reader", "crate::io::Read", False),
                ("Thing", "", "crate::io::nested::Thing", False),
                ("*", "", "crate::io::nested::*", True),
            ),
            0,
            False,
            False,
            False,
            "use crate::io::{self, Read as Reader, nested::{Thing, *}};",
            "use_declaration",
        )
    ]


def test_rust_attributes_visibility_modifiers_and_types(extract):
    facts = extract(
        "typed.rs",
        "#[derive(Clone)]\npub struct Box { item: Option<Input> }\npub async fn make(x: &Thing) -> Result<Output, Error> { todo!() }\n",
    )
    declarations = {fact.name: fact for fact in facts.declarations}
    assert declarations["Box"].visibility.value == "public"
    assert declarations["Box"].modifiers == ("pub",)
    assert [annotation.name for annotation in declarations["Box"].annotations] == ["derive"]
    assert declarations["make"].modifiers == ("async", "pub")
    assert [row[0] for row in evidence_view(facts)] == ["Option", "Input", "Thing", "Result", "Output", "Error"]


def test_rust_macro_and_non_code_import_spellings_are_ignored(extract):
    facts = extract("quiet.rs", '// use fake::Bad;\nconst S: &str = "use wrong::Thing;";\ninclude!(path);\n')
    assert facts.imports == ()
    assert facts.diagnostics == ()
