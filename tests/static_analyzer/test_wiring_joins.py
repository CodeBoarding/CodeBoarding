"""The join: which use resolves to which unit, what kind of edge that makes, and what did not join."""

import unittest
from dataclasses import replace
from pathlib import Path

from static_analyzer.wiring.anchors import collect
from static_analyzer.wiring.compose import compose_projects
from static_analyzer.wiring.join import join
from static_analyzer.wiring.resources import Use, discover
from static_analyzer.wiring.scan import Scan
from static_analyzer.wiring.units import build_units
from static_analyzer.wiring_results import Anchor, AnchorFamily, AnchorRole, Tier, Unit, UnitKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wiring"


def joined(case: str) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Every edge the fixture declares as (source, target, kind), and everything that did not join."""
    root = FIXTURES / case
    scan = Scan(root)
    projects = compose_projects(scan)
    units = build_units(scan, root.name, projects)
    anchors = collect(scan, units, projects)
    _, uses, _ = discover(scan, projects, units, anchors)
    joins, diagnostics = join(scan, units, anchors, uses)
    return (
        [(found.source, found.target, found.kind.value) for found in joins],
        [(diagnostic.code.value, diagnostic.message) for diagnostic in diagnostics],
    )


class TestDependencies(unittest.TestCase):
    def test_a_project_reference_is_a_dependency_wherever_the_project_sits(self) -> None:
        """A reference to a project at the repository root has no directory part to read."""
        edges, diagnostics = joined("join-dependencies")

        self.assertEqual(set(edges), {("src/Api", ".", "depends_on"), ("src/Api", "src/Db", "depends_on")})
        self.assertEqual(diagnostics, [])

    def test_a_package_depends_on_its_sibling_and_not_on_the_registry(self) -> None:
        """`@acme/api` is a unit here and `react` is not, so only one of the two is an edge."""
        edges, _ = joined("join-workspace")

        self.assertEqual(edges, [("packages/web", "packages/api", "depends_on")])

    def test_a_dependency_two_packages_answer_to_draws_nothing_and_names_both(self) -> None:
        """§6 rule 7: the name is ambiguous, and neither package wins by sorting first."""
        edges, diagnostics = joined("join-ambiguous-npm")

        self.assertEqual(edges, [])
        self.assertIn(
            (
                "ambiguous_key",
                "@acme/api in packages/web/package.json:4 names 2 units and draws nothing: packages/api, packages/api-next",
            ),
            diagnostics,
        )


class TestKinds(unittest.TestCase):
    def test_a_gateway_forwards_and_a_service_calls(self) -> None:
        edges, _ = joined("anchors-spring")

        self.assertIn(("gateway", "vets", "routes_to"), edges)
        self.assertIn(("vets", "gateway", "calls_http"), edges)

    def test_a_route_belongs_to_the_unit_whose_routes_the_file_declares(self) -> None:
        """An AppHost declares its own routes and configures every other service in the same file,
        so a variable it sets on one service is that service calling another, not a route."""
        edges, _ = joined("anchors-aspire")

        self.assertIn(("src/AppHost", "src/Basket.Api", "routes_to"), edges)
        self.assertIn(("src/Basket.Api", "src/Identity.Api", "calls_http"), edges)
        self.assertNotIn(("src/Basket.Api", "src/Identity.Api", "routes_to"), edges)

    def test_a_key_nobody_wrote_down_is_never_an_edge(self) -> None:
        """§6 rule 4: a key assembled at run time is T3, and only a literal one joins."""
        units = [Unit(id="a", dir="a", kind=UnitKind.NPM, aliases=("alpha",)), Unit(id="b", dir="b", kind=UnitKind.NPM)]
        guess = Anchor(
            family=AnchorFamily.SERVICE_NAMES,
            role=AnchorRole.USE,
            key="alpha",
            norm_key="alpha",
            file="b/app.ts",
            line=1,
            unit="b",
            tier=Tier.T3,
        )
        scan = Scan(FIXTURES / "join-workspace")

        self.assertEqual(join(scan, units, [guess])[0], [])
        self.assertEqual([found.target for found in join(scan, units, [replace(guess, tier=Tier.T1)])[0]], ["a"])


class TestResources(unittest.TestCase):
    def test_a_use_of_a_resource_is_an_edge_into_it_and_never_a_row(self) -> None:
        """The api names the cache in its environment: an arrow, where PR 5 left an unresolved use."""
        edges, diagnostics = joined("resources-compose")

        self.assertIn(("api", "resource:cache:cache", "uses"), edges)
        self.assertEqual([row for row in diagnostics if row[0] == "unresolved_use"], [])

    def test_a_configured_image_s_own_files_are_where_its_arrows_start(self) -> None:
        """A Prometheus's scrape list names the api: the Prometheus calls it (§6), from the directory
        that configures the image rather than from a unit."""
        edges, _ = joined("resources-compose")

        self.assertIn(("resource:api:metrics", "api", "calls_http"), edges)

    def test_a_gateway_routes_to_the_projects_its_chain_names(self) -> None:
        edges, _ = joined("resources-aspire")

        self.assertIn(("resource:gateway:edge", "src/Catalog.Api", "routes_to"), edges)
        self.assertIn(("src/Catalog.Api", "resource:db:postgres/db:catalogdb", "uses"), edges)

    def test_the_setting_says_what_a_use_is_for(self) -> None:
        """A registry is registered with, a configuration server fetched from, a collector reported to;
        anything else is used. A resource's own configuration calls what it names, unless the resource
        is a gateway, which routes (§5)."""
        scan = Scan(FIXTURES / "resources-compose")

        def use(source: str, target: str, setting: str, key: str = "host") -> Use:
            return Use(source, target, "app.yml", 1, 1, key, setting, AnchorFamily.CONFIGURATION)

        joins, _ = join(
            scan,
            [],
            [],
            [
                use("svc", "resource:api:tracing", "management.zipkin.tracing.endpoint"),
                use("svc", "resource:store:git", "spring.cloud.config.server.git.uri"),
                use("svc", "resource:api:consul", "spring.cloud.consul.host"),
                use("svc", "resource:db:pg", "spring.datasource.url"),
                use("svc", "resource:api:otel", "", "OTEL_EXPORTER_OTLP_ENDPOINT"),
                use("resource:gateway:edge", "svc", "AddYarp"),
                use("resource:api:metrics", "svc", "scrape_configs[0].static_configs[0].targets[0]"),
            ],
        )

        self.assertEqual(
            {(found.source, found.target): found.kind.value for found in joins},
            {
                ("svc", "resource:api:tracing"): "reports_to",
                ("svc", "resource:store:git"): "fetches_config",
                ("svc", "resource:api:consul"): "registers_with",
                ("svc", "resource:db:pg"): "uses",
                ("svc", "resource:api:otel"): "reports_to",
                ("resource:gateway:edge", "svc"): "routes_to",
                ("resource:api:metrics", "svc"): "calls_http",
            },
        )


class TestWhatDidNotJoin(unittest.TestCase):
    def test_a_name_two_units_answer_to_draws_nothing_and_names_both(self) -> None:
        """§6 rule 7: ambiguity is a different failure from a name nobody answers to."""
        edges, diagnostics = joined("join-ambiguous")

        self.assertEqual(edges, [])
        self.assertIn(
            ("ambiguous_key", "shared in docker-compose.yml:5 names 2 units and draws nothing: a, b"),
            diagnostics,
        )

    def test_a_name_no_unit_answers_to_is_reported(self) -> None:
        _, diagnostics = joined("join-ambiguous")

        self.assertIn(
            ("unresolved_use", "nowhere in docker-compose.yml:6 names no unit of this repository"),
            diagnostics,
        )

    def test_only_a_key_can_go_unread(self) -> None:
        """A service name, a port and an image are joined by name, so none of them is a definition
        anything looks up, and reporting one would bury the keys that are findings."""
        _, diagnostics = joined("join-ambiguous")

        self.assertEqual(
            sorted(message.split(" is set")[0] for code, message in diagnostics if code == "unused_definition"),
            ["MISSING_URL", "SHARED_URL"],
        )

    def test_an_annotation_is_a_role_and_not_a_name(self) -> None:
        _, diagnostics = joined("anchors-spring")

        self.assertEqual([message for _, message in diagnostics if message.startswith("@")], [])

    def test_a_use_that_resolves_from_a_file_of_no_unit_is_a_row(self) -> None:
        """The arrow has an end and no start: reported, not dropped."""
        edges, diagnostics = joined("join-no-unit")

        self.assertEqual(edges, [])
        self.assertIn(
            ("use_without_unit", "web in deploy/tools.yaml:12 names web from a file that belongs to no unit"),
            diagnostics,
        )

    def test_a_row_carries_the_place_the_lines_around_it_and_the_names_in_play(self) -> None:
        """§8: the shape a resolver would read one day, and what a reader needs to check a row by hand."""
        root = FIXTURES / "join-ambiguous"
        scan = Scan(root)
        projects = compose_projects(scan)
        units = build_units(scan, root.name, projects)
        _, diagnostics = join(scan, units, collect(scan, units, projects))
        (row,) = [d for d in diagnostics if d.code.value == "ambiguous_key"]

        self.assertEqual((row.file, row.line, row.key), ("docker-compose.yml", 5, "shared"))
        self.assertEqual(row.candidates, ("a", "b"))
        self.assertIn("SHARED_URL=http://shared:8080", "\n".join(row.context))
        self.assertLessEqual(len(row.context), 11)
        self.assertEqual(
            sorted(row.to_json()), ["candidates", "code", "context", "file", "key", "line", "message", "paths"]
        )


if __name__ == "__main__":
    unittest.main()
