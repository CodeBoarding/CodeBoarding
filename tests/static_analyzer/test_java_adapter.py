"""The Java adapter: what a symbol is called, which package it sits in, and which server errors end a run."""

from pathlib import Path

from static_analyzer.config import NodeType
from static_analyzer.engine.adapters.java_adapter import JavaAdapter
from static_analyzer.engine.lsp_client import ErrorVerdict

ROOT = Path("/repo")
SELECTION_CRASH = {
    "code": -32603,
    "message": "Internal error.",
    "data": (
        "java.util.concurrent.CompletionException: java.lang.NullPointerException: "
        'Cannot read field "type" because "argument.binding" is null\n'
        "\tat org.eclipse.jdt.internal.codeassist.SelectionEngine$1Visitor.visit(SelectionEngine.java:1473)"
    ),
}


def _name(rel: str, symbol: str, kind: int = NodeType.CLASS, parents: list[tuple[str, int]] | None = None) -> str:
    return JavaAdapter().build_qualified_name(ROOT / rel, symbol, kind, parents or [], ROOT)


class TestErrorVerdict:
    def test_a_code_selection_crash_leaves_one_edge_out(self, caplog):
        """Retrying crashes the same way, on dropwizard/metrics, and that one site ended the whole analysis."""
        assert JavaAdapter().error_verdict(SELECTION_CRASH) is ErrorVerdict.DECLINED
        assert "leaving its edge out" in caplog.text

    def test_any_other_internal_error_still_fails_the_run(self):
        """Reading an unknown failure as empty would lose edges silently."""
        error = {"code": -32603, "message": "Internal error.", "data": "java.lang.OutOfMemoryError: Java heap space"}
        assert JavaAdapter().error_verdict(error) is ErrorVerdict.FAILURE

    def test_a_null_pointer_outside_code_selection_still_fails_the_run(self):
        error = {"code": -32603, "data": "java.lang.NullPointerException\n\tat org.eclipse.jdt.ls.core.Handler.run"}
        assert JavaAdapter().error_verdict(error) is ErrorVerdict.FAILURE

    def test_an_error_without_data_still_fails_the_run(self):
        assert JavaAdapter().error_verdict({"code": -32603, "message": "Internal error."}) is ErrorVerdict.FAILURE


class TestBuildQualifiedName:
    def test_a_maven_source_root_is_spelled_as_it_is_on_disk(self):
        """Every adapter spells the path from the repository root; the walk over the tree steps through ``src``."""
        assert (
            _name("mockito-core/src/main/java/org/mockito/Answers.java", "Answers")
            == "mockito-core.src.main.java.org.mockito.Answers"
        )

    def test_a_type_is_not_doubled_by_its_own_file(self):
        """The stem equals the top-level type, so folding it in made the class its own parent."""
        assert _name("core/Animal.java", "Animal") == "core.Animal"

    def test_a_member_is_prefixed_by_its_class(self):
        member = _name("core/Animal.java", "speak()", NodeType.METHOD, [("Animal", NodeType.CLASS)])
        assert member == "core.Animal.speak()"
        assert member.startswith(_name("core/Animal.java", "Animal") + ".")

    def test_a_nested_type_keeps_the_whole_chain(self):
        parents: list[tuple[str, int]] = [("Outer", NodeType.CLASS), ("Middle", NodeType.CLASS)]
        assert _name("core/Outer.java", "Inner", NodeType.CLASS, parents) == "core.Outer.Middle.Inner"

    def test_a_package_private_sibling_is_not_named_as_a_nested_type(self):
        assert _name("core/Animal.java", "Dog") == "core.Dog"

    def test_a_test_source_set_stays_in_the_name(self):
        """`src/main/java` and `src/test/java` can hold the same package and type name."""
        assert (
            _name("core/src/test/java/org/mockito/Fixture.java", "Fixture") == "core.src.test.java.org.mockito.Fixture"
        ) and _name(
            "core/src/main/java/org/mockito/Fixture.java", "Fixture"
        ) == "core.src.main.java.org.mockito.Fixture"

    def test_an_arbitrary_gradle_source_set_is_recognised(self):
        """Gradle names its own: `integrationTest`, Android's `androidTest`."""
        name = _name("app/src/integrationTest/java/com/acme/SmokeTest.java", "SmokeTest")
        assert name == "app.src.integrationTest.java.com.acme.SmokeTest"

    def test_a_kotlin_source_root_is_kept_too(self):
        assert _name("app/src/main/kotlin/com/acme/Main.kt", "Main") == "app.src.main.kotlin.com.acme.Main"

    def test_a_flat_layout_keeps_every_directory(self):
        assert _name("com/acme/Widget.java", "Widget") == "com.acme.Widget"

    def test_a_file_at_the_root_has_no_prefix(self):
        assert _name("Widget.java", "Widget") == "Widget"

    def test_a_directory_named_src_alone_is_not_a_source_root(self):
        assert _name("src/com/acme/Widget.java", "Widget") == "src.com.acme.Widget"

    def test_generics_are_stripped_from_parameters(self):
        method = _name("core/Zoo.java", "feed(List<Animal>)", NodeType.METHOD, [("Zoo", NodeType.CLASS)])
        assert method == "core.Zoo.feed(List)"


class TestGetPackageForFile:
    def test_package_matches_the_prefix_of_the_names_in_the_file(self):
        adapter = JavaAdapter()
        source = ROOT / "mockito-core/src/main/java/org/mockito/Answers.java"
        package = adapter.get_package_for_file(source, ROOT)
        assert package == "mockito-core.src.main.java.org.mockito"
        assert adapter.build_qualified_name(source, "Answers", NodeType.CLASS, [], ROOT).startswith(package + ".")

    def test_every_package_is_collected(self):
        adapter = JavaAdapter()
        sources = [
            ROOT / "a/src/main/java/com/acme/One.java",
            ROOT / "b/src/main/java/com/acme/Two.java",
        ]
        assert adapter.get_all_packages(sources, ROOT) == {"a.src.main.java.com.acme", "b.src.main.java.com.acme"}
