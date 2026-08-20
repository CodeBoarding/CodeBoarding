"""One engine per language family, not one per detected language.

The scanner reports TypeScript, TSX, JavaScript and JSX separately, and they all share a
single ``typescript-language-server``. Emitting a config per detected language indexes the
same files repeatedly into separate ``Language`` buckets that are then clustered as if they
were different codebases.
"""

import json
import tempfile
import unittest
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import _adapter_names_for, _create_engine_configs
from static_analyzer.constants import Language
from static_analyzer.programming_language import ProgrammingLanguage

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
    def _configs(self, languages, files: dict[str, str]):
        # Resolved: on macOS mkdtemp hands back /var/... while tsc reports /private/var/...
        tmp = Path(tempfile.mkdtemp()).resolve()
        for name, body in files.items():
            path = tmp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        return _create_engine_configs(languages, tmp, RepoIgnoreManager(tmp))

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


class TestTypeScriptFamilyExtensions(unittest.TestCase):
    def test_the_typescript_adapter_claims_both_families(self):
        from static_analyzer.engine.adapters.typescript_adapter import TypeScriptAdapter

        extensions = TypeScriptAdapter().file_extensions
        for suffix in (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, extensions)


if __name__ == "__main__":
    unittest.main()
