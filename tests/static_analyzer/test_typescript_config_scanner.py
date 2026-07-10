"""Tests for TypeScript / JavaScript project configuration scanner."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from static_analyzer.typescript_config_scanner import (
    TypeScriptConfigScanner,
    TypeScriptProject,
    _resolve_system_tsc,
)
from utils import CODEBOARDING_DIR_NAME


def _force_fallback_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the scanner skip ``tsc --showConfig`` and use the FS-walk fallback.

    Why: unit tests shouldn't depend on a real ``tsc`` install. The fallback
    walk is the path actually exercised in test/CI environments without
    ``servers/`` provisioned.
    """
    monkeypatch.setattr(
        "static_analyzer.typescript_config_scanner._resolve_tsc_command",
        lambda *_a, **_k: None,
    )


class TestNoConfigFiles:
    def test_empty_repo_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _force_fallback_walk(monkeypatch)
        scanner = TypeScriptConfigScanner(tmp_path)
        assert scanner.find_typescript_projects() == []


class TestSinglePackage:
    def test_one_tsconfig_one_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _force_fallback_walk(monkeypatch)
        (tmp_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {}}))
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("export {};")

        scanner = TypeScriptConfigScanner(tmp_path)
        projects = scanner.find_typescript_projects()
        assert len(projects) == 1
        assert projects[0].root == tmp_path.resolve()
        assert (tmp_path / "src" / "index.ts").resolve() in projects[0].files


class TestSolutionTsconfigDropped:
    """A repo-root tsconfig with empty ``files`` and ``references`` pointing at
    leaf packages must NOT be returned as a project of its own. Otherwise its
    walk overlaps with the leaves, double-claiming files."""

    def test_solution_root_dropped_when_tsc_says_files_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Layout: solution at root, two leaf packages.
        (tmp_path / "tsconfig.json").write_text(
            json.dumps({"files": [], "references": [{"path": "./packages/a"}, {"path": "./packages/b"}]})
        )
        (tmp_path / "packages" / "a").mkdir(parents=True)
        (tmp_path / "packages" / "a" / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "packages" / "a" / "x.ts").write_text("export {};")
        (tmp_path / "packages" / "b").mkdir(parents=True)
        (tmp_path / "packages" / "b" / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "packages" / "b" / "y.ts").write_text("export {};")

        # Stub tsc to produce empty files for the solution and real files for leaves.
        def fake_run(cmd, **kwargs):
            project_dir = Path(cmd[cmd.index("-p") + 1]).resolve()
            payload: dict
            if project_dir == tmp_path.resolve():
                payload = {"files": []}
            elif project_dir.name == "a":
                payload = {"files": [str(project_dir / "x.ts")]}
            else:
                payload = {"files": [str(project_dir / "y.ts")]}

            class _R:
                returncode = 0
                stdout = json.dumps(payload)
                stderr = ""

            return _R()

        monkeypatch.setattr(
            "static_analyzer.typescript_config_scanner._resolve_tsc_command",
            lambda *_a, **_k: ["fake-tsc", "--showConfig"],
        )
        monkeypatch.setattr("static_analyzer.typescript_config_scanner.subprocess.run", fake_run)

        scanner = TypeScriptConfigScanner(tmp_path)
        projects = scanner.find_typescript_projects()
        roots = {p.root for p in projects}
        assert tmp_path.resolve() not in roots, "Solution tsconfig must be dropped"
        assert (tmp_path / "packages" / "a").resolve() in roots
        assert (tmp_path / "packages" / "b").resolve() in roots
        assert len(projects) == 2


def _stub_tsc(monkeypatch: pytest.MonkeyPatch, payload_for: Callable[[Path], dict]) -> None:
    """Make tsc invocations return ``payload_for(project_dir)`` as JSON."""

    def fake_run(cmd, **_kwargs):
        project_dir = Path(cmd[cmd.index("-p") + 1]).resolve()
        payload = payload_for(project_dir)

        class _R:
            returncode = 0
            stdout = json.dumps(payload)
            stderr = ""

        return _R()

    monkeypatch.setattr(
        "static_analyzer.typescript_config_scanner._resolve_tsc_command",
        lambda *_a, **_k: ["fake-tsc", "--showConfig"],
    )
    monkeypatch.setattr("static_analyzer.typescript_config_scanner.subprocess.run", fake_run)


class TestTrimOverlap:
    """The trim step assigns each file to the deepest project that claims it."""

    def test_partial_overlap_trims_parent_keeps_unique_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Parent claims ``root.ts`` AND ``child/c.ts``. Child claims ``c.ts``.
        After trim: parent keeps only ``root.ts``, child keeps ``c.ts``. Both
        survive — this is the Excalidraw ``excalidraw-app/`` scenario where
        the root tsconfig contributes files no leaf covers.
        """
        (tmp_path / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "child").mkdir()
        (tmp_path / "child" / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "child" / "c.ts").write_text("export {};")
        (tmp_path / "root.ts").write_text("export {};")

        def payload_for(project_dir: Path) -> dict:
            if project_dir == tmp_path.resolve():
                return {"files": [str(tmp_path / "root.ts"), str(tmp_path / "child" / "c.ts")]}
            return {"files": [str(project_dir / "c.ts")]}

        _stub_tsc(monkeypatch, payload_for)

        scanner = TypeScriptConfigScanner(tmp_path)
        projects = scanner.find_typescript_projects()
        roots_to_files = {p.root: {f.name for f in p.files} for p in projects}
        assert roots_to_files == {
            tmp_path.resolve(): {"root.ts"},
            (tmp_path / "child").resolve(): {"c.ts"},
        }

    def test_full_overlap_drops_parent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """When the parent's *entire* file set is also claimed by a child, the
        parent has nothing unique left after trim and is dropped — same effect
        as the previous "drop on overlap" rule, but achieved by emptiness.
        """
        (tmp_path / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "child").mkdir()
        (tmp_path / "child" / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "child" / "c.ts").write_text("export {};")

        def payload_for(project_dir: Path) -> dict:
            if project_dir == tmp_path.resolve():
                return {"files": [str(tmp_path / "child" / "c.ts")]}
            return {"files": [str(project_dir / "c.ts")]}

        _stub_tsc(monkeypatch, payload_for)

        scanner = TypeScriptConfigScanner(tmp_path)
        projects = scanner.find_typescript_projects()
        assert len(projects) == 1
        assert projects[0].root == (tmp_path / "child").resolve()

    def test_same_depth_alphabetical_tiebreak(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Two same-depth sibling projects claiming the same file: alphabetical
        order of the absolute path decides who keeps it.
        """
        # ``apple`` and ``banana`` are siblings; both claim ``shared.ts``.
        (tmp_path / "apple").mkdir()
        (tmp_path / "apple" / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "banana").mkdir()
        (tmp_path / "banana" / "tsconfig.json").write_text(json.dumps({}))
        shared = tmp_path / "shared.ts"
        shared.write_text("export {};")
        (tmp_path / "apple" / "a.ts").write_text("export {};")
        (tmp_path / "banana" / "b.ts").write_text("export {};")

        def payload_for(project_dir: Path) -> dict:
            if project_dir.name == "apple":
                return {"files": [str(project_dir / "a.ts"), str(shared)]}
            return {"files": [str(project_dir / "b.ts"), str(shared)]}

        _stub_tsc(monkeypatch, payload_for)

        scanner = TypeScriptConfigScanner(tmp_path)
        projects = scanner.find_typescript_projects()
        roots_to_files = {p.root: {f.name for f in p.files} for p in projects}
        # Alphabetical-first wins; ``apple`` < ``banana``.
        assert "shared.ts" in roots_to_files[(tmp_path / "apple").resolve()]
        assert "shared.ts" not in roots_to_files[(tmp_path / "banana").resolve()]
        # Both projects survive — each still has its unique file.
        assert "a.ts" in roots_to_files[(tmp_path / "apple").resolve()]
        assert "b.ts" in roots_to_files[(tmp_path / "banana").resolve()]


class TestTscResultsFiltered:
    def test_tsc_results_filtered_through_codeboardingignore(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Files tsc returns must still be filtered through ``.codeboardingignore``.
        Otherwise a permissive root tsconfig (no test exclusion) would re-introduce
        ``*.test.ts`` files into the analyzer that the user has explicitly asked
        CodeBoarding to skip — Option B already drops them; Option A must too.
        """
        (tmp_path / CODEBOARDING_DIR_NAME).mkdir()
        (tmp_path / CODEBOARDING_DIR_NAME / ".codeboardingignore").write_text("*.test.ts\n")
        (tmp_path / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "a.ts").write_text("export {};")
        (tmp_path / "a.test.ts").write_text("export {};")

        def payload_for(_project_dir: Path) -> dict:
            return {"files": [str(tmp_path / "a.ts"), str(tmp_path / "a.test.ts")]}

        _stub_tsc(monkeypatch, payload_for)

        scanner = TypeScriptConfigScanner(tmp_path)
        projects = scanner.find_typescript_projects()
        assert len(projects) == 1
        names = {f.name for f in projects[0].files}
        assert names == {"a.ts"}, "tsc-returned *.test.ts must be filtered by .codeboardingignore"


class TestFallbackWhenTscMissing:
    def test_fallback_walks_filesystem_for_each_tsconfig(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _force_fallback_walk(monkeypatch)

        (tmp_path / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "a.ts").write_text("export {};")
        (tmp_path / "b.tsx").write_text("export {};")

        scanner = TypeScriptConfigScanner(tmp_path)
        projects = scanner.find_typescript_projects()
        assert len(projects) == 1
        names = {f.name for f in projects[0].files}
        assert names == {"a.ts", "b.tsx"}

    def test_fallback_skips_ignored_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        _force_fallback_walk(monkeypatch)
        (tmp_path / CODEBOARDING_DIR_NAME).mkdir()
        (tmp_path / CODEBOARDING_DIR_NAME / ".codeboardingignore").write_text("*.spec.ts\n")
        (tmp_path / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "a.ts").write_text("export {};")
        (tmp_path / "a.spec.ts").write_text("export {};")

        scanner = TypeScriptConfigScanner(tmp_path)
        projects = scanner.find_typescript_projects()
        names = {f.name for f in projects[0].files}
        assert "a.ts" in names
        assert "a.spec.ts" not in names

    def test_fallback_walks_are_disjoint_across_nested_projects(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """When tsc is unavailable, each candidate's walk must exclude
        subtrees owned by other candidates. Otherwise the parent's walk
        re-claims files already owned by a child, re-introducing the
        duplicate-counting bug the module exists to fix.
        """
        _force_fallback_walk(monkeypatch)
        # Layout: root tsconfig + nested child tsconfig under packages/foo/.
        # Files: a "lone" file at the root that's only the parent's,
        # and a nested file that the child owns.
        (tmp_path / "tsconfig.json").write_text(json.dumps({}))
        (tmp_path / "lone.ts").write_text("export {};")
        nested = tmp_path / "packages" / "foo"
        nested.mkdir(parents=True)
        (nested / "tsconfig.json").write_text(json.dumps({}))
        (nested / "child.ts").write_text("export {};")

        scanner = TypeScriptConfigScanner(tmp_path)
        projects = scanner.find_typescript_projects()
        # After the disjoint walk + drop_overlapping_parents safety net:
        # the parent owns ``lone.ts`` (not ``child.ts``), the child owns
        # ``child.ts``. _drop_overlapping_parents would *not* drop the
        # parent because child.ts is no longer in its set, so both survive.
        roots_to_files = {p.root: {f.name for f in p.files} for p in projects}
        assert nested.resolve() in roots_to_files
        assert roots_to_files[nested.resolve()] == {"child.ts"}
        # Parent project survives if it owns at least one disjoint file.
        # If the test layout had only ``child.ts``, the parent would be
        # dropped (no owned files). Here ``lone.ts`` keeps it alive.
        assert tmp_path.resolve() in roots_to_files
        assert roots_to_files[tmp_path.resolve()] == {"lone.ts"}
        # Sanity: every file appears in exactly one project's list.
        all_files = [f for files in roots_to_files.values() for f in files]
        assert sorted(all_files) == sorted(set(all_files))


class TestTypeScriptProjectDataclass:
    def test_default_files_empty_list(self):
        p = TypeScriptProject(root=Path("/x"))
        assert p.files == []


class TestResolveSystemTsc:
    """Pins the contract that we delegate to ``shutil.which("tsc")``.

    Why: Windows can install tsc as ``tsc.cmd`` (npm), ``tsc.exe``, or
    ``tsc.bat`` depending on the installer. ``shutil.which`` honors
    ``PATHEXT`` and finds whichever is registered first; a hand-rolled
    PATH loop with a fixed extension (which we used to have) would miss
    the alternatives. Regressing back to a hardcoded extension would
    silently break Windows users without a typescript-language-server
    install under ``servers/``.
    """

    def test_delegates_to_shutil_which_with_tsc(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[str] = []

        def fake_which(name: str) -> str | None:
            calls.append(name)
            return "/some/path/to/tsc"

        monkeypatch.setattr("static_analyzer.typescript_config_scanner.shutil.which", fake_which)
        result = _resolve_system_tsc()
        assert result == "/some/path/to/tsc"
        assert calls == ["tsc"]

    def test_returns_none_when_tsc_absent(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "static_analyzer.typescript_config_scanner.shutil.which",
            lambda _name: None,
        )
        assert _resolve_system_tsc() is None
