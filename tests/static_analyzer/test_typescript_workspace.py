"""Workspace members as the package manager declares them, and the tsserver plugin that resolves calls into them."""

import json
import subprocess
from pathlib import Path

import pytest

from infra.tool_registry.paths import get_servers_dir, preferred_node_path
from static_analyzer.engine.adapters.typescript_adapter import TypeScriptAdapter
from static_analyzer.exceptions import WorkspaceSetupError
from static_analyzer.typescript_workspace import PLUGIN_LOCATION, PLUGIN_NAME, workspace_members

NODE = preferred_node_path(get_servers_dir())
TSSERVER = get_servers_dir() / "node_modules" / "typescript" / "lib" / "tsserver.js"


class TestWorkspaceMembers:
    def test_npm_and_yarn_list_the_patterns(self, tmp_path: Path):
        _root(tmp_path, workspaces=["packages/*", "./tools/seed"])
        _package(tmp_path / "packages" / "core", "@acme/core")
        _package(tmp_path / "tools" / "seed", "@acme/seed")

        assert workspace_members(tmp_path) == {
            "@acme/core": tmp_path / "packages" / "core",
            "@acme/seed": tmp_path / "tools" / "seed",
        }

    def test_yarn_object_form_names_them_under_packages(self, tmp_path: Path):
        _root(tmp_path, workspaces={"packages": ["packages/*"]})
        _package(tmp_path / "packages" / "core", "@acme/core")

        assert workspace_members(tmp_path) == {"@acme/core": tmp_path / "packages" / "core"}

    def test_pnpm_reads_its_workspace_file_and_not_the_manifests_workspaces(self, tmp_path: Path):
        _root(tmp_path, workspaces=["apps/*"])
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n  - '!packages/old'\n")
        _package(tmp_path / "apps" / "web", "web")
        _package(tmp_path / "packages" / "router", "router-utils")
        _package(tmp_path / "packages" / "old", "router-utils")

        assert workspace_members(tmp_path) == {"router-utils": tmp_path / "packages" / "router"}

    def test_braces_and_negated_globs_select_the_members(self, tmp_path: Path):
        _root(tmp_path, workspaces=["{apps,libs}/*", "!**/fixtures/**", "!libs/{legacy,old}"])
        for folder in ("apps/web", "libs/ui", "libs/legacy", "libs/old", "apps/fixtures/demo"):
            _package(tmp_path / folder, folder.replace("/", "-"))

        assert set(workspace_members(tmp_path)) == {"apps-web", "libs-ui"}

    def test_installed_copies_and_nameless_packages_are_no_members(self, tmp_path: Path):
        _root(tmp_path, workspaces=["packages/**"])
        _package(tmp_path / "packages" / "core", "@acme/core")
        _package(tmp_path / "packages" / "core" / "node_modules" / "zod", "zod")
        _package(tmp_path / "packages" / "nameless", None)

        assert workspace_members(tmp_path) == {"@acme/core": tmp_path / "packages" / "core"}

    def test_no_declaration_is_no_workspace(self, tmp_path: Path):
        assert workspace_members(tmp_path) == {}
        _root(tmp_path)
        assert workspace_members(tmp_path) == {}

    @pytest.mark.parametrize(
        "file,text",
        [
            ("package.json", "{ not json"),
            ("package.json", json.dumps({"workspaces": "packages/*"})),
            ("pnpm-workspace.yaml", "packages: [unclosed"),
            ("pnpm-workspace.yaml", "- packages/*\n"),
        ],
    )
    def test_a_declaration_that_cannot_be_read_fails(self, tmp_path: Path, file: str, text: str):
        """Why fail: a workspace read as none would leave every call into a member unresolved, and be cached."""
        (tmp_path / file).write_text(text)

        with pytest.raises(WorkspaceSetupError):
            workspace_members(tmp_path)


class TestAdapter:
    def test_the_members_are_handed_to_the_plugin_tsserver_loads(self, tmp_path: Path):
        _monorepo(tmp_path)
        adapter = TypeScriptAdapter()

        assert {"name": PLUGIN_NAME, "location": str(PLUGIN_LOCATION)} in adapter.get_lsp_init_options()["plugins"]
        [command] = adapter.get_lsp_commands(tmp_path)
        assert command["command"] == "_typescript.configurePlugin"
        assert command["arguments"][0] == PLUGIN_NAME
        assert command["arguments"][1]["members"]["@acme/core"] == str(tmp_path / "packages" / "core")

    def test_no_workspace_sends_no_command(self, tmp_path: Path):
        assert TypeScriptAdapter().get_lsp_commands(tmp_path) == []

    def test_a_missing_plugin_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _monorepo(tmp_path)
        monkeypatch.setattr("static_analyzer.engine.adapters.typescript_adapter.PLUGIN_LOCATION", tmp_path / "none")

        with pytest.raises(WorkspaceSetupError):
            TypeScriptAdapter().get_lsp_commands(tmp_path)

    @pytest.mark.parametrize(
        "changed,workspace,rebuilds",
        [
            ("packages/core/tsconfig.build.json", False, True),
            ("packages/js/jsconfig.json", False, True),
            ("pnpm-workspace.yaml", False, True),
            ("packages/core/package.json", True, True),
            ("package.json", False, False),
            ("packages/core/src/index.ts", True, False),
        ],
    )
    def test_a_warm_start_rebuilds_when_a_change_alters_how_imports_resolve(
        self, tmp_path: Path, changed: str, workspace: bool, rebuilds: bool
    ):
        if workspace:
            _monorepo(tmp_path)

        assert TypeScriptAdapter().rebuilds_for({tmp_path / changed}, tmp_path) is rebuilds


@pytest.mark.skipif(NODE is None or not TSSERVER.is_file(), reason="needs node and the installed typescript")
class TestPlugin:
    """tsserver itself, on a workspace neither installed nor built."""

    def test_a_declared_dependency_resolves_to_the_members_source(self, tmp_path: Path):
        repo = _monorepo(tmp_path)

        assert _definition(repo, repo / "packages/app/src/main.ts", "greet") == repo / "packages/core/src/index.ts"

    def test_an_exports_subpath_imported_undeclared_resolves_to_the_members_source(self, tmp_path: Path):
        repo = _monorepo(tmp_path)

        assert _definition(repo, repo / "packages/app/src/main.ts", "shout") == repo / "packages/text/src/shout.ts"

    def test_a_member_whose_sources_are_no_src_folder_resolves_through_its_tsconfig(self, tmp_path: Path):
        """``rootDir: lib``: only the redirect through the member's tsconfig maps ``dist`` back to its sources."""
        repo = _monorepo(tmp_path)
        lib = _package(repo / "packages" / "lib", "@acme/lib", main="dist/index.js", types="dist/index.d.ts")
        (lib / "lib").mkdir()
        (lib / "lib" / "index.ts").write_text("export function lift(): number {\n  return 1;\n}\n")
        compiler = {"outDir": "dist", "rootDir": "lib", "module": "nodenext", "moduleResolution": "nodenext"}
        (lib / "tsconfig.json").write_text(json.dumps({"compilerOptions": compiler, "include": ["lib"]}))
        caller = repo / "packages" / "app" / "src" / "lazy.ts"
        caller.write_text("const lib = await import(`@acme/lib`);\n\nlib.lift();\n")

        assert _definition(repo, caller, "lift") == lib / "lib/index.ts"

    def test_a_file_no_tsconfig_claims_resolves_to_the_members_source(self, tmp_path: Path):
        """``tools`` has no tsconfig: its files sit in an inferred project, which has no references of its own."""
        repo = _monorepo(tmp_path)
        seed = _package(repo / "packages" / "tools", "@acme/tools") / "seed.ts"
        seed.write_text('import { greet } from "@acme/core";\n\ngreet("seed");\n')

        assert _definition(repo, seed, "greet") == repo / "packages/core/src/index.ts"

    def test_a_bundled_entry_no_tsconfig_emits_maps_onto_its_source_under_src(self, tmp_path: Path):
        """The tsconfig's own emit is ``dist/src/index.*`` (no rootDir, ``test`` included); a bundler builds ``dist``."""
        repo = _monorepo(tmp_path)
        router = _package(repo / "packages" / "router", "@acme/router", main="dist/index.js", types="dist/index.d.ts")
        for file, text in {
            "src/index.ts": "export function route(path: string): string {\n  return path;\n}\n",
            "test/index.test.ts": "export const checked = true;\n",
        }.items():
            (router / file).parent.mkdir()
            (router / file).write_text(text)
        compiler = {"outDir": "dist", "module": "nodenext", "moduleResolution": "nodenext", "declaration": True}
        (router / "tsconfig.json").write_text(json.dumps({"compilerOptions": compiler, "include": ["src", "test"]}))
        caller = repo / "packages" / "app" / "src" / "routes.ts"
        caller.write_text('import { route } from "@acme/router";\n\nroute("/home");\n')

        assert _definition(repo, caller, "route") == router / "src/index.ts"

    def test_without_the_plugins_configuration_the_call_resolves_nowhere(self, tmp_path: Path):
        repo = _monorepo(tmp_path)

        assert _definition(repo, repo / "packages/app/src/main.ts", "greet", configure=False) is None


def _monorepo(root: Path) -> Path:
    """``app`` declares ``@acme/core`` and imports ``@acme/text/shout`` undeclared; both are built to a missing dist."""
    _root(root, workspaces=["packages/*"])
    compiler = {"outDir": "dist", "rootDir": "src", "module": "nodenext", "moduleResolution": "nodenext"}
    core = _package(root / "packages" / "core", "@acme/core", main="dist/index.js", types="dist/index.d.ts")
    text = _package(
        root / "packages" / "text",
        "@acme/text",
        type="module",
        exports={"./shout": {"types": "./dist/shout.d.ts", "default": "./dist/shout.js"}},
    )
    app = _package(root / "packages" / "app", "@acme/app", type="module", dependencies={"@acme/core": "*"})
    for package in (core, text, app):
        (package / "src").mkdir()
        (package / "tsconfig.json").write_text(json.dumps({"compilerOptions": compiler, "include": ["src"]}))
    (core / "src" / "index.ts").write_text("export function greet(name: string): string {\n  return name;\n}\n")
    (text / "src" / "shout.ts").write_text("export function shout(text: string): string {\n  return text;\n}\n")
    (app / "src" / "main.ts").write_text(
        'import { greet } from "@acme/core";\nimport { shout } from "@acme/text/shout";\n\ngreet(shout("x"));\n'
    )
    return root


def _definition(repo: Path, file: Path, name: str, configure: bool = True) -> Path | None:
    """The file tsserver's definition of the call to *name* in *file* lands in, outside *file*."""
    line_number, line = next(
        (index, text)
        for index, text in enumerate(file.read_text().splitlines(), 1)
        if "(" in text and name in text and "import" not in text
    )
    requests: list[dict] = []
    if configure:
        [command] = TypeScriptAdapter().get_lsp_commands(repo)
        plugin, configuration = command["arguments"]
        requests.append(
            {"command": "configurePlugin", "arguments": {"pluginName": plugin, "configuration": configuration}}
        )
    requests += [
        {"command": "open", "arguments": {"file": str(file)}},
        {
            "command": "definition",
            "arguments": {"file": str(file), "line": line_number, "offset": line.index(name) + 1},
        },
    ]
    numbered = [{"seq": seq, "type": "request", **request} for seq, request in enumerate(requests, 1)]
    output = subprocess.run(
        [str(NODE), str(TSSERVER), "--disableAutomaticTypingAcquisition"]
        + ["--globalPlugins", PLUGIN_NAME, "--pluginProbeLocations", str(PLUGIN_LOCATION)],
        input="".join(json.dumps(request) + "\n" for request in numbered),
        capture_output=True,
        text=True,
        env={"PATH": str(Path(str(NODE)).parent)},
        timeout=60,
    ).stdout
    answers = [json.loads(text) for text in output.splitlines() if text.startswith("{")]
    answer = next(message for message in answers if message.get("request_seq") == numbered[-1]["seq"])
    files = {Path(location["file"]) for location in answer.get("body") or []} - {file}
    return files.pop() if files else None


def _package(directory: Path, name: str | None, **manifest) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(json.dumps({"name": name, **manifest} if name else manifest))
    return directory


def _root(root: Path, **manifest) -> Path:
    return _package(root, "root", **manifest)
