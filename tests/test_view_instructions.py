import tempfile
import unittest
from pathlib import Path

from codeboarding_cli.view_instructions import print_view_instructions


class TestPrintViewInstructions(unittest.TestCase):
    def test_prints_webview_url_and_file_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = Path(temp_dir) / "analysis.json"
            analysis.write_text("{}")

            with self.assertLogs("codeboarding_cli.view_instructions", level="INFO") as cm:
                print_view_instructions(analysis)

        msg = "\n".join(cm.output)
        self.assertIn("app.codeboarding.org", msg)
        self.assertIn("Load a file", msg)
        self.assertIn(str(analysis.resolve()), msg)

    def test_missing_file_warns_and_does_not_print_instructions(self):
        with self.assertLogs("codeboarding_cli.view_instructions", level="WARNING") as cm:
            print_view_instructions(Path("/does/not/exist/analysis.json"))

        msg = "\n".join(cm.output)
        self.assertIn("not found", msg)
        self.assertNotIn("Load a file", msg)


if __name__ == "__main__":
    unittest.main()
