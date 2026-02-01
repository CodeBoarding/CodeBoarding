import os
import unittest
import shutil
from pathlib import Path
from codeboarding.cli.tracer import find_real_binary, main
from unittest.mock import patch, MagicMock


class TestTracer(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("temp_test_tracer")
        self.test_dir.mkdir(exist_ok=True)
        self.shim_dir = self.test_dir / ".codeboarding" / "shims"
        self.trace_dir = self.test_dir / ".codeboarding" / "traces"

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_find_real_binary(self):
        # Create a dummy bin dir and a dummy shim dir
        dummy_bin = self.test_dir / "bin"
        dummy_bin.mkdir()
        ls_path = dummy_bin / "ls"
        ls_path.touch()
        ls_path.chmod(0o755)

        dummy_shim = self.test_dir / "shim"
        dummy_shim.mkdir()

        # Set PATH to include both
        with patch.dict(os.environ, {"PATH": f"{dummy_shim}:{dummy_bin}"}):
            real_bin = find_real_binary("ls", dummy_shim)
            self.assertEqual(Path(real_bin).resolve(), ls_path.resolve())

    @patch("codeboarding.cli.tracer.get_project_root")
    @patch("sys.stdout", new_callable=MagicMock)
    def test_main_creates_shims(self, mock_stdout, mock_get_root):
        mock_get_root.return_value = self.test_dir

        # Mock find_real_binary to return a fake path
        with patch("codeboarding.cli.tracer.find_real_binary") as mock_find:
            mock_find.return_value = "/bin/ls"

            with patch("sys.argv", ["tracer.py", "--tools", "ls"]):
                main()

        shim_path = self.test_dir / ".codeboarding" / "shims" / "ls"
        self.assertTrue(shim_path.exists())
        self.assertTrue(os.access(shim_path, os.X_OK))

        with open(shim_path, "r") as f:
            content = f.read()
            self.assertIn('REAL_BIN="/bin/ls"', content)
            self.assertIn('TOOL="ls"', content)
            self.assertIn(f'REPO_ROOT="{self.test_dir.absolute()}"', content)


if __name__ == "__main__":
    unittest.main()
