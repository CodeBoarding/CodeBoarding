import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer import StaticAnalyzer


class TestDiscoverFileDependencies(unittest.TestCase):
    """Tests for StaticAnalyzer.discover_file_dependencies handling both Location and LocationLink LSP formats."""

    def _make_analyzer(self, file_ext: str = ".py") -> tuple[StaticAnalyzer, MagicMock]:
        analyzer = object.__new__(StaticAnalyzer)
        adapter = MagicMock()
        adapter.file_extensions = {file_ext}
        client = MagicMock()
        analyzer._engine_clients = [(adapter, Path("/fake"), client)]
        return analyzer, client

    @patch.object(StaticAnalyzer, "__init__", lambda self, *a, **kw: None)
    def test_location_format_uri(self):
        analyzer, client = self._make_analyzer()
        src = Path("/project/main.py").resolve()
        dep = Path("/project/utils.py").resolve()

        client.send_definition_batch.return_value = (
            [[{"uri": dep.as_uri(), "range": {"start": {"line": 0, "character": 0}}}]],
            [],
        )

        with patch("static_analyzer.SourceInspector") as MockInspector:
            MockInspector.return_value.find_call_sites.return_value = [(1, 0)]
            result = analyzer.discover_file_dependencies(src)

        self.assertEqual(result, [str(dep)])

    @patch.object(StaticAnalyzer, "__init__", lambda self, *a, **kw: None)
    def test_location_link_format_target_uri(self):
        analyzer, client = self._make_analyzer(".java")
        src = Path("/project/Main.java").resolve()
        dep = Path("/project/Utils.java").resolve()

        client.send_definition_batch.return_value = (
            [[{"targetUri": dep.as_uri(), "targetRange": {"start": {"line": 0, "character": 0}}}]],
            [],
        )

        with patch("static_analyzer.SourceInspector") as MockInspector:
            MockInspector.return_value.find_call_sites.return_value = [(5, 10)]
            result = analyzer.discover_file_dependencies(src)

        self.assertEqual(result, [str(dep)])

    @patch.object(StaticAnalyzer, "__init__", lambda self, *a, **kw: None)
    def test_mixed_formats(self):
        analyzer, client = self._make_analyzer()
        src = Path("/project/main.py").resolve()
        dep_a = Path("/project/a.py").resolve()
        dep_b = Path("/project/b.py").resolve()

        client.send_definition_batch.return_value = (
            [
                [{"uri": dep_a.as_uri(), "range": {"start": {"line": 0, "character": 0}}}],
                [{"targetUri": dep_b.as_uri(), "targetRange": {"start": {"line": 0, "character": 0}}}],
            ],
            [],
        )

        with patch("static_analyzer.SourceInspector") as MockInspector:
            MockInspector.return_value.find_call_sites.return_value = [(1, 0), (2, 0)]
            result = analyzer.discover_file_dependencies(src)

        self.assertEqual(sorted(result), sorted([str(dep_a), str(dep_b)]))


if __name__ == "__main__":
    unittest.main()
