"""The Java adapter: symbol names, packages, what the build import hid, and which server errors end a run."""

from pathlib import Path
from unittest.mock import patch

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
        "\tat java.base/java.util.concurrent.CompletableFuture.encodeThrowable(CompletableFuture.java:315)\n"
        'Caused by: java.lang.NullPointerException: Cannot read field "type" because "argument.binding" is null\n'
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
        assert "SelectionEngine.java:1473" in caplog.text

    def test_a_selection_parser_crash_leaves_one_edge_out(self):
        """crate: `return new Iterator<>() {` inside another anonymous class crashed JDT's parser recovery, every run."""
        error = {
            "code": -32603,
            "message": "Internal error.",
            "data": (
                "java.util.concurrent.CompletionException: java.lang.ArrayStoreException: arraycopy: element type "
                "mismatch\n"
                "\tat java.base/java.util.concurrent.CompletableFuture.encodeThrowable(CompletableFuture.java:315)\n"
                "Caused by: java.lang.ArrayStoreException: arraycopy: element type mismatch\n"
                "\tat java.base/java.lang.System.arraycopy(Native Method)\n"
                "\tat org.eclipse.jdt.internal.codeassist.select.SelectionParser.buildMoreCompletionContext"
                "(SelectionParser.java:195)"
            ),
        }
        assert JavaAdapter().error_verdict(error) is ErrorVerdict.DECLINED

    def test_a_compiler_crash_during_code_selection_leaves_one_edge_out(self):
        """crate: resolving a switch expression whose arms are `MutableLong::new`, a lambda and a throw."""
        error = {
            "code": -32603,
            "data": (
                "Caused by: java.lang.NullPointerException: Cannot invoke "
                '"org.eclipse.jdt.internal.compiler.lookup.TypeBinding.unboxedType()" because "rxpressionType" is null\n'
                "\tat org.eclipse.jdt.internal.compiler.ast.SwitchExpression$Results.add(SwitchExpression.java:139)\n"
                "\tat org.eclipse.jdt.internal.compiler.ast.YieldStatement.resolve(YieldStatement.java:157)\n"
                "\tat org.eclipse.jdt.internal.codeassist.SelectionEngine.select(SelectionEngine.java:1063)"
            ),
        }
        assert JavaAdapter().error_verdict(error) is ErrorVerdict.DECLINED

    def test_a_jvm_error_inside_code_selection_still_fails_the_run(self):
        """An error, unlike an exception, says the server itself is in trouble, wherever it surfaced."""
        error = {
            "code": -32603,
            "data": (
                "Caused by: java.lang.OutOfMemoryError: Java heap space\n"
                "\tat org.eclipse.jdt.internal.codeassist.SelectionEngine.select(SelectionEngine.java:1030)"
            ),
        }
        assert JavaAdapter().error_verdict(error) is ErrorVerdict.FAILURE

    def test_a_cancelled_request_keeps_its_retry(self):
        """Declining it would read a cancellation as an empty answer before the protocol's retry could ask again."""
        cancelled = {**SELECTION_CRASH, "code": -32800, "message": "Request cancelled."}
        assert JavaAdapter().error_verdict(cancelled) is ErrorVerdict.FAILURE

    def test_an_interrupted_code_selection_still_fails_the_run(self):
        """Interruption says what happened to the thread, not what the source is."""
        error = {
            "code": -32603,
            "data": (
                "Caused by: java.lang.InterruptedException\n"
                "\tat org.eclipse.jdt.internal.codeassist.SelectionEngine.select(SelectionEngine.java:1030)"
            ),
        }
        assert JavaAdapter().error_verdict(error) is ErrorVerdict.FAILURE

    def test_any_other_internal_error_still_fails_the_run(self):
        """Reading an unknown failure as empty would lose edges silently."""
        error = {"code": -32603, "message": "Internal error.", "data": "java.lang.OutOfMemoryError: Java heap space"}
        assert JavaAdapter().error_verdict(error) is ErrorVerdict.FAILURE

    def test_a_null_pointer_outside_code_selection_still_fails_the_run(self):
        error = {"code": -32603, "data": "java.lang.NullPointerException\n\tat org.eclipse.jdt.ls.core.Handler.run"}
        assert JavaAdapter().error_verdict(error) is ErrorVerdict.FAILURE

    def test_a_null_pointer_merely_passing_through_code_selection_still_fails_the_run(self):
        """Only the frame that threw says the crash is the compiler's."""
        error = {
            "code": -32603,
            "data": (
                "Caused by: java.lang.NullPointerException\n"
                "\tat org.eclipse.jdt.ls.core.Handler.run(Handler.java:1)\n"
                "\tat org.eclipse.jdt.internal.codeassist.SelectionEngine.select(SelectionEngine.java:1030)"
            ),
        }
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


FILTERED_CLASSPATH = b"""<classpath>
\t<classpathentry including="**/*Rules.java" kind="src" output="target/classes" path="src/main/java">
\t\t<attributes><attribute name="maven.pomderived" value="true"/></attributes>
\t</classpathentry>
\t<classpathentry excluding="**" kind="src" output="target/classes" path="src/main/resources"/>
\t<classpathentry including="**/*.jar" kind="lib" path="lib"/>
</classpath>
"""


class TestImportFilters:
    def test_a_source_folder_filter_is_dropped_in_every_module(self, tmp_path: Path) -> None:
        modules = [tmp_path / "core", tmp_path / "group" / "utils"]
        for module in modules:
            module.mkdir(parents=True)
            (module / ".classpath").write_bytes(FILTERED_CLASSPATH)

        assert JavaAdapter().lift_import_filters(tmp_path) is True

        for module in modules:
            lifted = (module / ".classpath").read_bytes()
            assert b'<classpathentry kind="src" output="target/classes" path="src/main/java">' in lifted

    def test_only_the_inclusion_filter_on_a_source_folder_goes(self, tmp_path: Path) -> None:
        """A resource folder's ``excluding="**"`` and a library's own filter mean something else."""
        (tmp_path / ".classpath").write_bytes(FILTERED_CLASSPATH)

        JavaAdapter().lift_import_filters(tmp_path)

        lifted = (tmp_path / ".classpath").read_bytes()
        assert b'excluding="**" kind="src"' in lifted
        assert b'including="**/*.jar" kind="lib"' in lifted

    def test_a_project_without_filters_is_left_alone(self, tmp_path: Path) -> None:
        """False is what spares every other repository a server restart."""
        unfiltered = b'<classpath><classpathentry kind="src" path="src/main/java"/></classpath>'
        (tmp_path / ".classpath").write_bytes(unfiltered)

        assert JavaAdapter().lift_import_filters(tmp_path) is False
        assert (tmp_path / ".classpath").read_bytes() == unfiltered

    def test_a_hand_written_filter_is_left_alone(self, tmp_path: Path) -> None:
        """Without ``maven.pomderived`` the filter is the project's own source set, not an m2e artifact."""
        hand_written = FILTERED_CLASSPATH.replace(b'"maven.pomderived"', b'"optional"')
        (tmp_path / ".classpath").write_bytes(hand_written)

        assert JavaAdapter().lift_import_filters(tmp_path) is False
        assert (tmp_path / ".classpath").read_bytes() == hand_written

    def test_the_session_puts_back_the_imported_file(self, tmp_path: Path) -> None:
        """A repository that commits its ``.classpath`` must not be left modified by an analysis."""
        (tmp_path / ".classpath").write_bytes(FILTERED_CLASSPATH)
        adapter = JavaAdapter()

        with adapter.session():
            adapter.lift_import_filters(tmp_path)

        assert (tmp_path / ".classpath").read_bytes() == FILTERED_CLASSPATH

    def test_an_edit_made_during_the_analysis_is_not_overwritten(self, tmp_path: Path) -> None:
        classpath = tmp_path / ".classpath"
        classpath.write_bytes(FILTERED_CLASSPATH)
        adapter = JavaAdapter()

        with adapter.session():
            adapter.lift_import_filters(tmp_path)
            classpath.write_bytes(b"<classpath>edited since</classpath>")

        assert classpath.read_bytes() == b"<classpath>edited since</classpath>"

    def test_one_file_that_cannot_be_put_back_does_not_stop_the_others(self, tmp_path: Path, caplog) -> None:
        classpaths = [tmp_path / module / ".classpath" for module in ("core", "utils")]
        for classpath in classpaths:
            classpath.parent.mkdir()
            classpath.write_bytes(FILTERED_CLASSPATH)
        adapter = JavaAdapter()
        write_bytes = Path.write_bytes
        refused: list[Path] = []

        def refuse_the_first_put_back(path: Path, data: bytes) -> int:
            if data == FILTERED_CLASSPATH and not refused:
                refused.append(path)
                raise OSError("Read-only file system")
            return write_bytes(path, data)

        with patch.object(Path, "write_bytes", refuse_the_first_put_back), adapter.session():
            adapter.lift_import_filters(tmp_path)

        [other] = [classpath for classpath in classpaths if classpath not in refused]
        assert other.read_bytes() == FILTERED_CLASSPATH
        assert "Could not put back" in caplog.text

    def test_a_second_import_does_not_replace_what_is_restored(self, tmp_path: Path) -> None:
        """Engines over nested projects import the same module in turn."""
        classpath = tmp_path / ".classpath"
        classpath.write_bytes(FILTERED_CLASSPATH)
        adapter = JavaAdapter()

        with adapter.session():
            adapter.lift_import_filters(tmp_path)
            classpath.write_bytes(FILTERED_CLASSPATH.replace(b"Rules", b"Other"))
            adapter.lift_import_filters(tmp_path)

        assert classpath.read_bytes() == FILTERED_CLASSPATH
