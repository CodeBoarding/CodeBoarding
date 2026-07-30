from tests.static_analyzer.program_info.source_facts.conftest import evidence_view, import_view


def test_csharp_global_static_alias_and_ordinary_using(extract):
    facts = extract("Usings.cs", "global using App.Core;\nusing static System.Math;\nusing IO = System.IO;\n")
    assert import_view(facts) == [
        (
            "App.Core",
            (("Core", "", "App.Core", False),),
            0,
            False,
            False,
            True,
            "global using App.Core;",
            "using_directive",
        ),
        (
            "System.Math",
            (("Math", "", "System.Math", False),),
            0,
            True,
            False,
            False,
            "using static System.Math;",
            "using_directive",
        ),
        (
            "System.IO",
            (("IO", "IO", "System.IO", False),),
            0,
            False,
            False,
            False,
            "using IO = System.IO;",
            "using_directive",
        ),
    ]


def test_csharp_attributes_nullable_arrays_generic_types_and_modifiers(extract):
    facts = extract(
        "Box.cs",
        "[Obsolete]\npublic sealed class Box {\n [Audit] protected internal async Task<Result?> Run(Input[] xs) => null;\n}\n",
    )
    declarations = {fact.name: fact for fact in facts.declarations}
    assert declarations["Box"].visibility.value == "public"
    assert declarations["Box"].modifiers == ("public", "sealed")
    assert [annotation.name for annotation in declarations["Box"].annotations] == ["Obsolete"]
    assert declarations["Run"].visibility.value == "protected"
    assert declarations["Run"].modifiers == ("async", "internal", "protected")
    assert [annotation.name for annotation in declarations["Run"].annotations] == ["Audit"]
    assert [row[0] for row in evidence_view(facts)] == ["Task", "Result", "Input"]


def test_csharp_comments_strings_and_computed_loading_are_ignored(extract):
    facts = extract("Quiet.cs", '// using Fake.Bad;\nvar text = "using Wrong.Name;";\nAssembly.Load(name);\n')
    assert facts.imports == ()
    assert facts.diagnostics == ()


def test_cpp_is_explicitly_unsupported(extract):
    facts = extract("main.cpp", "#include <vector>\nstd::vector<int> values;\n")
    assert facts.supported is False
    assert facts.language == "cpp"
    assert facts.imports == ()
    assert facts.type_uses == ()
    assert [(item.code, item.message) for item in facts.diagnostics] == [
        ("unsupported-language", "No parser for cpp")
    ]
