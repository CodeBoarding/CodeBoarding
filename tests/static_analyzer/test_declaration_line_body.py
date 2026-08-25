"""A call in a declaration's initialiser or concise body is body, not signature.

Correct JSX parsing makes tsserver report one-line symbols — an arrow inside a JSX attribute,
and the constant a call initialises — which then become the innermost container for a call on
their own declaration line. Without this, ``_process_references_for_position`` discards it.
"""

import tempfile
import unittest
from pathlib import Path

from static_analyzer.engine.source_inspector import SourceInspector


def write(suffix: str, body: str) -> Path:
    path = Path(tempfile.mkdtemp()) / f"sample{suffix}"
    path.write_text(body)
    return path


class TestDeclarationLineBody(unittest.TestCase):
    def setUp(self):
        self.si = SourceInspector()

    def test_a_call_in_a_jsx_attribute_arrow_is_body(self):
        # onClick={() => track(...)} — the arrow starts at column 16 on line 2 (0-based).
        path = write(".tsx", 'export function L() {\n  return (\n    <a onClick={() => track("x")}>y</a>\n  );\n}\n')
        self.assertTrue(self.si.is_reference_in_declaration_body(path, 2, 16, 2, 22, 27))

    def test_a_call_initialising_a_constant_is_body(self):
        # const device = useDevice() — the declarator starts at column 8 on line 1.
        path = write(".tsx", "export const C = () => {\n  const device = useDevice();\n  return device;\n};\n")
        self.assertTrue(self.si.is_reference_in_declaration_body(path, 1, 8, 1, 17, 26))

    def test_the_rule_applies_to_plain_javascript_too(self):
        path = write(".js", "export const DEFAULT_HANDLER = (x) => add(x, 1);\n")
        self.assertTrue(self.si.is_reference_in_declaration_body(path, 0, 13, 0, 38, 41))

    def test_a_python_default_argument_is_not_body(self):
        # Evaluated by the enclosing scope at def time, so it must not be credited to the
        # function being declared. The rule stays off every grammar but TypeScript/JavaScript.
        path = write(".py", "def g(x=f()):\n    return x\n")
        self.assertFalse(self.si.is_reference_in_declaration_body(path, 0, 0, 0, 8, 9))


if __name__ == "__main__":
    unittest.main()
