"""The anchors: where a wiring key is declared or used, one fixture tree per kind of source."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.wiring import run, write_dump
from static_analyzer.wiring.anchors.configuration import entries
from static_analyzer.wiring.anchors.keys import Names, Owners, env_key, hosts_in, service_host
from static_analyzer.wiring.scan import MAX_CONFIGURATION_BYTES, Scan
from static_analyzer.wiring_results import Anchor, AnchorFamily, AnchorRole, Tier, Unit, UnitKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wiring"


def anchors_of(case: str) -> list[Anchor]:
    return run(StaticAnalysisResults(), FIXTURES / case).anchors


def rows(case: str) -> set[tuple[str, str, str, str, str]]:
    """Each anchor as (family, role, key, `file:line`, unit), which is what a reader checks."""
    return {
        (anchor.family.value, anchor.role.value, anchor.key, f"{anchor.file}:{anchor.line}", anchor.unit)
        for anchor in anchors_of(case)
    }


class TestDeployment(unittest.TestCase):
    def test_a_compose_service_declares_itself_its_image_and_its_ports(self) -> None:
        found = rows("anchors-compose")

        self.assertIn(("deployment", "def", "api", "docker-compose.yml:2", "api"), found)
        self.assertIn(("deployment", "def", "8080:8080", "docker-compose.yml:2", "api"), found)
        # A stock image belongs to no unit here; it is how a service nobody builds is declared.
        self.assertIn(("deployment", "def", "openzipkin/zipkin:3", "docker-compose.yml:6", ""), found)

    def test_an_environment_value_names_the_service_it_points_at(self) -> None:
        found = rows("anchors-compose")

        self.assertIn(("deployment", "def", "SERVICE_TRACING_URL", "docker-compose.yml:6", "api"), found)
        self.assertIn(("service_names", "use", "tracing", "docker-compose.yml:6", "api"), found)
        self.assertIn(("service_names", "use", "broker", "docker-compose.yml:15", "api"), found)
        self.assertIn(("deployment", "def", "QUEUE_HOST", "docker-compose.yml:15", "api"), found)

    def test_depends_on_is_a_use_of_the_name_it_waits_for(self) -> None:
        self.assertIn(("service_names", "use", "tracing", "docker-compose.yml:2", "api"), rows("anchors-compose"))

    def test_the_code_that_reads_the_key_is_the_other_half(self) -> None:
        found = rows("anchors-compose")

        self.assertIn(("configuration", "use", "SERVICE_TRACING_URL", "api/main.py:3", "api"), found)
        self.assertIn(("configuration", "use", "QUEUE_HOST", "api/main.py:4", "api"), found)

    def test_kubernetes_env_a_config_map_and_an_ingress(self) -> None:
        found = rows("anchors-kubernetes")

        self.assertIn(("deployment", "def", "CART_ADDR", "deploy/web.yaml:15", "web"), found)
        self.assertIn(("service_names", "use", "cart", "deploy/web.yaml:15", "web"), found)
        self.assertIn(("deployment", "def", "LEDGER_ADDR", "deploy/web.yaml:28", ""), found)
        self.assertIn(("service_names", "use", "ledger", "deploy/web.yaml:28", ""), found)
        self.assertIn(("service_names", "def", "/api/cart/{id}", "deploy/web.yaml:38", ""), found)

    def test_an_apphost_configures_the_project_its_statement_is_about(self) -> None:
        """The receiver of `.WithEnvironment(...)` is the resource the statement declares, not the builder."""
        found = rows("anchors-aspire")

        self.assertIn(("deployment", "def", "Identity__Url", "src/AppHost/Program.cs:7", "src/Basket.Api"), found)
        self.assertIn(("service_names", "use", "identity-api", "src/AppHost/Program.cs:7", "src/Basket.Api"), found)


class TestConfiguration(unittest.TestCase):
    def test_a_gateway_route_table_gives_a_name_and_a_template(self) -> None:
        found = rows("anchors-spring")
        gateway = "anchors-spring/gateway/src/main/resources/application.yml".split("anchors-spring/")[1]

        self.assertIn(("service_names", "use", "lb://vets-service", f"{gateway}:10", "gateway"), found)
        self.assertIn(("service_names", "def", "/api/vet/**", f"{gateway}:12", "gateway"), found)
        self.assertIn(("service_names", "use", "config-server", f"{gateway}:18", "gateway"), found)
        self.assertIn(("configuration", "use", "CONFIG_SERVER_URL", f"{gateway}:5", "gateway"), found)

    def test_a_setting_is_written_where_its_key_and_its_value_meet(self) -> None:
        """Two documents write `import:` and two settings hold this value; only their meeting is one line."""
        found = rows("anchors-spring")
        gateway = "gateway/src/main/resources/application.yml"

        self.assertIn(("service_names", "use", "config-server", f"{gateway}:21", "gateway"), found)

    def test_a_schema_directory_is_what_a_database_holds(self) -> None:
        """The place is the key: every unit keeping a `db` would otherwise write down the same one."""
        self.assertIn(
            (
                "data_access",
                "def",
                "vets/src/main/resources/db",
                "vets/src/main/resources/db/hsqldb/schema.sql:1",
                "vets",
            ),
            rows("anchors-spring"),
        )

    def test_a_service_name_in_code_and_the_role_a_unit_takes_on(self) -> None:
        found = rows("anchors-spring")
        application = "vets/src/main/java/org/acme/VetsApplication.java"

        self.assertIn(("service_names", "use", "@EnableDiscoveryClient", f"{application}:4", "vets"), found)
        self.assertIn(("service_names", "use", "customers-service", f"{application}:9", "vets"), found)
        self.assertIn(("service_names", "use", "http://api-gateway/api", f"{application}:12", "vets"), found)
        self.assertIn(("data_access", "use", "VectorStore", f"{application}:6", "vets"), found)

    def test_appsettings_tolerates_comments_and_a_trailing_comma(self) -> None:
        found = rows("anchors-dotnet")
        settings = "src/Catalog/appsettings.json"

        self.assertIn(("configuration", "def", "ConnectionStrings.EventBus", f"{settings}:4", "src/Catalog"), found)
        self.assertIn(("service_names", "use", "rabbitmq", f"{settings}:4", "src/Catalog"), found)
        self.assertIn(("service_names", "use", "identity-api", f"{settings}:9", "src/Catalog"), found)

    def test_a_connection_string_names_a_server_and_a_database(self) -> None:
        found = rows("anchors-dotnet")
        settings = "src/Catalog/appsettings.json"

        self.assertIn(("data_access", "use", "server=postgres", f"{settings}:5", "src/Catalog"), found)
        self.assertIn(("data_access", "use", "database=catalogdb", f"{settings}:5", "src/Catalog"), found)

    def test_a_local_server_is_the_machine_talking_to_itself(self) -> None:
        """`Server=localhost` names no unit, so it is not a thing a resource could be made of."""
        found = rows("anchors-dotnet")
        settings = "src/Catalog/appsettings.json"

        self.assertEqual([key for _, _, key, _, _ in found if key.startswith("server=local")], [])
        self.assertIn(("data_access", "use", "database=devdb", f"{settings}:6", "src/Catalog"), found)
        self.assertIn(("data_access", "use", "database=sqldb", f"{settings}:7", "src/Catalog"), found)

    def test_a_catalogue_is_data_rather_than_configuration(self) -> None:
        """A provider list declares no wiring and costs more to read than everything that does."""
        directory = Path(self.enterContext(tempfile.TemporaryDirectory())) / "repo"
        shutil.copytree(FIXTURES / "anchors-spring", directory)
        catalogue = directory / "vets" / "src" / "main" / "resources" / "providers.yml"
        catalogue.write_text(
            "".join(f"provider-{number}:\n  base_url: https://api-{number}.example.com\n" for number in range(4000))
        )
        # A catalogue is a catalogue in whatever notation it is written.
        (directory / "vets" / "src" / "main" / "resources" / "catalogue.properties").write_text(
            "".join(f"provider.{number}.url=https://api-{number}.example.com\n" for number in range(4000))
        )

        found = {anchor.file for anchor in run(StaticAnalysisResults(), directory).anchors}

        self.assertGreater(catalogue.stat().st_size, MAX_CONFIGURATION_BYTES)
        self.assertNotIn("vets/src/main/resources/providers.yml", found)
        self.assertNotIn("vets/src/main/resources/catalogue.properties", found)
        self.assertIn("vets/src/main/resources/application.properties", found)

    def test_a_value_is_not_read_inside_a_longer_one(self) -> None:
        """`30` occurs inside `300`, and a setting's own line is the one thing its value identifies."""
        path = "gateway/src/main/resources/application.yml"
        lines = {key: line for key, _, line in entries(Scan(FIXTURES / "anchors-spring"), path)}

        self.assertEqual(lines["upstream.timeout"], 24)
        self.assertEqual(lines["downstream.timeout"], 26)

    def test_a_schema_directory_is_named_by_a_file_someone_wrote(self) -> None:
        """Where no file declares the schema, the anchor still must not land on a generated one."""
        migrations = [
            anchor
            for anchor in anchors_of("anchors-dotnet")
            if anchor.family is AnchorFamily.DATA_ACCESS and anchor.role is AnchorRole.DEF
        ]

        self.assertEqual(
            [anchor.file for anchor in migrations],
            ["src/Catalog/Migrations/20231021004633_FixOrderitemseqSchema.cs"],
        )

    def test_a_service_name_is_not_a_store(self) -> None:
        """`lb://vets-service` names a unit; only a store's scheme makes a data-access anchor."""
        self.assertEqual(
            [
                anchor
                for anchor in anchors_of("anchors-spring")
                if anchor.family is AnchorFamily.DATA_ACCESS and anchor.key.startswith("spring.cloud")
            ],
            [],
        )


class TestRoutes(unittest.TestCase):
    def test_a_pass_names_its_upstream_and_what_the_upstream_resolves_to(self) -> None:
        """The include names an installed path, so the upstreams are found by the tail of it."""
        found = {(anchor.key, anchor.norm_key) for anchor in anchors_of("anchors-nginx")}

        self.assertIn(("proxy_pass http://django", "django"), found)
        self.assertIn(("uwsgi_pass tornado", "tornado"), found)
        self.assertIn(("proxy_pass http://api", "api"), found)
        self.assertIn(("proxy_pass http://api", "backend"), found)

    def test_a_location_is_a_template(self) -> None:
        routes = [anchor for anchor in anchors_of("anchors-nginx") if anchor.tier is Tier.T2]

        self.assertEqual(sorted(anchor.key for anchor in routes), ["/api", "/json", "/notify"])
        self.assertTrue(all(anchor.role is AnchorRole.DEF for anchor in routes))

    def test_a_reverse_proxy_route_names_the_resource_its_cluster_is(self) -> None:
        found = rows("anchors-aspire")

        self.assertIn(
            ("service_names", "def", "/basket-api/items/{id}", "src/AppHost/Program.cs:11", "src/AppHost"), found
        )
        self.assertIn(("service_names", "use", "basket-api", "src/AppHost/Program.cs:11", "src/AppHost"), found)

    def test_an_optional_parameter_is_a_parameter_and_not_a_query(self) -> None:
        """`{brandId?}` is one parameter; splitting the query off first would cut the route in half."""
        templates = {anchor.norm_key for anchor in anchors_of("anchors-aspire") if anchor.tier is Tier.T2}

        self.assertIn("/basket-api/items/by-brand/{}", templates)


class TestReaders(unittest.TestCase):
    def test_every_language_the_engines_support(self) -> None:
        found = {(anchor.key, anchor.file) for anchor in anchors_of("anchors-readers")}

        self.assertEqual(
            found,
            {
                ("ACCOUNTS_HOST", "svc/app.py"),
                ("PORT", "svc/app.py"),
                ("LEDGER_URL", "svc/app.ts"),
                ("MODE", "svc/app.ts"),
                ("accounts.host", "svc/App.java"),
                ("customers.host", "svc/App.java"),
                ("BALANCE_ADDR", "svc/App.java"),
                ("Ledger:Url", "svc/Program.cs"),
                ("Catalog", "svc/Program.cs"),
                ("CONTACTS_ADDR", "svc/main.go"),
            },
        )

    def test_a_generated_file_is_nobody_s_decision(self) -> None:
        """A designer file, a lockfile and a compiled proto are a tool's output, not a declaration."""
        self.assertNotIn("Ignored:Key", {anchor.key for anchor in anchors_of("anchors-readers")})
        self.assertNotIn("vets/pnpm-lock.yaml", {anchor.file for anchor in anchors_of("anchors-spring")})


class TestKeys(unittest.TestCase):
    def test_a_key_is_compared_the_way_its_framework_compares_it(self) -> None:
        self.assertEqual(env_key("spring.datasource.url"), "SPRINGDATASOURCEURL")
        self.assertEqual(env_key("SPRING_DATASOURCE_URL"), "SPRINGDATASOURCEURL")
        self.assertEqual(env_key("Identity__Url"), "IDENTITYURL")

    def test_a_value_names_a_service_only_where_it_names_one(self) -> None:
        self.assertEqual(service_host("http://config-server:8888"), "config-server")
        self.assertEqual(service_host("configserver:http://config-server:8888"), "config-server")
        self.assertEqual(service_host("lb://customers-service"), "customers-service")
        self.assertEqual(service_host("cart:7070"), "cart")
        self.assertEqual(service_host("accounts-db.default.svc.cluster.local"), "accounts-db")
        self.assertEqual(service_host("api-gateway", "SERVICE_HOST"), "api-gateway")

    def test_a_public_name_and_a_local_one_are_nobody_here(self) -> None:
        self.assertEqual(service_host("https://nango.dev/docs"), "")
        self.assertEqual(service_host("http://localhost:8888/"), "")
        self.assertEqual(service_host("registry"), "")
        self.assertEqual(service_host("${SERVICE_URL}"), "")

    def test_only_a_network_scheme_carries_a_host(self) -> None:
        """`maui://authcallback` is a deep link the phone answers, not a service anything reaches."""
        self.assertEqual(service_host("maui://authcallback"), "")
        self.assertEqual(service_host("grpc://ledger:9090"), "ledger")
        self.assertEqual(service_host("nats://broker:4222"), "broker")

    def test_several_hosts_in_one_value(self) -> None:
        self.assertEqual(hosts_in("kafka-1:9092,kafka-2:9092", "bootstrap-servers"), ["kafka-1", "kafka-2"])

    def test_a_file_belongs_to_the_unit_whose_directory_is_its_longest_ancestor(self) -> None:
        units = [
            Unit(id="src/api", dir="src/api", kind=UnitKind.NPM),
            Unit(id="src/api/inner", dir="src/api/inner", kind=UnitKind.NPM),
            Unit(id=".", dir=".", kind=UnitKind.PYTHON),
        ]
        owners = Owners(units)

        self.assertEqual(owners.of("src/api/inner/app.ts"), "src/api/inner")
        self.assertEqual(owners.of("src/api/app.ts"), "src/api")
        self.assertEqual(owners.of("tools/run.py"), ".")

    def test_a_name_two_units_answer_to_resolves_to_neither(self) -> None:
        names = Names(
            [
                Unit(id="a", dir="a", kind=UnitKind.NPM, aliases=("cart", "shared")),
                Unit(id="b", dir="b", kind=UnitKind.NPM, aliases=("shared",)),
            ]
        )

        self.assertEqual(names.unit_of("Cart"), "a")
        self.assertEqual(names.unit_of("shared"), "")
        self.assertEqual(names.unit_of("nothing"), "")


class TestDump(unittest.TestCase):
    def test_anchors_are_dumped_in_the_schema_the_graders_read(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        wiring = run(StaticAnalysisResults(), FIXTURES / "anchors-compose")

        write_dump(wiring, FIXTURES / "anchors-compose", directory)
        dumped = json.loads((directory / "anchors.json").read_text())

        self.assertEqual(sorted(dumped), ["anchors", "commit", "repo"])
        self.assertEqual(
            sorted(dumped["anchors"][0]),
            ["column", "family", "file", "key", "line", "norm_key", "role", "tier", "unit"],
        )
        self.assertEqual(len(dumped["anchors"]), len(wiring.anchors))
        # An anchor about no unit says so with the empty string the field always holds.
        self.assertEqual({type(anchor["unit"]) for anchor in dumped["anchors"]}, {str})

    def test_two_runs_over_one_tree_find_the_same_anchors(self) -> None:
        self.assertEqual(anchors_of("anchors-spring"), anchors_of("anchors-spring"))


if __name__ == "__main__":
    unittest.main()
