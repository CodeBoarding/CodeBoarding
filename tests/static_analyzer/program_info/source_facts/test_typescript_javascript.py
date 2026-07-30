from tests.static_analyzer.program_info.source_facts.conftest import evidence_view, import_view


def test_ecmascript_import_forms(extract):
    facts = extract(
        "imports.ts",
        'import main, {Alpha as A, Beta} from "pkg";\nimport * as ns from "space";\nimport "setup";\n',
    )
    assert import_view(facts) == [
        (
            "pkg",
            (("default", "main", "pkg", False), ("Alpha", "A", "pkg.Alpha", False), ("Beta", "", "pkg.Beta", False)),
            0,
            False,
            False,
            False,
            'import main, {Alpha as A, Beta} from "pkg";',
            "import_statement",
        ),
        (
            "space",
            (("*", "ns", "space", True),),
            0,
            False,
            False,
            False,
            'import * as ns from "space";',
            "import_statement",
        ),
        ("setup", (), 0, False, True, False, 'import "setup";', "import_statement"),
    ]


def test_commonjs_destructuring_and_computed_require(extract):
    facts = extract("common.js", 'const {one, two} = require("lib");\nconst bad = require(name);\n')
    assert import_view(facts) == [
        (
            "lib",
            (("one", "", "lib.one", False), ("two", "", "lib.two", False)),
            0,
            False,
            False,
            False,
            'const {one, two} = require("lib");',
            "lexical_declaration",
        )
    ]
    assert facts.diagnostics == ()


def test_typescript_native_type_nodes_and_decorator_attachment(extract):
    facts = extract(
        "types.ts",
        "@sealed\nexport class Service { public async run(arg: Array<Input>): Promise<Output | null> { throw 0; } }\n",
    )
    assert evidence_view(facts) == [
        ("Input", 1, 51, 1, "Input", "typescript", "tree-sitter"),
        ("Output", 1, 68, 1, "Output", "typescript", "tree-sitter"),
    ]
    declarations = {fact.name: fact for fact in facts.declarations}
    assert declarations["Service"].modifiers == ("export",)
    assert declarations["Service"].annotations == facts.annotations
    assert declarations["run"].visibility.value == "public"
    assert declarations["run"].modifiers == ("async", "public")


def test_javascript_comments_strings_and_dynamic_import_are_ignored(extract):
    facts = extract("dynamic.js", '// import A from "a"\nconst s = "import X";\nimport(target);\n')
    assert facts.imports == ()
    assert facts.type_uses == ()
    assert facts.diagnostics == ()
