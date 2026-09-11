from pathlib import Path

import pytest

from static_analyzer.clustering.names import Trie, units_from_graph, units_from_graphs
from tests.static_analyzer.names.conftest import graph_from_layout, node_of, unit


class TestUnitsFromGraph:
    def test_one_unit_per_file_positioned_by_its_directory(self):
        graph = graph_from_layout(
            {
                "pkg/a.py": ["pkg.a.f", "pkg.a.g"],
                "pkg/sub/b.py": ["whatever.the.adapter.said"],
            }
        )
        units = units_from_graph(graph, "python")
        assert [u.unit_id for u in units] == ["pkg/a.py", "pkg/sub/b.py"]
        assert units[0].names == ("pkg.a.f", "pkg.a.g")
        assert units[0].position == ("pkg",) and units[0].key == ("pkg", "a.py")
        assert units[1].position == ("pkg", "sub") and units[1].key == ("pkg", "sub", "b.py")

    def test_a_file_and_a_directory_of_one_name_have_distinct_keys(self):
        graph = graph_from_layout({"pkg/foo.py": ["pkg.foo.f"], "pkg/foo/stray.py": ["pkg.foo.stray.g"]})
        keys = [u.key for u in units_from_graph(graph, "python")]
        assert keys == [("pkg", "foo.py"), ("pkg", "foo", "stray.py")]
        assert keys[1][: len(keys[0])] != keys[0]

    def test_the_names_are_never_read_for_structure(self):
        """Two files spelled differently by two adapters sit together when they share a directory."""
        graph = graph_from_layout({"src/WebApp/x.cs": ["WebApp.x"], "src/WebApp/y.js": ["src.WebApp.y.f"]})
        assert {u.position for u in units_from_graph(graph, "csharp")} == {("src", "WebApp")}

    def test_a_dotted_directory_is_one_segment(self):
        graph = graph_from_layout({"BTCPayServer.Client/C.cs": ["BTCPayServer.Client.C"]}, "csharp")
        assert units_from_graph(graph, "csharp")[0].position == ("BTCPayServer.Client",)

    def test_an_absolute_path_is_positioned_under_the_repository_root(self, tmp_path: Path):
        graph = graph_from_layout({str(tmp_path / "pkg" / "a.py"): ["pkg.a.f"]})
        assert units_from_graph(graph, "python", tmp_path)[0].position == ("pkg",)
        with pytest.raises(ValueError, match="absolute"):
            units_from_graph(graph, "python")

    def test_a_root_file_has_an_empty_position(self):
        graph = graph_from_layout({"setup.py": ["setup.main"]})
        assert units_from_graph(graph, "python")[0].position == ()

    def test_the_nearest_manifest_above_a_file_names_its_project(self, tmp_path: Path):
        (tmp_path / "lib" / "src").mkdir(parents=True)
        (tmp_path / "lib" / "Lib.csproj").write_text("<Project/>")
        (tmp_path / "app").mkdir()
        (tmp_path / "package.json").write_text("{}")
        graph = graph_from_layout(
            {str(tmp_path / "lib" / "src" / "a.cs"): ["a"], str(tmp_path / "app" / "b.cs"): ["b"]}, "csharp"
        )
        units = {u.position: u.project for u in units_from_graph(graph, "csharp", tmp_path)}
        assert units == {
            ("lib", "src"): ("lib",),
            ("app",): None,
        }, "the repository root's own manifest is not a project"

    def test_a_path_outside_the_repository_root_is_refused(self, tmp_path: Path):
        graph = graph_from_layout({str(tmp_path.parent / "elsewhere" / "a.py"): ["a"]})
        with pytest.raises(ValueError, match="outside"):
            units_from_graph(graph, "python", tmp_path)
        with pytest.raises(ValueError, match="outside"):
            units_from_graph(graph_from_layout({"../a.py": ["a"]}), "python")

    def test_languages_are_read_in_sorted_order(self):
        graphs = {
            "typescript": graph_from_layout({"r/ts.ts": ["src.ts.f"]}, "typescript"),
            "python": graph_from_layout({"r/py.py": ["src.py.f"]}),
        }
        assert [u.language for u in units_from_graphs(graphs)] == ["python", "typescript"]


class TestTrie:
    def test_counts_units_per_subtree(self):
        trie = Trie([unit("p/x/a.py", "A"), unit("p/x/b.py", "B"), unit("p/y/c.py", "C")])
        assert trie.root.count == 3
        assert node_of(trie, ("p", "x")).count == 2
        assert node_of(trie, ("p", "y")).count == 1
        assert trie.node(("p", "z")) is None

    def test_a_unit_at_the_root_is_a_root_unit(self):
        trie = Trie([unit("a.py", "A"), unit("p/b.py", "B")])
        assert [u.unit_id for u in trie.root.units] == ["a.py"]
        assert node_of(trie, ("p",)).count == 1
