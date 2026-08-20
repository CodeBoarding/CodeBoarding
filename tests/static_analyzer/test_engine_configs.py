"""One engine per language family, not one per detected language."""

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import (
    StaticAnalysisFatalError,
    StaticAnalyzer,
    _adapter_names_for,
    _create_engine_configs,
)
from static_analyzer.constants import Language
from static_analyzer.engine.adapters.typescript_adapter import TypeScriptAdapter
from static_analyzer.programming_language import ProgrammingLanguage
from static_analyzer.scanner import ProjectScanner

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


if __name__ == "__main__":
    unittest.main()


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


class TestFamilyOwnerFlip(unittest.TestCase):
    def test_a_cache_owned_by_the_other_family_language_is_refused(self):
        # Adding a repo's first .ts flips the owner to TypeScript. Extracting the cached state
        # by the new language finds nothing and would rebuild only the changed files.
        from static_analyzer.analysis_result import StaticAnalysisResults
        from static_analyzer.constants import Language

        tmp = Path(tempfile.mkdtemp()).resolve()
        with patch.object(ProjectScanner, "scan", return_value=[lang("TypeScript"), lang("JavaScript")]):
            analyzer = StaticAnalyzer(tmp)

        cached = StaticAnalysisResults()
        cached._bucket(Language.JAVASCRIPT)

        self.assertTrue(analyzer._family_owner_changed(cached))

    def test_a_cache_the_live_adapter_owns_is_accepted(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        with patch.object(ProjectScanner, "scan", return_value=[lang("TypeScript")]):
            analyzer = StaticAnalyzer(tmp)

        from static_analyzer.analysis_result import StaticAnalysisResults
        from static_analyzer.constants import Language

        cached = StaticAnalysisResults()
        cached._bucket(Language.TYPESCRIPT)

        self.assertFalse(analyzer._family_owner_changed(cached))

    def test_a_python_only_cache_is_never_a_family_flip(self):
        from static_analyzer.analysis_result import StaticAnalysisResults
        from static_analyzer.constants import Language

        tmp = Path(tempfile.mkdtemp()).resolve()
        with patch.object(ProjectScanner, "scan", return_value=[lang("Python")]):
            analyzer = StaticAnalyzer(tmp)

        cached = StaticAnalysisResults()
        cached._bucket(Language.PYTHON)

        self.assertFalse(analyzer._family_owner_changed(cached))
