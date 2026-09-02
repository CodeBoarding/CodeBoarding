from static_analyzer.clustering.names import Trie, unit_position, units_from_graph, units_from_graphs
from tests.static_analyzer.names.conftest import graph_from_layout, node_of, unit


class TestUnitPosition:
    def test_python_module_with_several_symbols(self):
        names = ["agents.agent.CodeBoardingAgent", "agents.agent.CodeBoardingAgent.run", "agents.agent.helper"]
        assert unit_position(names, ".") == ("agents", "agent")

    def test_a_file_declaring_one_class_sits_in_its_module_not_its_class(self):
        names = ["agents.abstraction_agent.AbstractionAgent", "agents.abstraction_agent.AbstractionAgent.run"]
        assert unit_position(names, ".") == ("agents", "abstraction_agent")

    def test_a_file_with_one_symbol(self):
        assert unit_position(["pkg.mod.only"], ".") == ("pkg", "mod")

    def test_csharp_single_type_file_sits_in_its_directory(self):
        """#545 collapses the stem into the type, so the position is the directory."""
        names = ["Basket.API.Model.CustomerBasket", "Basket.API.Model.CustomerBasket.CustomerBasket()"]
        assert unit_position(names, ".") == ("Basket", "API", "Model")

    def test_csharp_file_with_sibling_types_keeps_its_stem(self):
        names = ["Basket.API.Extensions.Extensions.Ext", "Basket.API.Extensions.Extensions.IntegrationEventContext"]
        assert unit_position(names, ".") == ("Basket", "API", "Extensions", "Extensions")

    def test_java_type_sits_in_its_package(self):
        assert unit_position(["core.org.mockito.Fixture", "core.org.mockito.Fixture.run()"], ".") == (
            "core",
            "org",
            "mockito",
        )

    def test_generic_and_parameter_delimiters_do_not_split(self):
        names = ["A.B.Repo<T>.Add(Map<K, V.W> item)", "A.B.Repo<T>"]
        assert unit_position(names, ".") == ("A", "B")

    def test_names_sharing_nothing_sit_at_the_root(self):
        assert unit_position(["a.x", "b.y"], ".") == ()


class TestUnitsFromGraph:
    def test_one_unit_per_file_keyed_by_the_engine_path(self):
        graph = graph_from_layout(
            {
                "/repo/pkg/a.py": ["pkg.a.f", "pkg.a.g"],
                "/repo/pkg/b.py": ["pkg.b.C", "pkg.b.C.m"],
            }
        )
        units = units_from_graph(graph, "python")
        assert [unit.unit_id for unit in units] == ["/repo/pkg/a.py", "/repo/pkg/b.py"]
        assert units[0].names == ("pkg.a.f", "pkg.a.g")
        assert units[1].position == ("pkg", "b")

    def test_the_path_is_an_identity_and_never_read(self):
        """Blanking every path segment changes no position: the names carry the structure."""
        layout = {"/x/y/z.py": ["pkg.mod.C", "pkg.mod.C.m"]}
        opaque = {"0": layout["/x/y/z.py"]}
        assert units_from_graph(graph_from_layout(layout), "python")[0].position == (
            units_from_graph(graph_from_layout(opaque), "python")[0].position
        )

    def test_languages_are_read_in_sorted_order(self):
        graphs = {
            "typescript": graph_from_layout({"/r/ts.ts": ["src.ts.f"]}, "typescript"),
            "python": graph_from_layout({"/r/py.py": ["src.py.f"]}),
        }
        assert [unit.language for unit in units_from_graphs(graphs)] == ["python", "typescript"]


class TestTrie:
    def test_counts_units_per_subtree(self):
        trie = Trie([unit("a", "p.x.A"), unit("b", "p.x.B"), unit("c", "p.y.C")])
        assert trie.root.count == 3
        assert node_of(trie, ("p", "x")).count == 2
        assert node_of(trie, ("p", "y")).count == 1
        assert trie.node(("p", "z")) is None

    def test_a_unit_at_the_root_is_a_root_unit(self):
        trie = Trie([unit("a", "A"), unit("b", "p.B")])
        assert [u.unit_id for u in trie.root.units] == ["a"]
        assert node_of(trie, ("p",)).count == 1
