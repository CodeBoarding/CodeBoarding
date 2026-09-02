"""Naming for the Java adapter: what a symbol is called, and which package it sits in."""

from pathlib import Path

from static_analyzer.config import NodeType
from static_analyzer.engine.adapters.java_adapter import JavaAdapter

ROOT = Path("/repo")


def _name(rel: str, symbol: str, kind: int = NodeType.CLASS, parents: list[tuple[str, int]] | None = None) -> str:
    return JavaAdapter().build_qualified_name(ROOT / rel, symbol, kind, parents or [], ROOT)


class TestBuildQualifiedName:
    def test_maven_source_root_names_no_package(self):
        assert (
            _name("mockito-core/src/main/java/org/mockito/Answers.java", "Answers")
            == "mockito-core.org.mockito.Answers"
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
            _name("core/src/test/java/org/mockito/Fixture.java", "Fixture") == "core.test.org.mockito.Fixture"
        ) and _name("core/src/main/java/org/mockito/Fixture.java", "Fixture") == "core.org.mockito.Fixture"

    def test_an_arbitrary_gradle_source_set_is_recognised(self):
        """Gradle names its own: `integrationTest`, Android's `androidTest`."""
        name = _name("app/src/integrationTest/java/com/acme/SmokeTest.java", "SmokeTest")
        assert name == "app.integrationTest.com.acme.SmokeTest"

    def test_a_kotlin_source_root_is_stripped(self):
        assert _name("app/src/main/kotlin/com/acme/Main.kt", "Main") == "app.com.acme.Main"

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
        assert package == "mockito-core.org.mockito"
        assert adapter.build_qualified_name(source, "Answers", NodeType.CLASS, [], ROOT).startswith(package + ".")

    def test_every_package_is_collected(self):
        adapter = JavaAdapter()
        sources = [
            ROOT / "a/src/main/java/com/acme/One.java",
            ROOT / "b/src/main/java/com/acme/Two.java",
        ]
        assert adapter.get_all_packages(sources, ROOT) == {"a.com.acme", "b.com.acme"}
