"""The unit table, one fixture tree per format behaviour it has to get right."""

import tempfile
import unittest
from pathlib import Path

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.wiring import run
from static_analyzer.wiring.compose import compose_projects, interpolate
from static_analyzer.wiring.images import build_directory, image_ref, read_dockerfile
from static_analyzer.wiring.scan import Scan
from static_analyzer.wiring_results import DiagnosticCode, Unit, UnitKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wiring"


def units_of(case: str) -> dict[str, Unit]:
    wiring = run(StaticAnalysisResults(), FIXTURES / case)
    return {unit.dir: unit for unit in wiring.units}


def diagnostics_of(case: str) -> list[tuple[DiagnosticCode, str]]:
    wiring = run(StaticAnalysisResults(), FIXTURES / case)
    return [(diagnostic.code, diagnostic.message) for diagnostic in wiring.diagnostics]


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class TestCompose(unittest.TestCase):
    def test_a_service_split_across_two_files_is_one_unit(self) -> None:
        """Names accumulate across the files, and a later file wins a scalar: `api-dev` over `api-box`."""
        units = units_of("compose-merge")

        self.assertEqual(list(units), ["api"])
        self.assertEqual(units["api"].kind, UnitKind.PYTHON)
        self.assertEqual(units["api"].manifest, "api/pyproject.toml")
        self.assertEqual(units["api"].aliases, ("acme-api", "acme/api", "api", "api-dev"))
        self.assertEqual(
            units["api"].builds,
            ("api/Dockerfile", "docker-compose.dev.yml", "docker-compose.yml"),
        )

    def test_variables_come_from_the_env_beside_the_file(self) -> None:
        units = units_of("compose-interpolation")

        self.assertEqual(list(units), ["src/worker"])
        self.assertEqual(units["src/worker"].kind, UnitKind.GO)
        self.assertIn("acme/worker", units["src/worker"].aliases)

    def test_interpolation_follows_the_specification(self) -> None:
        environment = {"TAG": "1.2"}
        self.assertEqual(interpolate("acme/api:${TAG}", environment), "acme/api:1.2")
        self.assertEqual(interpolate("redis:${REDIS_TAG:-7}", environment), "redis:7")
        self.assertEqual(interpolate("${DOCKER_REGISTRY-}api", environment), "api")
        self.assertEqual(interpolate("$TAG", environment), "1.2")
        self.assertEqual(interpolate("$$TAG", environment), "$TAG")
        self.assertEqual(interpolate("${UNSET}", environment), "${UNSET}")

    def test_the_error_and_alternative_forms_follow_the_specification(self) -> None:
        """`${VAR:?err}` would stop compose, so the text stays as written; `${VAR:+word}` is the word only when set."""
        environment = {"TAG": "1.2", "EMPTY": ""}
        self.assertEqual(interpolate("${UNSET:?tag is required}", environment), "${UNSET:?tag is required}")
        self.assertEqual(interpolate("${TAG:?tag is required}", environment), "1.2")
        self.assertEqual(interpolate("${TAG:+release}", environment), "release")
        self.assertEqual(interpolate("${UNSET:+release}", environment), "")
        self.assertEqual(interpolate("${EMPTY:+release}", environment), "")
        self.assertEqual(interpolate("${EMPTY+release}", environment), "release")
        self.assertEqual(interpolate("${EMPTY:-fallback}", environment), "fallback")
        self.assertEqual(interpolate("${EMPTY-fallback}", environment), "")

    def test_a_scalar_where_a_list_was_expected_is_one_entry(self) -> None:
        """`profiles: debug` and `ports: 8080` are mistakes compose tolerates; iterating their characters is not."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "compose.yaml", "services:\n  tool:\n    build: ./tool\n    profiles: debug\n    ports: 8080\n")
        write(root, "tool/Dockerfile", "FROM node:22\nCOPY . /app\n")
        write(root, "tool/package.json", '{"name": "tool"}')

        (project,) = compose_projects(Scan(root))

        self.assertEqual(project.services[0].profiles, ("debug",))
        self.assertEqual(project.services[0].ports, ("8080",))

    def test_a_service_line_is_where_its_key_is_written(self) -> None:
        """`api:` occurs inside an earlier image name; the service is declared on line 6."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(
            root,
            "compose.yaml",
            "services:\n  web:\n    image: registry/api:1\n    environment:\n      - API_URL=http://api\n"
            "  api:\n    build: ./api\n    environment:\n      PORT: 8080\n",
        )
        write(root, "api/Dockerfile", "FROM node:22\nCOPY . /app\n")
        write(root, "api/package.json", '{"name": "api"}')

        (project,) = compose_projects(Scan(root))
        by_name = {service.name: service for service in project.services}

        self.assertEqual(by_name["api"].line, 6)
        self.assertEqual(by_name["web"].line, 2)
        self.assertEqual([(entry.key, entry.line) for entry in by_name["web"].environment], [("API_URL", 5)])
        self.assertEqual([(entry.key, entry.line) for entry in by_name["api"].environment], [("PORT", 9)])

    def test_a_service_with_no_name_is_a_row_and_not_a_crash(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "compose.yaml", "services:\n  ~:\n    image: acme/x\n  api:\n    build: ./api\n")
        write(root, "api/Dockerfile", "FROM node:22\nCOPY . /app\n")
        write(root, "api/package.json", '{"name": "api"}')

        wiring = run(StaticAnalysisResults(), root)

        self.assertEqual([unit.dir for unit in wiring.units], ["api"])
        self.assertIn(
            (DiagnosticCode.UNREADABLE_MANIFEST, "compose.yaml declares a service with no name"),
            [(d.code, d.message) for d in wiring.diagnostics],
        )

    def test_a_profile_makes_a_variant(self) -> None:
        units = units_of("compose-profiles")

        self.assertEqual(units["app"].variant, ())
        self.assertEqual(units["tool"].variant, ("debug",))

    def test_extends_and_merge_keys_are_followed(self) -> None:
        units = units_of("compose-extends")

        self.assertEqual(list(units), ["web"])
        self.assertEqual(units["web"].aliases, ("web", "web-app", "web-box", "web-host"))

    def test_a_build_that_copies_nothing_builds_nothing_here(self) -> None:
        units = units_of("compose-environment-image")

        self.assertEqual(list(units), ["src/app"])
        self.assertEqual(
            diagnostics_of("compose-environment-image"),
            [
                (
                    DiagnosticCode.IGNORED_MANIFEST,
                    "docker-compose.yml builds nothing from this repository and runs none of its images",
                )
            ],
        )

    def test_a_service_running_an_image_a_workflow_builds_is_that_directory(self) -> None:
        units = units_of("compose-own-image")

        self.assertEqual(list(units), ["svc"])
        self.assertEqual(units["svc"].aliases, ("@acme/svc", "acme/svc", "svc", "svc-host"))

    def test_the_repository_is_named_by_its_remote_and_not_by_its_checkout(self) -> None:
        """A clone called `wt-3` still owns the image named after the repository (§6)."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "wt-3"
        write(root, ".git/HEAD", "ref: refs/heads/main\n")
        write(root, ".git/config", '[remote "origin"]\n\turl = https://github.com/acme/thing.git\n')
        write(root, "pyproject.toml", "[project]\nname = 'thing-lib'\n")
        write(root, "docker-compose.yml", "services:\n  app:\n    image: ghcr.io/acme/thing:1\n")

        wiring = run(StaticAnalysisResults(), root)

        self.assertEqual([(unit.dir, unit.aliases) for unit in wiring.units], [(".", ("app", "thing-lib"))])

    def test_a_compose_project_is_the_files_in_one_directory(self) -> None:
        (project,) = compose_projects(Scan(FIXTURES / "compose-merge"))
        self.assertEqual(project.files, ("docker-compose.yml", "docker-compose.dev.yml"))
        self.assertEqual([service.name for service in project.services], ["api", "db"])


class TestBuildManifests(unittest.TestCase):
    def test_a_maven_parent_tags_one_image_per_module(self) -> None:
        units = units_of("maven-modules")

        self.assertEqual(sorted(units), ["gateway", "service"])
        self.assertEqual(units["gateway"].kind, UnitKind.MAVEN)
        self.assertEqual(units["gateway"].aliases, ("acme-gateway", "acme/acme-gateway", "gateway", "gateway-box"))
        self.assertEqual(units["service"].aliases, ("acme-service", "service"))

    def test_an_aspire_apphost_names_the_projects_it_registers(self) -> None:
        units = units_of("dotnet-aspire")

        self.assertEqual(sorted(units), ["frontend", "src/AppHost", "src/Basket.Api", "src/Web"])
        self.assertEqual(units["src/Basket.Api"].aliases, ("Basket.Api", "Basket.Service", "basket-api"))
        self.assertEqual(units["src/Web"].aliases, ("Web", "web-frontend"))
        self.assertEqual(units["frontend"].aliases, ("storefront", "storefront-ui"))
        self.assertEqual(units["frontend"].kind, UnitKind.NPM)

    def test_a_workspace_lists_its_members_and_nothing_nested_inside_one(self) -> None:
        units = units_of("npm-workspaces")

        self.assertEqual(sorted(units), [".", "packages/api", "packages/ui"])
        self.assertEqual(units["."].aliases, ("acme",))

    def test_every_other_manifest_declares_its_directory(self) -> None:
        units = units_of("other-manifests")

        self.assertEqual(
            {directory: (unit.kind, unit.aliases) for directory, unit in units.items()},
            {
                "elx": (UnitKind.ELIXIR, ("acme_elx",)),
                "gem": (UnitKind.RUBY, ("acme-gem",)),
                "php": (UnitKind.PHP, ("acme/php",)),
                "services/loader": (UnitKind.PYTHON, ()),
                "services/reader": (UnitKind.PYTHON, ("reader",)),
                "services/writer": (UnitKind.PYTHON, ("writer",)),
                "tools/cli": (UnitKind.RUST, ("acme-cli",)),
            },
        )

    def test_a_requirements_file_declares_a_unit_where_code_or_a_dockerfile_says_so(self) -> None:
        """A `src/` layout keeps its code below the file; a docs tree has a requirements file and no code."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "svc/requirements.txt", "flask\n")
        write(root, "svc/src/app/main.py", "print(1)\n")
        write(root, "runner/requirements.txt", "locust\n")
        write(root, "runner/Dockerfile", "FROM python:3.12\nCOPY requirements.txt .\n")
        write(root, "docs/requirements.txt", "sphinx\n")
        write(root, "docs/index.md", "# docs\n")

        self.assertEqual([unit.dir for unit in run(StaticAnalysisResults(), root).units], ["runner", "svc"])


class TestDeploymentTopology(unittest.TestCase):
    def test_a_workload_and_the_service_selecting_it_name_the_unit(self) -> None:
        units = units_of("kubernetes")

        self.assertEqual(list(units), ["api"])
        self.assertEqual(units["api"].aliases, ("acme-api-pkg", "acme/api", "api", "api-deploy", "api-svc"))

    def test_a_skaffold_context_above_the_file_resolves_to_the_jib_project(self) -> None:
        units = units_of("skaffold-jib")

        self.assertEqual(list(units), ["services/ledger"])
        self.assertEqual(units["services/ledger"].kind, UnitKind.MAVEN)
        self.assertEqual(units["services/ledger"].aliases, ("ledger", "ledger-service"))

    def test_a_chart_deploying_one_image_of_this_repository_names_that_unit(self) -> None:
        units = units_of("helm-chart")

        self.assertEqual(list(units), ["web"])
        self.assertIn("web-chart", units["web"].aliases)


class TestWhatDeclaresNothing(unittest.TestCase):
    def test_a_project_template_is_not_a_system(self) -> None:
        self.assertEqual(list(units_of("template-tree")), ["app"])
        self.assertEqual(
            diagnostics_of("template-tree"),
            [(DiagnosticCode.IGNORED_MANIFEST, "templates/starter is a project template, not a system")],
        )

    def test_tests_declare_nothing_and_latest_is_not_a_test(self) -> None:
        units = units_of("test-shapes")

        self.assertEqual(list(units), ["latest"])
        self.assertEqual(
            diagnostics_of("test-shapes"),
            [
                (DiagnosticCode.IGNORED_MANIFEST, "Acme.Tests is a test directory"),
                (DiagnosticCode.IGNORED_MANIFEST, "docker-compose.test.yml is named as a test"),
                (DiagnosticCode.IGNORED_MANIFEST, "tests is a test directory"),
            ],
        )

    def test_a_deployment_file_cannot_make_the_repository_root_a_unit(self) -> None:
        """A CI image that mounts the tree names the repository; only a manifest here builds it."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(
            root,
            "docker-compose.yml",
            "services:\n  ci:\n    build:\n      context: .\n      dockerfile: ci/Dockerfile\n",
        )
        write(root, "ci/Dockerfile", "FROM ubuntu:22.04\nCOPY . /code\n")
        write(root, "app.py", "print(1)\n")

        wiring = run(StaticAnalysisResults(), root)

        self.assertEqual([unit.dir for unit in wiring.units], [])
        self.assertEqual([diagnostic.code for diagnostic in wiring.diagnostics], [DiagnosticCode.IGNORED_MANIFEST])
        self.assertIn("builds the repository itself", wiring.diagnostics[0].message)

    def test_a_dockerfile_named_under_a_pruned_directory_still_builds(self) -> None:
        """The Go standard layout: `build/package/Dockerfile` with the module at the root."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(
            root,
            "docker-compose.yml",
            "services:\n  app:\n    build:\n      context: .\n      dockerfile: build/package/Dockerfile\n",
        )
        write(root, "build/package/Dockerfile", "FROM golang:1.23\nCOPY . /src\n")
        write(root, "go.mod", "module acme/thing\n")
        write(root, "main.go", "package main\n")

        wiring = run(StaticAnalysisResults(), root)

        self.assertEqual(
            [(unit.dir, unit.kind, unit.aliases) for unit in wiring.units], [(".", UnitKind.GO, ("acme/thing", "app"))]
        )

    def test_the_root_is_a_unit_when_a_manifest_there_builds_it(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(
            root,
            "docker-compose.yml",
            "services:\n  app:\n    build:\n      context: .\n      dockerfile: docker/Dockerfile\n",
        )
        write(root, "docker/Dockerfile", "FROM python:3.12\nCOPY . /app\n")
        write(root, "pyproject.toml", "[project]\nname = 'the-app'\n")

        wiring = run(StaticAnalysisResults(), root)

        self.assertEqual([(unit.dir, unit.aliases) for unit in wiring.units], [(".", ("app", "the-app"))])


class TestKind(unittest.TestCase):
    def test_a_directory_of_one_dockerfile_takes_the_kind_that_builds_it(self) -> None:
        """A compose file runs it and the Dockerfile builds it; the build says what the unit is."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "docker-compose.yml", "services:\n  loader:\n    build: ./jobs/loader\n")
        write(root, "jobs/loader/Dockerfile", "FROM python:3.12\nCOPY load.py /srv/\n")
        write(root, "jobs/loader/load.py", "print(1)\n")

        (unit,) = run(StaticAnalysisResults(), root).units

        self.assertEqual(unit.dir, "jobs/loader")
        self.assertEqual(unit.kind, UnitKind.DOCKER)
        self.assertEqual(unit.manifest, "jobs/loader/Dockerfile")
        self.assertEqual(unit.aliases, ("loader",))

    def test_a_dockerfile_that_copies_only_configuration_configures_an_image_and_is_no_unit(self) -> None:
        """A Grafana with its dashboards is a Grafana: the image runs, the directory holds its settings (§6)."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "docker-compose.yml", "services:\n  grafana:\n    build: ./monitoring/grafana\n")
        write(
            root,
            "monitoring/grafana/Dockerfile",
            "FROM grafana/grafana:11\nADD ./dashboards /var/lib/grafana/dashboards\nCOPY grafana.ini /etc/\n",
        )
        write(root, "monitoring/grafana/grafana.ini", "[server]\n")
        write(root, "monitoring/grafana/dashboards/home.json", "{}\n")

        wiring = run(StaticAnalysisResults(), root)

        self.assertEqual(wiring.units, [])
        self.assertEqual(
            [(d.code, d.message, d.paths) for d in wiring.diagnostics],
            [
                (
                    DiagnosticCode.CONFIGURED_IMAGE,
                    "monitoring/grafana/Dockerfile configures grafana/grafana:11 and builds no code of this repository",
                    ("monitoring/grafana/Dockerfile",),
                )
            ],
        )

    def test_a_bind_mount_reads_the_context_like_a_copy(self) -> None:
        """`docker init` writes a Dockerfile that never copies and builds from a bind mount."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "docker-compose.yml", "services:\n  app:\n    build: ./app\n")
        write(
            root,
            "app/Dockerfile",
            "FROM golang:1.23 AS build\nRUN --mount=type=bind,source=go.mod,target=go.mod go mod download\n"
            "RUN --mount=type=bind,target=. go build -o /bin/server .\n",
        )
        write(root, "app/go.mod", "module acme/app\n")
        write(root, "app/main.go", "package main\n")

        self.assertEqual([unit.dir for unit in run(StaticAnalysisResults(), root).units], ["app"])


class TestAmbiguity(unittest.TestCase):
    def test_two_units_answering_to_one_name_are_reported(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "package.json", '{"name": "root", "workspaces": ["packages/*"]}')
        write(root, "packages/one/package.json", '{"name": "acme-thing"}')
        write(root, "packages/two/package.json", '{"name": "Acme.Thing"}')

        wiring = run(StaticAnalysisResults(), root)

        self.assertEqual(
            [(diagnostic.code, diagnostic.message) for diagnostic in wiring.diagnostics],
            [(DiagnosticCode.AMBIGUOUS_ALIAS, "acme-thing is a name of packages/one and packages/two")],
        )

    def test_one_image_two_directories_resolves_to_neither(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "a/Dockerfile", "FROM node:22\nCOPY . /srv\n")
        write(root, "a/package.json", '{"name": "a"}')
        write(root, "b/Dockerfile", "FROM node:22\nCOPY . /srv\n")
        write(root, "b/package.json", '{"name": "b"}')
        write(
            root,
            "docker-compose.yml",
            "services:\n  a:\n    build: ./a\n    image: acme/shared\n"
            "  b:\n    build: ./b\n    image: acme/shared\n"
            "  runner:\n    image: acme/shared\n",
        )

        wiring = run(StaticAnalysisResults(), root)
        units = {unit.dir: unit for unit in wiring.units}

        self.assertEqual(sorted(units), ["a", "b"])
        self.assertNotIn("acme/shared", units["a"].aliases)
        self.assertEqual([diagnostic.code for diagnostic in wiring.diagnostics], [DiagnosticCode.AMBIGUOUS_IMAGE])


class TestImages(unittest.TestCase):
    def test_a_reference_loses_its_host_tag_and_digest(self) -> None:
        self.assertEqual(image_ref("docker.io/openzipkin/zipkin:3").repository, "openzipkin/zipkin")
        self.assertEqual(image_ref("docker.io/openzipkin/zipkin:3").tag, "3")
        self.assertEqual(image_ref("postgres@sha256:abc").repository, "postgres")
        self.assertEqual(image_ref("acme/api:${TAG}").repository, "")

    def test_the_directory_a_build_names_is_where_its_dockerfile_sits(self) -> None:
        """A Dockerfile beside code builds that code; a `docker/` folder of Dockerfiles builds its context."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "src/cart/Dockerfile", "FROM node:22\n")
        write(root, "src/cart/package.json", '{"name": "cart"}')
        write(root, "docker/Dockerfile", "FROM python:3.12\n")
        write(root, "docker/grafana/Dockerfile", "FROM grafana/grafana\n")
        write(root, "docker/grafana/grafana.ini", "[server]\n")
        scan = Scan(root)

        self.assertEqual(build_directory(scan, "", "src/cart/Dockerfile"), "src/cart")
        self.assertEqual(build_directory(scan, "docker/grafana", "docker/grafana/Dockerfile"), "docker/grafana")
        self.assertEqual(build_directory(scan, "", "docker/Dockerfile"), "")
        self.assertEqual(build_directory(scan, "services", "docker/Dockerfile"), "services")
        self.assertEqual(build_directory(scan, "src/api", ""), "src/api")

    def test_a_dockerfile_that_copies_only_from_another_stage_copies_nothing_here(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "Dockerfile", "FROM golang AS build\nFROM alpine\nCOPY --from=build /out /usr/bin\n")
        write(root, "own/Dockerfile", "FROM alpine\nADD https://example.com/x.tar.gz /tmp\nCOPY app/ /srv\n")
        write(root, "own/app/main.py", "print(1)\n")

        scan = Scan(root)
        self.assertFalse(read_dockerfile(scan, "Dockerfile").copies_context)
        self.assertEqual(read_dockerfile(scan, "Dockerfile").base_image, "golang")
        self.assertTrue(read_dockerfile(scan, "own/Dockerfile").copies_context)
        self.assertTrue(read_dockerfile(scan, "own/Dockerfile").copies_source)

    def test_a_scalar_where_kubernetes_expects_a_mapping_is_a_row_and_not_a_crash(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "docker-compose.yml", "services:\n  api:\n    build: ./api\n    image: acme/api\n")
        write(root, "api/Dockerfile", "FROM node:22\nCOPY . /srv\n")
        write(root, "api/package.json", '{"name": "api"}')
        write(
            root,
            "deploy/api.yaml",
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\nspec:\n  template:\n    metadata: broken\n"
            "    spec:\n      containers:\n        - name: api\n          image: acme/api\n",
        )

        wiring = run(StaticAnalysisResults(), root)

        self.assertEqual([unit.dir for unit in wiring.units], ["api"])
        self.assertIn("api", {unit.dir: unit for unit in wiring.units}["api"].aliases)


if __name__ == "__main__":
    unittest.main()
