"""The resources: what the repository talks to and builds none of, one fixture per way of naming one."""

import json
import tempfile
import unittest
from pathlib import Path

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.wiring import run, write_dump
from static_analyzer.wiring.anchors import collect
from static_analyzer.wiring.compose import compose_projects
from static_analyzer.wiring.resources import Use, discover
from static_analyzer.wiring.scan import Scan
from static_analyzer.wiring.units import build_units
from static_analyzer.wiring.catalogue import classify, classify_image, classify_word
from static_analyzer.wiring_results import DiagnosticCode, Resource, ResourceKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wiring"


def resources_of(case: str) -> list[Resource]:
    return run(StaticAnalysisResults(), FIXTURES / case).resources


def rows(case: str) -> list[tuple[str, str, str, str]]:
    """Each resource as (key, kind, name, home unit), which is what a reader checks."""
    return [(one.key, one.kind.value, one.name, one.home_unit) for one in resources_of(case)]


def uses_of(case: str) -> list[Use]:
    root = FIXTURES / case
    scan = Scan(root)
    projects = compose_projects(scan)
    units = build_units(scan, root.name, projects)
    _, uses, _ = discover(scan, projects, units, collect(scan, units, projects))
    return uses


def unknown(case: str) -> list[str]:
    wiring = run(StaticAnalysisResults(), FIXTURES / case)
    return [d.message for d in wiring.diagnostics if d.code is DiagnosticCode.UNKNOWN_IMAGE_KIND]


class TestCompose(unittest.TestCase):
    def test_a_service_running_a_stock_image_is_a_thing_rather_than_a_box(self) -> None:
        """The image decides the kind and the repository decides the name (§4)."""
        self.assertEqual(
            rows("resources-compose"),
            [
                ("resource:api:metrics", "api", "metrics", ""),
                ("resource:broker:broker", "broker", "broker", ""),
                ("resource:cache:cache", "cache", "cache", "api"),
                ("resource:db:sql", "db", "sql", ""),
            ],
        )

    def test_a_service_this_repository_builds_is_never_a_resource(self) -> None:
        self.assertEqual([one.name for one in resources_of("resources-compose") if one.name == "api"], [])

    def test_the_only_unit_naming_a_resource_is_its_home(self) -> None:
        """`CACHE_URL` is the one line naming the cache, so the api is whose cache it is (§7)."""
        (cache,) = [one for one in resources_of("resources-compose") if one.kind is ResourceKind.CACHE]

        self.assertEqual(cache.home_unit, "api")
        self.assertEqual(cache.declared_by, ("docker-compose.yml",))

    def test_an_image_the_catalogue_does_not_know_is_a_row(self) -> None:
        self.assertEqual(
            unknown("resources-compose"),
            ["mystery in docker-compose.yml runs acme/mystery:1, which the catalogue does not know"],
        )

    def test_a_dockerfile_that_only_configures_an_image_is_that_image(self) -> None:
        """PetClinic's `docker/prometheus` is a Prometheus, not a unit (§6)."""
        (metrics,) = [one for one in resources_of("resources-compose") if one.name == "metrics"]

        self.assertEqual((metrics.kind, metrics.display_name), (ResourceKind.API, "Prometheus"))
        self.assertEqual(metrics.declared_by, ("monitoring/prometheus/Dockerfile",))

    def test_the_display_name_is_the_catalogue_s(self) -> None:
        names = {one.name: one.display_name for one in resources_of("resources-compose")}

        self.assertEqual(names, {"broker": "RabbitMQ", "cache": "Redis", "metrics": "Prometheus", "sql": "SQL Server"})


class TestAspire(unittest.TestCase):
    def test_a_constructor_says_what_a_thing_is(self) -> None:
        self.assertEqual(
            rows("resources-aspire"),
            [
                ("resource:db:postgres", "db", "postgres", "src/Catalog.Api"),
                ("resource:gateway:edge", "gateway", "edge", ""),
            ],
        )

    def test_what_a_server_holds_is_a_child_of_its_parents_kind_with_its_owner(self) -> None:
        (postgres,) = [one for one in resources_of("resources-aspire") if one.kind is ResourceKind.DB]

        self.assertEqual(
            [(child.key, child.kind.value, child.name, child.owner) for child in postgres.children],
            [("resource:db:postgres/db:catalogdb", "db", "catalogdb", "src/Catalog.Api")],
        )

    def test_a_name_a_unit_answers_to_is_a_box_and_not_a_resource(self) -> None:
        """`AddRabbitMQ("eventbus")` beside an `eventbus` project is how that box is deployed (§2)."""
        self.assertEqual([one for one in resources_of("resources-aspire") if "eventbus" in one.key], [])

    def test_a_declaration_that_keeps_no_handle_still_declares(self) -> None:
        """`builder.AddYarp("edge")` binds no variable and is a gateway all the same."""
        self.assertIn("resource:gateway:edge", [one.key for one in resources_of("resources-aspire")])

    def test_a_name_carrying_a_catalogue_word_declares_nothing_and_an_unknown_constructor_is_a_row(self) -> None:
        """`AddParameter("openai-key")` is a value and `vaultwarden` is not a Vault: the constructor decides."""
        keys = [one.key for one in resources_of("resources-aspire")]

        self.assertNotIn("resource:api:openai", keys)
        self.assertNotIn("resource:api:vaultwarden", keys)
        self.assertEqual(
            unknown("resources-aspire"),
            [
                "vaultwarden in src/AppHost/Program.cs is registered with AddAzureContainerAppEnvironment, "
                "which the catalogue does not know"
            ],
        )


class TestConfigured(unittest.TestCase):
    def test_every_key_naming_one_third_party_makes_one_resource(self) -> None:
        """Four keys across two files, one OpenAI: a key is a way of reaching a thing (§7)."""
        found = resources_of("resources-configured")

        self.assertEqual([(one.key, one.kind.value) for one in found], [("resource:api:openai", "api")])
        self.assertEqual(found[0].declared_by, ("svc/app.py", "svc/application.yml"))
        # The unit that names a thing uses it, whichever spelling the key is normalised by.
        self.assertEqual(found[0].home_unit, "svc")

    def test_a_repository_that_deploys_nothing_talks_to_nothing(self) -> None:
        """The same key, a `pg` driver and a vector store, in a library: options offered to whoever
        imports it, not a system (§7); a compose file running only a stock registry is a tool beside it."""
        self.assertEqual(resources_of("resources-library"), [])
        self.assertEqual(
            unknown("resources-library"),
            ["registry in tools/docker-compose.yml runs verdaccio/verdaccio:5, which the catalogue does not know"],
        )


class TestDriversAndClients(unittest.TestCase):
    def test_a_driver_dependency_declares_a_resource_used_by_that_unit(self) -> None:
        """Two modules depend on `hsqldb`: one HSQLDB, whose home is the directory the two share."""
        (hsqldb,) = [one for one in resources_of("resources-drivers") if one.name == "hsqldb"]

        self.assertEqual((hsqldb.kind, hsqldb.display_name), (ResourceKind.DB, "HSQLDB"))
        self.assertEqual(hsqldb.declared_by, ("customers/pom.xml", "genai/pom.xml"))
        self.assertEqual(hsqldb.home_unit, ".")

    def test_a_unit_nothing_runs_declares_no_resource_by_its_dependencies(self) -> None:
        """`tools` depends on `postgresql` and no manifest runs it: a library's option, not a system's store."""
        self.assertEqual([one for one in resources_of("resources-drivers") if one.name == "postgres"], [])

    def test_a_vector_store_client_declares_a_db(self) -> None:
        (store,) = [one for one in resources_of("resources-drivers") if one.name == "vectorstore"]

        self.assertEqual((store.kind, store.display_name, store.home_unit), (ResourceKind.DB, "Vector store", "genai"))

    def test_a_kubernetes_deployment_is_what_makes_a_key_a_fact_about_a_system(self) -> None:
        """No compose file and no AppHost here: the workload is what says this repository deploys."""
        self.assertIn("resource:api:openai", [one.key for one in resources_of("resources-drivers")])


class TestUses(unittest.TestCase):
    def test_the_setting_naming_a_resource_is_a_use_of_it_by_that_unit(self) -> None:
        """`CACHE_URL=redis://cache:6379` on the api is the api using the cache, at that line (§7)."""
        (use,) = [one for one in uses_of("resources-compose") if one.target == "resource:cache:cache"]

        self.assertEqual(
            (use.source, use.file, use.line, use.key, use.setting),
            ("api", "docker-compose.yml", 6, "cache", "CACHE_URL"),
        )

    def test_a_resource_carries_its_users(self) -> None:
        (hsqldb,) = [one for one in resources_of("resources-drivers") if one.name == "hsqldb"]

        self.assertEqual(hsqldb.users, ("customers", "genai"))

    def test_a_driver_names_the_one_thing_of_its_kind_the_deployment_runs(self) -> None:
        """`pg` in the api's manifest and `db` running `postgres:16` are one PostgreSQL, under the name
        the compose file gave it; `ioredis` beside two Redis services stays its own node, because a
        driver cannot say which of two it talks to (§7)."""
        found = {one.key: one for one in resources_of("resources-unified")}

        self.assertEqual(
            sorted(found), ["resource:cache:cachea", "resource:cache:cacheb", "resource:cache:redis", "resource:db:db"]
        )
        self.assertEqual(found["resource:db:db"].declared_by, ("api/package.json", "docker-compose.yml"))
        self.assertEqual((found["resource:db:db"].users, found["resource:db:db"].home_unit), (("api",), "api"))
        self.assertEqual(found["resource:cache:redis"].users, ("api",))
        uses = {(use.source, use.target, use.file, use.line) for use in uses_of("resources-unified")}
        self.assertIn(("api", "resource:db:db", "api/package.json", 4), uses)
        self.assertIn(("api", "resource:cache:redis", "api/package.json", 5), uses)

    def test_a_configuration_key_that_is_a_child_s_name_is_a_use_of_the_child(self) -> None:
        """A project reading `catalogdb` from its configuration uses the database of that name."""
        uses = {(use.source, use.target) for use in uses_of("resources-aspire")}

        self.assertIn(("src/Catalog.Api", "resource:db:postgres/db:catalogdb"), uses)

    def test_a_resource_s_own_configuration_naming_another_is_a_use_and_never_a_user(self) -> None:
        """A Grafana's data source names a Prometheus: an arrow between two resources, and the Prometheus
        keeps no user, because a resource is not a unit and has no box (§7)."""
        found = {one.key: one for one in resources_of("resources-compose")}

        self.assertEqual(found["resource:api:metrics"].users, ())


class TestNothingToFind(unittest.TestCase):
    def test_a_repository_that_declares_no_such_thing_has_none(self) -> None:
        self.assertEqual(resources_of("join-workspace"), [])
        self.assertEqual(resources_of("maven-modules"), [])


class TestCatalogue(unittest.TestCase):
    def test_one_catalogue_reads_every_vocabulary(self) -> None:
        """An image, a constructor, a scheme, a driver and a client type are ways of naming one thing (§7)."""
        self.assertEqual(classify("openzipkin/zipkin"), (ResourceKind.API, "Zipkin"))
        self.assertEqual(classify("Redis"), (ResourceKind.CACHE, "Redis"))
        self.assertEqual(classify("rabbitmq:3-management"), (ResourceKind.BROKER, "RabbitMQ"))
        self.assertEqual(classify("bitnami/postgresql-repmgr"), (ResourceKind.DB, "PostgreSQL"))
        self.assertEqual(classify("SimpleVectorStore"), (ResourceKind.DB, "Vector store"))
        self.assertEqual(classify("nothing-of-the-kind"), (None, ""))

    def test_an_image_is_read_by_every_path_segment(self) -> None:
        """`mssql/server` is the page's own example: the word is in the namespace, not the name."""
        self.assertEqual(classify_image("mssql/server"), (ResourceKind.DB, "SQL Server"))
        self.assertEqual(classify_image("prom/prometheus"), (ResourceKind.API, "Prometheus"))
        self.assertEqual(classify_image("otel/opentelemetry-collector-contrib"), (ResourceKind.API, "OpenTelemetry"))
        self.assertEqual(classify_image("acme/mystery"), (None, ""))

    def test_a_constructor_must_be_the_whole_word(self) -> None:
        self.assertEqual(classify_word("SqlServer"), (ResourceKind.DB, "SQL Server"))
        self.assertEqual(classify_word("AzureOpenAI"), (ResourceKind.API, "Azure OpenAI"))
        self.assertEqual(classify_word("AzureContainerAppEnvironment"), (None, ""))
        self.assertEqual(classify_word("openai-key"), (None, ""))


class TestDump(unittest.TestCase):
    def test_resources_are_dumped_in_the_schema_of_the_output_contract(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repository = FIXTURES / "resources-aspire"

        write_dump(run(StaticAnalysisResults(), repository), repository, directory)
        dumped = json.loads((directory / "resources.json").read_text())

        self.assertEqual(sorted(dumped), ["commit", "repo", "resources"])
        self.assertEqual(
            sorted(dumped["resources"][0]),
            ["children", "declared_by", "display_name", "home_unit", "key", "kind", "name", "users"],
        )
        self.assertEqual(sorted(dumped["resources"][0]["children"][0]), ["key", "kind", "name", "owner"])

    def test_two_runs_over_one_tree_find_the_same_resources(self) -> None:
        self.assertEqual(resources_of("resources-aspire"), resources_of("resources-aspire"))


if __name__ == "__main__":
    unittest.main()
