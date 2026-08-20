"""The languageId a file is opened with decides whether tsserver parses JSX."""

import unittest
from pathlib import Path

from static_analyzer.engine.adapters.typescript_adapter import JavaScriptAdapter, TypeScriptAdapter
from static_analyzer.engine.adapters.python_adapter import PythonAdapter


class TestLanguageIdPerSuffix(unittest.TestCase):
    def setUp(self):
        self.ts = TypeScriptAdapter()
        self.js = JavaScriptAdapter()

    def test_tsx_opens_as_the_jsx_dialect(self):
        # Opened as plain "typescript", tsserver reads `<div ...>` as a type assertion and
        # returns the component's symbols flattened, truncated and partly unnamed.
        self.assertEqual(self.ts.language_id_for(Path("src/Button.tsx")), "typescriptreact")

    def test_jsx_opens_as_the_jsx_dialect(self):
        self.assertEqual(self.js.language_id_for(Path("src/Button.jsx")), "javascriptreact")

    def test_plain_suffixes_keep_the_adapter_id(self):
        for suffix, adapter, expected in (
            (".ts", self.ts, "typescript"),
            (".mts", self.ts, "typescript"),
            (".js", self.js, "javascript"),
            (".mjs", self.js, "javascript"),
        ):
            with self.subTest(suffix=suffix):
                self.assertEqual(adapter.language_id_for(Path(f"src/mod{suffix}")), expected)

    def test_jsx_dialect_follows_the_adapter_owning_the_file(self):
        # The TypeScript adapter owns the whole family when a project mixes them, so a .jsx
        # it opens still needs the JSX dialect rather than the adapter-wide id.
        self.assertEqual(self.ts.language_id_for(Path("src/Legacy.jsx")), "javascriptreact")

    def test_unknown_suffix_falls_back_to_the_adapter_id(self):
        self.assertEqual(self.ts.language_id_for(Path("src/data.json")), "typescript")

    def test_other_languages_are_unaffected(self):
        adapter = PythonAdapter()
        self.assertEqual(adapter.language_id_for(Path("mod.py")), adapter.language_id)


if __name__ == "__main__":
    unittest.main()
