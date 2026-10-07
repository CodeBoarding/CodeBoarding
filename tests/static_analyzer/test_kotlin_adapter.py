"""Naming, launch and readiness for the Kotlin adapter."""

import json
import threading
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.config import Language, NodeType
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters import kotlin_adapter
from static_analyzer.engine.adapters.kotlin_adapter import KotlinAdapter
from static_analyzer.engine.lsp_client import ErrorVerdict
from static_analyzer.engine.source_inspector import SourceInspector

ROOT = Path("/repo")


def _symbol(name: str, kind: int, line: int, char: int, children: list[dict] | None = None) -> dict:
    position = {"line": line, "character": char}
    return {
        "name": name,
        "kind": int(kind),
        "range": {"start": position, "end": position},
        "selectionRange": {"start": position, "end": position},
        "children": children or [],
    }


def _client(phase: str | None) -> MagicMock:
    """A client whose server has reported *phase* for its build import, or nothing yet."""
    client = MagicMock()
    client.import_finished = threading.Event()
    client.import_phase = phase or ""
    client.import_failed = False
    client.import_folder_statuses = []
    client.wait_for_progress_quiet.return_value = True
    if phase is not None:
        client.import_finished.set()
    return client


class TestProperties:
    def test_identity(self):
        adapter = KotlinAdapter()
        assert adapter.language == "Kotlin"
        assert adapter.language_enum is Language.KOTLIN
        assert adapter.language_id == "kotlin"
        assert adapter.lsp_command == ["intellij-server"]

    def test_build_scripts_are_not_sources(self):
        assert KotlinAdapter().file_extensions == (".kt",)

    def test_registry_returns_kotlin_adapter(self):
        assert isinstance(get_adapter("Kotlin"), KotlinAdapter)

    def test_dispatch_comes_from_the_server_and_bases_from_definitions(self):
        """The server answers ``implementation`` but offers no type hierarchy, and answers a constructor call with its class."""
        adapter = KotlinAdapter()
        assert not adapter.expands_virtual_dispatch
        assert adapter.resolves_bases_by_definition
        assert adapter.constructor_calls_resolve_to_class
        assert not adapter.resolves_method_groups
        assert adapter.get_workspace_settings() is None

    def test_an_internal_error_is_asked_again(self):
        error = {"code": -32603, "message": "Internal error."}
        assert KotlinAdapter().error_verdict(error) is ErrorVerdict.TRANSIENT


class TestBuildQualifiedName:
    def test_a_type_named_after_its_file_takes_the_stems_place(self):
        adapter = KotlinAdapter()
        owner = _symbol("Owner", NodeType.CLASS, 0, 6, [_symbol("addPet(Pet)", NodeType.FUNCTION, 1, 8)])
        adapter.record_document_symbols(
            ROOT / "owner/Owner.kt", [owner, _symbol("helper()", NodeType.FUNCTION, 5, 4)], ROOT
        )

        assert adapter.build_qualified_name(ROOT / "owner/Owner.kt", "Owner", NodeType.CLASS, [], ROOT) == "owner.Owner"
        member = adapter.build_qualified_name(
            ROOT / "owner/Owner.kt", "addPet(Pet)", NodeType.FUNCTION, [("Owner", NodeType.CLASS)], ROOT
        )
        assert member == "owner.Owner.addPet(Pet)"
        top_level = adapter.build_qualified_name(ROOT / "owner/Owner.kt", "helper()", NodeType.FUNCTION, [], ROOT)
        assert top_level == "owner.Owner.helper()"

    def test_the_stem_stays_when_folding_would_give_a_member_and_a_top_level_function_one_name(self):
        adapter = KotlinAdapter()
        owner = _symbol("Owner", NodeType.CLASS, 0, 6, [_symbol("helper()", NodeType.FUNCTION, 1, 8)])
        adapter.record_document_symbols(
            ROOT / "owner/Owner.kt", [owner, _symbol("helper()", NodeType.FUNCTION, 5, 4)], ROOT
        )

        member = adapter.build_qualified_name(
            ROOT / "owner/Owner.kt", "helper()", NodeType.FUNCTION, [("Owner", NodeType.CLASS)], ROOT
        )
        top_level = adapter.build_qualified_name(ROOT / "owner/Owner.kt", "helper()", NodeType.FUNCTION, [], ROOT)
        assert member == "owner.Owner.Owner.helper()"
        assert top_level == "owner.Owner.helper()"

    def test_a_file_of_functions_keeps_its_stem(self):
        adapter = KotlinAdapter()
        adapter.record_document_symbols(
            ROOT / "util/Strings.kt", [_symbol("shout(String)", NodeType.FUNCTION, 0, 4)], ROOT
        )
        name = adapter.build_qualified_name(ROOT / "util/Strings.kt", "shout(String)", NodeType.FUNCTION, [], ROOT)
        assert name == "util.Strings.shout(String)"

    def test_a_file_rerecorded_without_its_type_keeps_its_stem_again(self):
        adapter = KotlinAdapter()
        adapter.record_document_symbols(ROOT / "a/Dog.kt", [_symbol("Dog", NodeType.CLASS, 0, 6)], ROOT)
        adapter.record_document_symbols(ROOT / "a/Dog.kt", [_symbol("Cat", NodeType.CLASS, 0, 6)], ROOT)
        assert adapter.build_qualified_name(ROOT / "a/Dog.kt", "Cat", NodeType.CLASS, [], ROOT) == "a.Dog.Cat"

    def test_packages_are_directories(self):
        adapter = KotlinAdapter()
        files = [ROOT / "app/src/main/kotlin/a/A.kt", ROOT / "app/src/main/kotlin/b/B.kt"]
        assert adapter.get_package_for_file(files[0], ROOT) == "app.src.main.kotlin.a"
        assert adapter.get_all_packages(files, ROOT) == {"app.src.main.kotlin.a", "app.src.main.kotlin.b"}


class TestRefineDocumentSymbols:
    def test_overloads_constructors_and_kinds_are_told_apart(self, tmp_path: Path):
        source = tmp_path / "Shapes.kt"
        source.write_text(
            "interface Shape\n"
            "enum class Mode { ON, OFF }\n"
            "class Dog(val name: String) {\n"
            '    constructor() : this("Rex")\n'
            "    fun fetch(item: String) = item\n"
            "    fun fetch(item: String, times: Int) = item\n"
            "}\n"
            "fun List<Dog>.loudest(): Dog? = null\n"
        )
        symbols = [
            _symbol("Shape", NodeType.CLASS, 0, 10),
            _symbol("Mode", NodeType.CLASS, 1, 11, [_symbol("ON", NodeType.CLASS, 1, 18)]),
            _symbol(
                "Dog",
                NodeType.CLASS,
                2,
                6,
                [
                    _symbol("Dog", NodeType.CONSTRUCTOR, 2, 9),
                    _symbol("Dog", NodeType.CONSTRUCTOR, 3, 4),
                    _symbol("fetch", NodeType.FUNCTION, 4, 8),
                    _symbol("fetch", NodeType.FUNCTION, 5, 8),
                ],
            ),
            _symbol("loudest", NodeType.FUNCTION, 7, 14),
        ]

        refined = KotlinAdapter().refine_document_symbols(source, symbols, SourceInspector())

        assert [(s["name"], s["kind"]) for s in refined] == [
            ("Shape", NodeType.INTERFACE),
            ("Mode", NodeType.ENUM),
            ("Dog", NodeType.CLASS),
            ("loudest(List)", NodeType.FUNCTION),
        ]
        assert refined[1]["children"][0]["kind"] == NodeType.ENUM_MEMBER
        assert [child["name"] for child in refined[2]["children"]] == [
            "Dog(String)",
            "Dog()",
            "fetch(String)",
            "fetch(String, Int)",
        ]
        assert symbols[2]["children"][2]["name"] == "fetch", "the server's answer is not mutated"

    def test_a_classs_constants_and_constructor_values_are_properties(self, tmp_path: Path):
        source = tmp_path / "Shelter.kt"
        source.write_text(
            "val LIMIT = 1\nclass Shelter(val capacity: Int) {\n    companion object {\n        const val MAX = 3\n    }\n}\n"
        )
        symbols = [
            _symbol("LIMIT", NodeType.VARIABLE, 0, 4),
            _symbol(
                "Shelter",
                NodeType.CLASS,
                1,
                6,
                [
                    _symbol("capacity", NodeType.VARIABLE, 1, 18),
                    _symbol("Companion", NodeType.OBJECT, 2, 4, [_symbol("MAX", NodeType.CONSTANT, 3, 18)]),
                ],
            ),
        ]

        refined = KotlinAdapter().refine_document_symbols(source, symbols, SourceInspector())

        assert refined[0]["kind"] == NodeType.VARIABLE, "a top-level value stays as the server named it"
        shelter = refined[1]["children"]
        assert shelter[0]["kind"] == NodeType.PROPERTY
        assert shelter[1]["children"][0]["kind"] == NodeType.PROPERTY

    def test_objects_and_companions_are_classes(self, tmp_path: Path):
        source = tmp_path / "Registry.kt"
        source.write_text("object Registry {\n    fun all() = 1\n}\n")
        symbols = [_symbol("Registry", NodeType.OBJECT, 0, 7, [_symbol("all", NodeType.METHOD, 1, 8)])]

        refined = KotlinAdapter().refine_document_symbols(source, symbols, SourceInspector())

        assert refined[0]["kind"] == NodeType.CLASS
        assert refined[0]["children"][0]["name"] == "all()"


class TestLaunch:
    def test_the_server_runs_over_stdio_with_caches_kept_per_project(self, tmp_path: Path):
        with (
            patch("static_analyzer.engine.adapters.kotlin_adapter.shutil.which", return_value="/s/bin/intellij-server"),
            patch("static_analyzer.engine.adapters.kotlin_adapter.user_data_dir", return_value=tmp_path),
        ):
            command = KotlinAdapter().get_lsp_command(ROOT / "app")
            again = KotlinAdapter().get_lsp_command(ROOT / "app")
            other = KotlinAdapter().get_lsp_command(ROOT / "lib")

        assert command[1] == "--stdio"
        system = command[2].removeprefix("--system-path=")
        assert Path(system).parent == tmp_path / "kotlin-lsp"
        assert Path(system).name.startswith("app-")
        assert command == again
        assert other[2] != command[2]

    def test_a_missing_server_fails_fast(self):
        with patch("static_analyzer.engine.adapters.kotlin_adapter.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="run codeboarding-setup"):
                KotlinAdapter().get_lsp_command(ROOT)

    def test_the_didopen_drain_probe_is_asked_in_rounds(self):
        assert KotlinAdapter().drain_probe_round_seconds == 60

    def test_requests_are_bounded_apart_from_the_import(self):
        assert KotlinAdapter().get_lsp_default_timeout() == 120

    def test_the_heap_reaches_the_launcher_through_its_options(self, tmp_path: Path):
        source = tmp_path / "Big.kt"
        source.write_bytes(b" " * 2_000_000)
        with patch("static_analyzer.engine.adapters.kotlin_adapter.total_ram_gb", return_value=None):
            assert KotlinAdapter().get_lsp_env(tmp_path, [source]) == {"IJ_JAVA_OPTIONS": "-Xmx7G"}

    def test_the_heap_grows_with_the_analysed_sources_within_half_the_memory(self, tmp_path: Path):
        small, large = tmp_path / "Small.kt", tmp_path / "Large.kt"
        small.write_text("")
        large.write_bytes(b" " * 4_000_000)
        with patch("static_analyzer.engine.adapters.kotlin_adapter.total_ram_gb", return_value=None):
            assert kotlin_adapter._heap_size([small]) == "4G"
            assert kotlin_adapter._heap_size([small, large]) == "10G"
        with patch("static_analyzer.engine.adapters.kotlin_adapter.total_ram_gb", return_value=16.0):
            assert kotlin_adapter._heap_size([large]) == "8G"
        with patch("static_analyzer.engine.adapters.kotlin_adapter.total_ram_gb", return_value=2.0):
            assert kotlin_adapter._heap_size([small]) == "2G"


class TestImport:
    def test_analysis_waits_for_the_build_import(self):
        assert KotlinAdapter().wait_for_workspace_ready
        KotlinAdapter().validate_workspace_ready(_client("FINISHED"))

    def test_a_failed_import_still_analyses_the_sources(self, caplog: pytest.LogCaptureFixture):
        KotlinAdapter().validate_workspace_ready(_client("FAILED"))
        assert "could not import the build" in caplog.text

    def test_an_index_still_building_at_the_limit_fails(self):
        """Why: an incomplete index answers definitions with nothing, which would read as missing edges."""
        client = _client("FINISHED")
        client.wait_for_progress_quiet.return_value = False
        with pytest.raises(RuntimeError, match="still indexing"):
            KotlinAdapter().validate_workspace_ready(client)

    def test_an_import_that_never_ends_fails(self):
        with patch("static_analyzer.engine.adapters.kotlin_adapter._IMPORT_TIMEOUT_SECONDS", 0.01):
            with pytest.raises(RuntimeError, match="did not finish importing"):
                KotlinAdapter().validate_workspace_ready(_client(None))


class TestSourcesFallback:
    def _project(self, tmp_path: Path) -> tuple[Path, list[Path]]:
        app = tmp_path / "app" / "src" / "main" / "kotlin" / "com" / "acme"
        app.mkdir(parents=True)
        (app / "App.kt").write_text("package com.acme\n\nclass App\n")
        loose = tmp_path / "scripts"
        loose.mkdir()
        (loose / "Tool.kt").write_text("package tools.misc\n\nfun tool() = 1\n")
        return tmp_path, [app / "App.kt", loose / "Tool.kt"]

    def test_an_imported_build_needs_no_fallback(self, tmp_path: Path):
        root, files = self._project(tmp_path)
        client = _client("FINISHED")
        client.import_failed = False
        assert KotlinAdapter().fallback_workspace_folders(client, root, files) == []

    def test_a_project_with_no_build_falls_back_too(self, tmp_path: Path):
        root, files = self._project(tmp_path)
        client = _client("FINISHED")
        client.import_folder_statuses = ["BLOCKED"]
        with (
            patch("static_analyzer.engine.adapters.kotlin_adapter.user_data_dir", return_value=tmp_path / "data"),
            patch.object(KotlinAdapter, "_launcher", return_value=tmp_path / "kotlin-lsp" / "bin" / "intellij-server"),
        ):
            assert KotlinAdapter().fallback_workspace_folders(client, root, files)

    def test_a_failed_import_falls_back_to_a_workspace_of_source_roots(self, tmp_path: Path):
        root, files = self._project(tmp_path)
        client = _client("FINISHED")
        client.import_failed = True
        with (
            patch("static_analyzer.engine.adapters.kotlin_adapter.user_data_dir", return_value=tmp_path / "data"),
            patch.object(KotlinAdapter, "_launcher", return_value=tmp_path / "kotlin-lsp" / "bin" / "intellij-server"),
        ):
            folders = KotlinAdapter().fallback_workspace_folders(client, root, files)

        assert len(folders) == 1 and folders[0].is_relative_to(tmp_path / "data" / "kotlin-lsp")
        workspace = json.loads((folders[0] / "workspace.json").read_text())
        (module,) = workspace["modules"]
        assert {"type": "moduleSource"} in module["dependencies"]
        (content,) = module["contentRoots"]
        assert content["path"] == str(root)
        assert sorted(source["path"] for source in content["sourceRoots"]) == sorted(
            [str(root / "app" / "src" / "main" / "kotlin"), str(root / "scripts")]
        ), "a package root where the path spells the package, else the file's own directory"

    def test_the_fallback_workspace_depends_on_the_servers_standard_library(self, tmp_path: Path):
        root, files = self._project(tmp_path)
        install = tmp_path / "kotlin-lsp"
        (install / "bin").mkdir(parents=True)
        (install / "lib").mkdir()
        launcher = install / "bin" / "intellij-server"
        launcher.write_text("")
        with zipfile.ZipFile(install / "lib" / "util-8.jar", "w") as jar:
            jar.writestr("META-INF/kotlin-stdlib.kotlin_module", "")
            jar.writestr("kotlin/collections/CollectionsKt.class", "")
            jar.writestr("com/intellij/util/Unrelated.class", "")
        adapter = KotlinAdapter()
        client = _client("FAILED")
        with (
            patch("static_analyzer.engine.adapters.kotlin_adapter.shutil.which", return_value=str(launcher)),
            patch("static_analyzer.engine.adapters.kotlin_adapter.user_data_dir", return_value=tmp_path / "data"),
        ):
            (folder,) = adapter.fallback_workspace_folders(client, root, files)

        workspace = json.loads((folder / "workspace.json").read_text())
        (library,) = workspace["libraries"]
        assert {"type": "library", "name": library["name"], "scope": "compile"} in workspace["modules"][0][
            "dependencies"
        ]
        with zipfile.ZipFile(library["roots"][0]["path"]) as copied:
            assert sorted(copied.namelist()) == [
                "META-INF/kotlin-stdlib.kotlin_module",
                "kotlin/collections/CollectionsKt.class",
            ]

    def test_without_a_standard_library_the_workspace_still_names_the_sources(self, tmp_path: Path):
        root, files = self._project(tmp_path)
        client = _client("FAILED")
        launcher = tmp_path / "bare" / "bin" / "intellij-server"
        with (
            patch("static_analyzer.engine.adapters.kotlin_adapter.user_data_dir", return_value=tmp_path / "data"),
            patch.object(KotlinAdapter, "_launcher", return_value=launcher),
        ):
            (folder,) = KotlinAdapter().fallback_workspace_folders(client, root, files)

        workspace = json.loads((folder / "workspace.json").read_text())
        assert workspace["libraries"] == []
        assert workspace["modules"][0]["contentRoots"][0]["sourceRoots"]

    def test_a_multiplatform_project_starts_on_its_sources(self, tmp_path: Path):
        common = tmp_path / "lib" / "src" / "commonMain" / "kotlin" / "com" / "acme"
        common.mkdir(parents=True)
        (common / "Api.kt").write_text("package com.acme\n\nexpect fun api(): Int\n")
        launcher = tmp_path / "bare" / "bin" / "intellij-server"
        with (
            patch("static_analyzer.engine.adapters.kotlin_adapter.user_data_dir", return_value=tmp_path / "data"),
            patch.object(KotlinAdapter, "_launcher", return_value=launcher),
        ):
            (folder,) = KotlinAdapter().workspace_folders(tmp_path, [common / "Api.kt"])

        workspace = json.loads((folder / "workspace.json").read_text())
        (content,) = workspace["modules"][0]["contentRoots"]
        assert [root["path"] for root in content["sourceRoots"]] == [
            str(tmp_path / "lib" / "src" / "commonMain" / "kotlin")
        ]

    def test_a_jvm_project_starts_on_its_build(self, tmp_path: Path):
        root, files = self._project(tmp_path)
        assert KotlinAdapter().workspace_folders(root, files) == []

    def test_the_sources_workspace_names_the_java_roots_too(self, tmp_path: Path):
        """Why: Kotlin code calls the repository's Java, which resolves only from a source root."""
        root, files = self._project(tmp_path)
        java = tmp_path / "lib" / "src" / "main" / "java" / "com" / "acme"
        java.mkdir(parents=True)
        (java / "Store.java").write_text("package com.acme;\n\npublic class Store {}\n")
        built = tmp_path / "lib" / "build" / "generated" / "com" / "acme"
        built.mkdir(parents=True)
        (built / "Generated.java").write_text("package com.acme;\n\nclass Generated {}\n")
        launcher = tmp_path / "bare" / "bin" / "intellij-server"
        with (
            patch("static_analyzer.engine.adapters.kotlin_adapter.user_data_dir", return_value=tmp_path / "data"),
            patch.object(KotlinAdapter, "_launcher", return_value=launcher),
        ):
            (folder,) = KotlinAdapter().fallback_workspace_folders(_client("FAILED"), root, files)

        (content,) = json.loads((folder / "workspace.json").read_text())["modules"][0]["contentRoots"]
        roots = {source["path"] for source in content["sourceRoots"]}
        assert str(tmp_path / "lib" / "src" / "main" / "java") in roots
        assert not any("build" in Path(path).parts for path in roots)
