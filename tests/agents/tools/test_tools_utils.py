import tempfile
import unittest
from pathlib import Path

from agents.tools.utils import read_dot_file


class TestToolsUtils(unittest.TestCase):
    def test_read_dot_file_valid(self):
        # Create a temporary dot file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
            f.write("digraph G {\n")
            f.write('  "A" -> "B";\n')
            f.write('  "A" -> "C";\n')
            f.write('  "B" -> "D";\n')
            f.write("}\n")
            temp_file = f.name

        try:
            result = read_dot_file(temp_file)
            self.assertIn("A", result)
            self.assertIn("B", result["A"])
            self.assertIn("C", result["A"])
            self.assertEqual(len(result["A"]), 2)
            self.assertIn("B", result)
            self.assertIn("D", result["B"])
        finally:
            Path(temp_file).unlink()

    def test_read_dot_file_with_quotes(self):
        # Test that quotes are properly stripped
        with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
            f.write("digraph G {\n")
            f.write('  "node1" -> "node2";\n')
            f.write("}\n")
            temp_file = f.name

        try:
            result = read_dot_file(temp_file)
            # Verify keys are without quotes
            self.assertIn("node1", result)
            self.assertNotIn('"node1"', result)
            self.assertIn("node2", result["node1"])
        finally:
            Path(temp_file).unlink()

    def test_read_dot_file_multiple_edges_from_same_source(self):
        # Test multiple edges from the same source node
        with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
            f.write("digraph G {\n")
            f.write('  "start" -> "end1";\n')
            f.write('  "start" -> "end2";\n')
            f.write('  "start" -> "end3";\n')
            f.write("}\n")
            temp_file = f.name

        try:
            result = read_dot_file(temp_file)
            self.assertEqual(len(result["start"]), 3)
            self.assertIn("end1", result["start"])
            self.assertIn("end2", result["start"])
            self.assertIn("end3", result["start"])
        finally:
            Path(temp_file).unlink()

    def test_read_dot_file_empty_graph(self):
        # Test with an empty graph
        with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
            f.write("digraph G {\n")
            f.write("}\n")
            temp_file = f.name

        try:
            result = read_dot_file(temp_file)
            self.assertEqual(result, {})
        finally:
            Path(temp_file).unlink()


if __name__ == "__main__":
    unittest.main()
