"""One engine per language family, not one per detected language."""

import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import (
    StaticAnalysisFatalError,
    StaticAnalyzer,
    _adapter_names_for,
    _create_engine_configs,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.config import Language
from static_analyzer.engine.adapters.python_adapter import PythonAdapter
from static_analyzer.engine.adapters.typescript_adapter import JavaScriptAdapter, TypeScriptAdapter
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.scanner import ProjectScanner
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner, TypeScriptProject

_SUFFIXES = {
    "TypeScript": [".ts"],
    "TSX": [".tsx"],
    "JavaScript": [".js"],
    "JSX": [".jsx"],
    "Python": [".py"],
    "Java": [".java"],
}


def lang(name: str, size: int = 100) -> ProgrammingLanguage:
    return ProgrammingLanguage(
        language=name,
        size=size,
        percentage=1.0,
        suffixes=_SUFFIXES[name],
        server_commands=["stub-lsp"],
        lsp_server_key="typescript" if name in ("TypeScript", "TSX", "JavaScript", "JSX") else name.lower(),
    )


class TestAdapterNamesFor(unittest.TestCase):
    def test_typescript_and_tsx_collapse_to_one_adapter(self):
        self.assertEqual(_adapter_names_for([lang("TypeScript"), lang("TSX")]), ["TypeScript"])

    def test_javascript_and_jsx_collapse_to_one_adapter(self):
        self.assertEqual(_adapter_names_for([lang("JavaScript"), lang("JSX")]), ["JavaScript"])

    def test_typescript_wins_the_family_over_javascript(self):
        self.assertEqual(_adapter_names_for([lang("JavaScript"), lang("TSX"), lang("TypeScript")]), ["TypeScript"])

    def test_javascript_survives_when_no_typescript_exists(self):
        self.assertEqual(_adapter_names_for([lang("JavaScript"), lang("JSX")]), ["JavaScript"])

    def test_other_languages_keep_their_order(self):
        names = _adapter_names_for([lang("Python"), lang("TypeScript"), lang("JavaScript"), lang("Java")])
        self.assertEqual(names, ["Python", "TypeScript", "Java"])


class TestEngineConfigsPerFamily(unittest.TestCase):
    def test_a_typescript_repo_gets_exactly_one_engine(self):
        configs = self._configs(
            [lang("TypeScript"), lang("TSX"), lang("JavaScript")],
            {
                "tsconfig.json": json.dumps({"include": ["**/*.ts", "**/*.tsx"]}),
                "src/a.ts": "export function a(): void {}\n",
                "src/b.tsx": "export const B = () => null;\n",
                "bench/tool.mjs": "export function t() {}\n",
            },
        )
        self.assertEqual(len(configs), 1)
        self.assertIs(configs[0].adapter.language_enum, Language.TYPESCRIPT)

    def test_a_pure_javascript_repo_keeps_its_engine(self):
        # The key risk: dropping JavaScript unconditionally would leave this repo with none.
        configs = self._configs([lang("JavaScript")], {"src/a.js": "export function a() {}\n"})
        self.assertEqual(len(configs), 1)
        self.assertIs(configs[0].adapter.language_enum, Language.JAVASCRIPT)

    def test_a_javascript_repo_with_jsconfig_keeps_its_engine(self):
        configs = self._configs(
            [lang("JavaScript")],
            {"jsconfig.json": json.dumps({"include": ["**/*.js"]}), "src/a.js": "export function a() {}\n"},
        )
        self.assertEqual(len(configs), 1)
        self.assertIs(configs[0].adapter.language_enum, Language.JAVASCRIPT)

    def test_a_python_repo_is_untouched(self):
        configs = self._configs([lang("Python")], {"a.py": "def a():\n    pass\n"})
        self.assertEqual(len(configs), 1)
        self.assertIs(configs[0].adapter.language_enum, Language.PYTHON)

    def _configs(self, languages, files: dict[str, str]):
        # Resolved: on macOS mkdtemp hands back /var/... while tsc reports /private/var/...
        tmp = Path(tempfile.mkdtemp()).resolve()
        for name, body in files.items():
            path = tmp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        return _create_engine_configs(languages, tmp, RepoIgnoreManager(tmp))


class TestTypeScriptFamilyExtensions(unittest.TestCase):
    def test_the_typescript_adapter_claims_both_families(self):
        extensions = TypeScriptAdapter().file_extensions
        for suffix in (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, extensions)


class TestIncrementalRefusesAnIncompatibleCache(unittest.TestCase):
    def test_an_artifact_from_another_engine_version_is_refused(self):
        # AGENTS.md: incremental never silently becomes full. A tag bump makes the artifact
        # unreadable, and running a full pass here would overwrite the one a later run needs.
        tmp = Path(tempfile.mkdtemp()).resolve()
        (tmp / "a.py").write_text("def a():\n    pass\n")
        artifacts = tmp / ".codeboarding"
        artifacts.mkdir()
        (artifacts / "static_analysis.pkl").write_bytes(b"not-a-real-pickle")
        (artifacts / "static_analysis.sha").write_text("v1\ndeadbeef\n")

        # The scanner shells out to tokei, which the analyzer only needs to pick engines.
        with patch.object(ProjectScanner, "scan", return_value=[]):
            analyzer = StaticAnalyzer(tmp, changed_files={tmp / "a.py"})
        analyzer._clients_started = True

        with self.assertRaises(StaticAnalysisFatalError) as caught:
            analyzer.analyze(artifacts)
        self.assertIn("full analysis", str(caught.exception))


class TestFamilyBucketIsStable(unittest.TestCase):
    """The bucket key must not move when a repo gains its first .ts or loses its last."""

    def test_both_family_adapters_store_under_one_language(self):
        self.assertIs(TypeScriptAdapter().results_language, Language.TYPESCRIPT)
        self.assertIs(JavaScriptAdapter().results_language, Language.TYPESCRIPT)

    def test_the_family_adapters_still_report_their_own_language(self):
        # results_language decides storage; language_enum stays the adapter's own identity.
        self.assertIs(TypeScriptAdapter().language_enum, Language.TYPESCRIPT)
        self.assertIs(JavaScriptAdapter().language_enum, Language.JAVASCRIPT)

    def test_other_languages_store_under_themselves(self):
        self.assertIs(PythonAdapter().results_language, Language.PYTHON)

    def test_a_bucket_reports_the_languages_its_files_hold(self):
        results = StaticAnalysisResults()
        results.add_source_files(Language.TYPESCRIPT, ["/r/src/a.ts", "/r/src/b.js", "/r/src/c.jsx"])
        self.assertEqual(
            results.source_languages(Language.TYPESCRIPT),
            {Language.TYPESCRIPT, Language.JAVASCRIPT},
        )

    def test_a_javascript_only_repo_is_still_reported_as_javascript(self):
        # The key says TypeScript because the family owns the bucket; the files say otherwise.
        results = StaticAnalysisResults()
        results.add_source_files(Language.TYPESCRIPT, ["/r/src/a.js", "/r/src/b.mjs"])
        self.assertEqual(results.source_languages(Language.TYPESCRIPT), {Language.JAVASCRIPT})
        self.assertEqual(results.present_languages(), {Language.JAVASCRIPT})

    def test_adding_the_first_typescript_file_shows_up_as_a_new_language(self):
        results = StaticAnalysisResults()
        results.add_source_files(Language.TYPESCRIPT, ["/r/src/a.js"])
        self.assertEqual(results.present_languages(), {Language.JAVASCRIPT})
        results.add_source_files(Language.TYPESCRIPT, ["/r/src/b.ts"])
        self.assertEqual(results.present_languages(), {Language.JAVASCRIPT, Language.TYPESCRIPT})

    def test_files_are_attributed_to_the_language_they_are_written_in(self):
        results = StaticAnalysisResults()
        results.add_source_files(Language.TYPESCRIPT, ["/r/a.ts", "/r/b.tsx", "/r/c.js", "/r/d.jsx"])
        self.assertEqual(
            [Path(p).name for p in results.source_files_of_language(Language.JAVASCRIPT)], ["c.js", "d.jsx"]
        )
        self.assertEqual(
            [Path(p).name for p in results.source_files_of_language(Language.TYPESCRIPT)], ["a.ts", "b.tsx"]
        )


if __name__ == "__main__":
    unittest.main()


class TestMixedFamilyCoverage(unittest.TestCase):
    """A tsconfig omits .js unless allowJs is set, but one engine owns both families."""

    def _repo(self) -> Path:
        root = Path(tempfile.mkdtemp()).resolve()
        (root / "src").mkdir()
        for name in ("lib.ts", "widget.tsx", "util.js", "panel.jsx"):
            (root / "src" / name).write_text("export const x = 1;\n")
        skipped = root / "node_modules" / "dep"
        skipped.mkdir(parents=True)
        (skipped / "index.js").write_text("module.exports = 1;\n")
        return root

    def _scanner(self, root: Path) -> TypeScriptConfigScanner:
        return TypeScriptConfigScanner(root, ignore_manager=RepoIgnoreManager(root))

    def test_unclaimed_family_files_are_found(self):
        root = self._repo()
        claimed = TypeScriptProject(root=root, files=[(root / "src" / "lib.ts").resolve()])
        found = [p.name for p in self._scanner(root).find_unclaimed_family_files([claimed])]
        self.assertEqual(sorted(found), ["panel.jsx", "util.js", "widget.tsx"])

    def test_node_modules_stays_out(self):
        root = self._repo()
        found = self._scanner(root).find_unclaimed_family_files([])
        self.assertNotIn("index.js", [p.name for p in found])

    def test_a_claimed_file_is_not_added_twice(self):
        root = self._repo()
        claimed = TypeScriptProject(root=root, files=[p.resolve() for p in (root / "src").iterdir()])
        self.assertEqual(self._scanner(root).find_unclaimed_family_files([claimed]), [])

    def test_javascript_reaches_the_engine_when_tsconfig_omits_it(self):
        root = self._repo()
        only_ts = TypeScriptProject(root=root, files=[(root / "src" / "lib.ts").resolve()])
        with patch.object(TypeScriptConfigScanner, "find_typescript_projects", return_value=[only_ts]):
            configs = _create_engine_configs([lang("TypeScript"), lang("JavaScript")], root, RepoIgnoreManager(root))
        self.assertEqual(len(configs), 1)
        names = sorted(p.name for p in configs[0].source_files)
        self.assertEqual(names, ["lib.ts", "panel.jsx", "util.js", "widget.tsx"])


class TestTsconfigExcludeIsHonoured(unittest.TestCase):
    """Silence about .js is not the same as an explicit exclude."""

    def _repo(self) -> Path:
        root = Path(tempfile.mkdtemp()).resolve()
        for sub in ("src", "e2e", "webview-ui/src", "gen"):
            (root / sub).mkdir(parents=True)
        (root / "src" / "keep.ts").write_text("export const a = 1;\n")
        (root / "src" / "keep.js").write_text("export const b = 2;\n")
        (root / "e2e" / "spec.js").write_text("export const c = 3;\n")
        (root / "webview-ui" / "src" / "app.jsx").write_text("export const d = 4;\n")
        (root / "gen" / "thing.gen.js").write_text("export const e = 5;\n")
        return root

    def _topped_up(self, root: Path, exclude: list[str]) -> list[str]:
        project = TypeScriptProject(root=root, files=[(root / "src" / "keep.ts").resolve()], exclude=exclude)
        scanner = TypeScriptConfigScanner(root, ignore_manager=RepoIgnoreManager(root))
        return sorted(p.relative_to(root).as_posix() for p in scanner.find_unclaimed_family_files([project]))

    def test_a_plain_directory_exclude_takes_its_whole_subtree(self):
        self.assertNotIn("webview-ui/src/app.jsx", self._topped_up(self._repo(), ["webview-ui"]))

    def test_several_excludes_all_apply(self):
        root = self._repo()
        self.assertEqual(self._topped_up(root, ["webview-ui", "gen"]), ["src/keep.js"])

    def test_a_star_pattern_excludes_matching_files(self):
        root = self._repo()
        self.assertNotIn("gen/thing.gen.js", self._topped_up(root, ["**/*.gen.js"]))

    def test_a_star_pattern_does_not_over_match_other_suffixes(self):
        # `**/*.gen.ts` says nothing about .js, so the .js is still topped up.
        root = self._repo()
        self.assertIn("gen/thing.gen.js", self._topped_up(root, ["**/*.gen.ts"]))

    def test_nothing_excluded_means_everything_unclaimed_is_added(self):
        # e2e is absent because the default .codeboardingignore already carries **/e2e/**,
        # so the top-up never sees it regardless of what the tsconfig says.
        root = self._repo()
        self.assertEqual(
            self._topped_up(root, []),
            ["gen/thing.gen.js", "src/keep.js", "webview-ui/src/app.jsx"],
        )

    def test_the_ignore_file_still_wins_over_an_empty_exclude(self):
        root = self._repo()
        self.assertNotIn("e2e/spec.js", self._topped_up(root, []))


class TestTscIsAskedForJavaScript(unittest.TestCase):
    """tsc decides family membership, so it has to be asked about the JS too."""

    def test_showconfig_requests_allowjs(self):
        scanner = TypeScriptConfigScanner(Path("/repo"), ignore_manager=RepoIgnoreManager(Path("/repo")))
        with patch("static_analyzer.typescript_config_scanner.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout='{"files": []}', stderr="")
            scanner._showconfig(Path("/repo"), ["tsc", "--showConfig"])
        self.assertIn("--allowJs", run.call_args[0][0])

    def test_exclude_is_carried_off_showconfig(self):
        scanner = TypeScriptConfigScanner(Path("/repo"), ignore_manager=RepoIgnoreManager(Path("/repo")))
        payload = '{"files": [], "exclude": ["legacy", "**/*.gen.ts"]}'
        with patch("static_analyzer.typescript_config_scanner.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout=payload, stderr="")
            _, excluded = scanner._resolve_project_files(Path("/repo"), ["tsc", "--showConfig"], [])
        self.assertEqual(excluded, ["legacy", "**/*.gen.ts"])
