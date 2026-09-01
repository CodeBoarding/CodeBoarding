"""Tests for the Java language adapter's naming rules."""

from pathlib import Path

from static_analyzer.config import NodeType
from static_analyzer.engine.adapters.java_adapter import JavaAdapter

ROOT = Path("/repo")
DOG = Path("/repo/src/main/java/core/Dog.java")


def qname(file_path: Path, name: str, kind: int = NodeType.CLASS, parents=()) -> str:
    return JavaAdapter().build_qualified_name(
        file_path=file_path,
        symbol_name=name,
        symbol_kind=kind,
        parent_chain=list(parents),
        project_root=ROOT,
    )


class TestBuildQualifiedName:
    def test_top_level_type_is_not_doubled(self):
        """``Dog.java`` used to yield ``…core.Dog.Dog``: the stem, then the type."""
        assert qname(DOG, "Dog") == "src.main.java.core.Dog"

    def test_member_is_prefixed_by_its_declaring_type(self):
        """The class must be a prefix of its members, or CONTAINS cannot link them.

        Prefix arithmetic is how ``result_converter`` builds CONTAINS. While the
        class was ``…core.Dog.Dog`` and its method ``…core.Dog.speak()``, the two
        were siblings and no containment edge could exist between them.
        """
        method = qname(DOG, "speak()", NodeType.METHOD, [("Dog", NodeType.CLASS)])
        assert method == "src.main.java.core.Dog.speak()"
        assert method.startswith(qname(DOG, "Dog") + ".")

    def test_package_private_sibling_is_not_attributed_to_the_public_type(self):
        """A second top-level type in the file is a sibling, not a nested type."""
        assert qname(DOG, "Cat") == "src.main.java.core.Cat"

    def test_nested_type_keeps_its_owner(self):
        assert (
            qname(Path("/repo/src/main/java/core/Container.java"), "Item", parents=[("Container", NodeType.CLASS)])
            == "src.main.java.core.Container.Item"
        )

    def test_nested_member_keeps_the_whole_chain(self):
        assert (
            qname(
                Path("/repo/src/main/java/core/Container.java"),
                "describe()",
                NodeType.METHOD,
                [("Container", NodeType.CLASS), ("Item", NodeType.CLASS)],
            )
            == "src.main.java.core.Container.Item.describe()"
        )

    def test_root_level_file_has_no_package_prefix(self):
        assert qname(Path("/repo/Main.java"), "Main") == "Main"

    def test_anonymous_class_marker_survives(self):
        assert (
            qname(DOG, "new Comparator() {...}", NodeType.CLASS, [("Services", NodeType.CLASS)])
            == "src.main.java.core.Services.new Comparator() {...}"
        )


class TestCleanSymbolName:
    def test_generics_are_stripped_from_parameters(self):
        assert JavaAdapter._clean_symbol_name("add(List<Animal>)") == "add(List)"

    def test_multiple_parameters_keep_their_order_and_spacing(self):
        assert JavaAdapter._clean_symbol_name("create(String,String,int)") == "create(String, String, int)"

    def test_nested_generics_are_stripped_whole(self):
        assert JavaAdapter._clean_symbol_name("of(Map<String, List<Integer>>)") == "of(Map)"

    def test_a_declarations_own_type_parameters_are_dropped(self):
        assert JavaAdapter._clean_symbol_name("map(Function) <R>") == "map(Function)"

    def test_a_name_without_parentheses_is_returned_unchanged(self):
        assert JavaAdapter._clean_symbol_name("Dog") == "Dog"

    def test_an_empty_parameter_list_stays_empty(self):
        assert JavaAdapter._clean_symbol_name("speak()") == "speak()"


class TestExtractPackage:
    def test_maven_layout(self):
        assert JavaAdapter().extract_package("src.main.java.core.Dog.speak()") == "core"

    def test_nested_package(self):
        assert JavaAdapter().extract_package("src.main.java.com.example.util.Helper") == "com.example.util"

    def test_package_for_file_matches_the_directory(self):
        assert JavaAdapter().get_package_for_file(DOG, ROOT) == "core"

    def test_package_for_file_is_unaffected_by_the_type_name(self):
        """It builds a name with an empty symbol, so the stem must not leak in."""
        adapter = JavaAdapter()
        assert adapter.get_package_for_file(Path("/repo/src/main/java/com/example/Service.java"), ROOT) == "com.example"


class TestJavaDeclaredPackage:
    """The package, not the Maven directory, is the name the compiler uses."""

    def setup_method(self):
        self.adapter = JavaAdapter()
        self.root = Path("/repo")

    def _declare(self, source: Path, package: str) -> str:
        return self.adapter.build_qualified_name(
            file_path=source,
            symbol_name=package,
            symbol_kind=NodeType.PACKAGE,
            parent_chain=[],
            project_root=self.root,
        )

    def test_the_package_symbol_is_not_doubled(self):
        source = Path("/repo/src/main/java/core/Animal.java")
        assert self._declare(source, "core") == "core"

    def test_the_build_layout_leaves_the_names(self):
        source = Path("/repo/src/main/java/core/Animal.java")
        self._declare(source, "core")
        result = self.adapter.build_qualified_name(
            file_path=source,
            symbol_name="Animal",
            symbol_kind=NodeType.CLASS,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "core.Animal"

    def test_a_file_with_no_package_symbol_falls_back_to_the_directory(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/src/main/java/core/Animal.java"),
            symbol_name="Animal",
            symbol_kind=NodeType.CLASS,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "src.main.java.core.Animal"

    def test_a_package_derived_name_yields_the_whole_package(self):
        """Names no longer carry a src/main/java marker, so extraction cannot key on one."""
        assert self.adapter.extract_package("org.mockito.internal.util.MockUtil.isMock(Object)") == (
            "org.mockito.internal.util"
        )
        assert self.adapter.extract_package("core.Animal") == "core"

    def test_a_directory_derived_name_still_strips_the_build_layout(self):
        assert self.adapter.extract_package("src.main.java.core.Animal.speak()") == "core"
