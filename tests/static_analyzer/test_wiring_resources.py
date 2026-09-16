"""The resources: what the repository talks to and builds none of, one fixture per way of naming one."""

import json
import tempfile
import unittest
from pathlib import Path

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.wiring import run, write_dump
from static_analyzer.wiring.resources import classify
from static_analyzer.wiring_results import Resource, ResourceKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wiring"


def resources_of(case: str) -> list[Resource]:
    return run(StaticAnalysisResults(), FIXTURES / case).resources


def rows(case: str) -> list[tuple[str, str, str, str]]:
    """Each resource as (key, kind, name, home), which is what a reader checks."""
    return [(one.key, one.kind.value, one.name, one.home) for one in resources_of(case)]


class TestCompose(unittest.TestCase):
    def test_a_service_running_a_stock_image_is_a_thing_rather_than_a_box(self) -> None:
        """The image decides the kind and the repository decides the name (§4)."""
        self.assertEqual(
            rows("resources-compose"),
            [
                ("resource:broker:broker", "broker", "broker", ""),
                ("resource:cache:cache", "cache", "cache", "api"),
            ],
        )

    def test_a_service_this_repository_builds_is_never_a_resource(self) -> None:
        self.assertEqual([one.name for one in resources_of("resources-compose") if one.name == "api"], [])

    def test_the_only_unit_naming_a_resource_is_its_home(self) -> None:
        """`CACHE_URL` is the one line naming the cache, so the api is whose cache it is (§7)."""
        (cache,) = [one for one in resources_of("resources-compose") if one.kind is ResourceKind.CACHE]

        self.assertEqual(cache.home, "api")
        self.assertEqual(cache.declared_by, ("docker-compose.yml",))


class TestAspire(unittest.TestCase):
    def test_a_constructor_says_what_a_thing_is(self) -> None:
        self.assertEqual(
            rows("resources-aspire"),
            [
                ("resource:db:postgres", "db", "postgres", ""),
                ("resource:gateway:edge", "gateway", "edge", ""),
            ],
        )

    def test_what_a_server_holds_is_a_child_of_its_parents_kind(self) -> None:
        (postgres,) = [one for one in resources_of("resources-aspire") if one.kind is ResourceKind.DB]

        self.assertEqual(
            [(child.key, child.kind.value, child.name) for child in postgres.children],
            [("resource:db:postgres/db:catalogdb", "db", "catalogdb")],
        )

    def test_a_name_a_unit_answers_to_is_a_box_and_not_a_resource(self) -> None:
        """`AddRabbitMQ("eventbus")` beside an `eventbus` project is how that box is deployed (§2)."""
        self.assertEqual([one for one in resources_of("resources-aspire") if "eventbus" in one.key], [])

    def test_a_declaration_that_keeps_no_handle_still_declares(self) -> None:
        """`builder.AddYarp("edge")` binds no variable and is a gateway all the same."""
        self.assertIn("resource:gateway:edge", [one.key for one in resources_of("resources-aspire")])


class TestConfigured(unittest.TestCase):
    def test_every_key_naming_one_third_party_makes_one_resource(self) -> None:
        """Four keys across two files, one OpenAI: a key is a way of reaching a thing (§7)."""
        found = resources_of("resources-configured")

        self.assertEqual([(one.key, one.kind.value) for one in found], [("resource:api:openai", "api")])
        self.assertEqual(found[0].declared_by, ("svc/app.py", "svc/application.yml"))


class TestNothingToFind(unittest.TestCase):
    def test_a_repository_that_declares_no_such_thing_has_none(self) -> None:
        self.assertEqual(resources_of("join-workspace"), [])
        self.assertEqual(resources_of("maven-modules"), [])


class TestCatalogue(unittest.TestCase):
    def test_one_catalogue_reads_three_vocabularies(self) -> None:
        """An image, a constructor and a scheme are three ways of naming the same thing (§7)."""
        self.assertEqual(classify("openzipkin/zipkin"), (ResourceKind.API, "Zipkin"))
        self.assertEqual(classify("Redis"), (ResourceKind.CACHE, "Redis"))
        self.assertEqual(classify("rabbitmq:3-management"), (ResourceKind.BROKER, "RabbitMQ"))
        self.assertEqual(classify("bitnami/postgresql-repmgr"), (ResourceKind.DB, "PostgreSQL"))
        self.assertEqual(classify("nothing-of-the-kind"), (None, ""))


class TestDump(unittest.TestCase):
    def test_resources_are_dumped_in_the_schema_of_the_output_contract(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repository = FIXTURES / "resources-aspire"

        write_dump(run(StaticAnalysisResults(), repository), repository, directory)
        dumped = json.loads((directory / "resources.json").read_text())

        self.assertEqual(sorted(dumped), ["commit", "repo", "resources"])
        self.assertEqual(sorted(dumped["resources"][0]), ["children", "declared_by", "home", "key", "kind", "name"])
        self.assertEqual(sorted(dumped["resources"][0]["children"][0]), ["key", "kind", "name", "owner"])

    def test_two_runs_over_one_tree_find_the_same_resources(self) -> None:
        self.assertEqual(resources_of("resources-aspire"), resources_of("resources-aspire"))


if __name__ == "__main__":
    unittest.main()
