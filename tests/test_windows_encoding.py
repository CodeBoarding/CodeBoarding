import unittest
from pathlib import Path


class TestWindowsEncoding(unittest.TestCase):

    def test_no_cp1252_unencodable_characters_in_source(self):
        """Scan all .py source files for characters that cannot be encoded with cp1252.

        On Windows the default console encoding is often cp1252.  Any such
        character that reaches a log statement or print() will raise
        UnicodeEncodeError, stalling the analysis.  This test walks every .py
        file in the repository and fails if any contain unencodable characters.
        """
        repo_root = Path(__file__).resolve().parent.parent
        violations: list[str] = []

        for py_file in sorted(repo_root.rglob("*.py")):
            # Skip virtual-env, hidden dirs, and build artefacts
            rel = py_file.relative_to(repo_root)
            parts = rel.parts
            if any(
                p.startswith(".") or p in ("__pycache__", ".venv", "venv", "node_modules", "build", "dist")
                for p in parts
            ):
                continue

            try:
                text = py_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            for lineno, line in enumerate(text.splitlines(), start=1):
                for col, ch in enumerate(line):
                    try:
                        ch.encode("cp1252")
                    except UnicodeEncodeError:
                        violations.append(f"{rel}:{lineno}:{col + 1}  char {ch!r} (U+{ord(ch):04X})")

        self.assertEqual(
            violations,
            [],
            f"Found {len(violations)} character(s) that cannot be encoded with cp1252 "
            f"(will break logging on Windows):\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
