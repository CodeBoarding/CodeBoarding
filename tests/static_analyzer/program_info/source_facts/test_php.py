from tests.static_analyzer.program_info.source_facts.conftest import evidence_view, import_view


def test_php_grouped_and_aliased_use(extract):
    facts = extract("main.php", "<?php\nuse Vendor\\Package\\{Thing, Other as Alias};\nuse App\\Service as S;\n")
    assert import_view(facts) == [
        (
            "Vendor\\Package",
            (("Thing", "", "Vendor\\Package\\Thing", False), ("Other", "Alias", "Vendor\\Package\\Other", False)),
            0,
            False,
            False,
            False,
            "use Vendor\\Package\\{Thing, Other as Alias};",
            "namespace_use_declaration",
        ),
        (
            "",
            (("Service", "S", "App\\Service", False),),
            0,
            False,
            False,
            False,
            "use App\\Service as S;",
            "namespace_use_declaration",
        ),
    ]


def test_php_attributes_nullable_union_and_visibility(extract):
    facts = extract(
        "Box.php",
        "<?php\n#[Route('/')]\nfinal class Box { private static function run(?Input $x): Output|Failure {} }\n",
    )
    declarations = {fact.name: fact for fact in facts.declarations}
    assert declarations["Box"].modifiers == ("final",)
    assert [annotation.name for annotation in declarations["Box"].annotations] == ["Route"]
    assert declarations["run"].visibility.value == "private"
    assert declarations["run"].modifiers == ("private", "static")
    assert [row[0] for row in evidence_view(facts)] == ["Input", "Output", "Failure"]


def test_php_comments_strings_and_dynamic_include_are_not_imports(extract):
    facts = extract("quiet.php", '<?php\n// use Fake\\Thing;\n$x = "use Wrong\\Name;";\ninclude $path;\n')
    assert facts.imports == ()
    assert facts.annotations == ()
    assert facts.diagnostics == ()
