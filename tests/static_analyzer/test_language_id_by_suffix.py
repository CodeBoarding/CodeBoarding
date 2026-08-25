"""The languageId a file is opened with decides whether tsserver parses JSX."""

import unittest
from pathlib import Path

from static_analyzer.config import LANGUAGE_ID_BY_SUFFIX, JsxLanguageId, Language, SourceSuffix


class TestLanguageIdPerSuffix(unittest.TestCase):
    def test_tsx_opens_as_the_jsx_dialect(self):
        # Opened as plain "typescript", tsserver reads `<div ...>` as a type assertion and
        # returns the component's symbols flattened, truncated and partly unnamed.
        self.assertEqual(LANGUAGE_ID_BY_SUFFIX[SourceSuffix.TSX], JsxLanguageId.TYPESCRIPT)

    def test_jsx_opens_as_the_jsx_dialect(self):
        self.assertEqual(LANGUAGE_ID_BY_SUFFIX[SourceSuffix.JSX], JsxLanguageId.JAVASCRIPT)

    def test_plain_suffixes_keep_their_language_id(self):
        for suffix, expected in (
            (SourceSuffix.TS, Language.TYPESCRIPT),
            (SourceSuffix.MTS, Language.TYPESCRIPT),
            (SourceSuffix.CTS, Language.TYPESCRIPT),
            (SourceSuffix.JS, Language.JAVASCRIPT),
            (SourceSuffix.MJS, Language.JAVASCRIPT),
            (SourceSuffix.CJS, Language.JAVASCRIPT),
            (SourceSuffix.PY, Language.PYTHON),
            (SourceSuffix.CS, Language.CSHARP),
        ):
            with self.subTest(suffix=suffix):
                self.assertEqual(LANGUAGE_ID_BY_SUFFIX[suffix], expected)

    def test_the_id_follows_the_file_not_the_adapter(self):
        # The TypeScript adapter owns the whole family when a project mixes them, so the id
        # cannot come from the adapter: the same server opens .ts and .tsx differently.
        self.assertNotEqual(LANGUAGE_ID_BY_SUFFIX[SourceSuffix.TS], LANGUAGE_ID_BY_SUFFIX[SourceSuffix.TSX])

    def test_a_plain_str_suffix_still_resolves(self):
        # Path.suffix is a plain str; SourceSuffix is a StrEnum so it keys the same dict.
        self.assertEqual(LANGUAGE_ID_BY_SUFFIX[Path("src/Button.tsx").suffix], JsxLanguageId.TYPESCRIPT)

    def test_every_known_suffix_has_an_id(self):
        self.assertEqual(set(LANGUAGE_ID_BY_SUFFIX), set(SourceSuffix))

    def test_the_id_serialises_as_a_bare_string(self):
        # It goes straight into the didOpen payload, so the enum repr must not leak.
        self.assertEqual(f"{LANGUAGE_ID_BY_SUFFIX[SourceSuffix.TSX]}", "typescriptreact")


if __name__ == "__main__":
    unittest.main()
