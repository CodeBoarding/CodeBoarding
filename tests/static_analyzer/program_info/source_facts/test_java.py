from tests.static_analyzer.program_info.source_facts.conftest import evidence_view, import_view


def test_java_static_wildcard_and_ordinary_imports(extract):
    facts = extract("Main.java", "import java.util.List;\nimport static java.util.Collections.*;\nclass Main {}\n")
    assert import_view(facts) == [
        (
            "java.util",
            (("List", "", "java.util.List", False),),
            0,
            False,
            False,
            False,
            "import java.util.List;",
            "import_declaration",
        ),
        (
            "java.util.Collections",
            (("*", "", "java.util.Collections", True),),
            0,
            True,
            False,
            False,
            "import static java.util.Collections.*;",
            "import_declaration",
        ),
    ]


def test_java_annotations_visibility_modifiers_and_types(extract):
    facts = extract(
        "Box.java",
        "@Deprecated\npublic final class Box {\n  @Override protected synchronized Result run(List<Input> xs) { return null; }\n}\n",
    )
    declarations = {fact.name: fact for fact in facts.declarations}
    assert declarations["Box"].visibility.value == "public"
    assert declarations["Box"].modifiers == ("final", "public")
    assert [a.name for a in declarations["Box"].annotations] == ["Deprecated"]
    assert declarations["run"].visibility.value == "protected"
    assert declarations["run"].modifiers == ("protected", "synchronized")
    assert [a.name for a in declarations["run"].annotations] == ["Override"]
    assert [row[0] for row in evidence_view(facts)] == ["Result", "List", "Input"]


def test_java_package_visibility_and_non_code_text(extract):
    facts = extract("Quiet.java", 'class Quiet { String text = "import fake.Bad; @Wrong"; } // import nope.X;\n')
    declaration = next(fact for fact in facts.declarations if fact.name == "Quiet")
    assert declaration.visibility.value == "package"
    assert facts.imports == ()
    assert facts.annotations == ()
