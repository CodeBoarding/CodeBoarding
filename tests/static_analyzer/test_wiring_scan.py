import tempfile
import unittest
from pathlib import Path

from static_analyzer.wiring.scan import (
    FileKind,
    MAX_BYTES,
    Scan,
    classify,
    listing,
    mapping,
    parse_dotenv,
    repo_path,
)
from static_analyzer.wiring_results import DiagnosticCode

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wiring"


def write(root: Path, path: str, text: str = "") -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


class TestClassify(unittest.TestCase):
    def test_names_the_pass_knows(self) -> None:
        for directory, name, kind in (
            ("", "docker-compose.yml", FileKind.COMPOSE),
            ("", "compose.yaml", FileKind.COMPOSE),
            ("deploy", "docker-compose.prod.yaml", FileKind.COMPOSE),
            ("", "skaffold.yaml", FileKind.SKAFFOLD),
            ("", "Dockerfile", FileKind.DOCKERFILE),
            ("", "Dockerfile.centos7", FileKind.DOCKERFILE),
            ("", "api.dockerfile", FileKind.DOCKERFILE),
            ("", "pom.xml", FileKind.MAVEN),
            ("src", "Basket.API.csproj", FileKind.DOTNET_PROJECT),
            ("", "eShop.slnx", FileKind.DOTNET_SOLUTION),
            ("", "pyproject.toml", FileKind.PYTHON_PROJECT),
            ("", "requirements-dev.txt", FileKind.REQUIREMENTS),
            ("", "go.mod", FileKind.GO_MODULE),
            ("", "Cargo.toml", FileKind.CARGO),
            ("chart", "Chart.yaml", FileKind.HELM_CHART),
            ("chart", "values.yaml", FileKind.HELM_VALUES),
            ("src/main/resources", "application-docker.yml", FileKind.SPRING_CONFIG),
            ("", ".env.production", FileKind.DOTENV),
            ("deploy", "deployment.yaml", FileKind.YAML),
        ):
            self.assertIs(classify(directory, name), kind, f"{directory}/{name}")

    def test_source_and_prose_are_never_classified(self) -> None:
        for name in ("main.py", "Program.cs", "README.md", "app.ts", "styles.css"):
            self.assertIsNone(classify("src", name), name)

    def test_only_the_workflows_directory_is_read_under_dot_github(self) -> None:
        self.assertIs(classify(".github/workflows", "release.yml"), FileKind.WORKFLOW)
        self.assertIsNone(classify(".github", "dependabot.yml"))
        self.assertIsNone(classify(".github/actions/build", "action.yml"))


class TestPaths(unittest.TestCase):
    def test_a_path_is_spelled_from_the_repository_root(self) -> None:
        self.assertEqual(repo_path("docker", "../src/api"), "src/api")
        self.assertEqual(repo_path("services/ledger", "../../"), "")
        self.assertEqual(repo_path("", "./web/Dockerfile"), "web/Dockerfile")

    def test_what_is_not_a_path_here_is_nothing(self) -> None:
        self.assertEqual(repo_path("", "../outside"), "")
        self.assertEqual(repo_path("", "https://github.com/acme/thing.git"), "")
        self.assertEqual(repo_path("", "/etc/hosts"), "")
        self.assertEqual(repo_path("", ""), "")

    def test_dotenv_reads_what_compose_reads(self) -> None:
        values = parse_dotenv('# a comment\nexport TAG=1.2\nQUOTED="a b"\nEMPTY=\nNOT_A_LINE\n')
        self.assertEqual(values, {"TAG": "1.2", "QUOTED": "a b", "EMPTY": ""})


class TestWalk(unittest.TestCase):
    def test_the_allowlist_decides_what_is_kept(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "pyproject.toml", "[project]\nname = 'app'\n")
        write(root, "src/app.py", "x = 1")
        write(root, "node_modules/left-pad/package.json", "{}")
        write(root, "dist/package.json", "{}")
        write(root, "tests/docker-compose.yml", "services: {}")
        write(root, "Acme.Tests/Acme.Tests.csproj", "<Project />")
        write(root, ".github/workflows/ci.yml", "jobs: {}")
        write(root, ".github/dependabot.yml", "updates: []")
        write(root, ".devcontainer/docker-compose.yml", "services: {}")
        write(root, "latest/go.mod", "module acme/latest")

        scan = Scan(root)

        self.assertEqual(
            sorted(scan.files),
            [".github/workflows/ci.yml", "latest/go.mod", "pyproject.toml"],
        )

    def test_a_file_named_as_a_test_is_reported_rather_than_read(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "docker-compose.test.yml", "services: {}")
        write(root, "docker-compose.yml", "services: {}")

        scan = Scan(root)

        self.assertEqual(sorted(scan.files), ["docker-compose.yml"])
        self.assertEqual(
            [(d.code, d.paths) for d in scan.diagnostics],
            [(DiagnosticCode.IGNORED_MANIFEST, ("docker-compose.test.yml",))],
        )

    def test_what_is_named_as_a_test_and_what_only_looks_like_one(self) -> None:
        """`ui-tests` is a test directory, `latest` is not, and `spec.yaml` is a contract."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, ".github/workflows/ui-tests/Dockerfile", "FROM cypress/included:14\n")
        write(root, ".github/workflows/tests.yaml", "jobs: {}\n")
        write(root, "docs/spec.yaml", "openapi: 3.1.0\n")
        write(root, "latest/go.mod", "module acme/latest\n")
        write(root, "src/app.spec.ts", "describe('x', () => {})")
        write(root, "src/package.json", '{"name": "app"}')

        scan = Scan(root)

        self.assertEqual(
            sorted(scan.files),
            [".github/workflows/tests.yaml", "docs/spec.yaml", "latest/go.mod", "src/package.json"],
        )

    def test_under_dot_github_only_the_workflows_are_walked(self) -> None:
        """A script beside the workflows is the forge's tooling, and a key it reads is nobody's deployment."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, ".github/workflows/build.yml", "jobs: {}\n")
        write(root, ".github/scripts/seo.py", "import os\nKEY = os.environ['OPENAI_API_KEY']\n")
        write(root, "pyproject.toml", "[project]\nname = 'thing'\n")

        scan = Scan(root)

        self.assertFalse(scan.has_dir(".github/scripts"))
        self.assertIn(
            (".github/scripts is inside a hidden directory", (".github/scripts",)),
            [(d.message, d.paths) for d in scan.diagnostics],
        )

    def test_a_test_directory_is_plural_in_either_spelling_and_a_singular_suffix_is_a_name(self) -> None:
        """Losing a unit costs a box; keeping one costs an entry nothing joins."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for directory in ("ABTest", "plugin-chart-paired-t-test", "contests", "ui-tests", "Acme.Tests", "E2ETests"):
            write(root, f"{directory}/package.json", '{"name": "x"}')

        scan = Scan(root)

        self.assertEqual(
            sorted(scan.files),
            ["ABTest/package.json", "contests/package.json", "plugin-chart-paired-t-test/package.json"],
        )

    def test_every_directory_the_walk_leaves_out_is_a_row(self) -> None:
        """One row per excluded place, so nothing is dropped silently — the repository's `.git` aside."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for directory in ("node_modules", "testing", "fixtures", "mocks", "e2e", "Acme.Tests", ".idea", ".git", "src"):
            write(root, f"{directory}/package.json", '{"name": "x"}')

        scan = Scan(root)

        self.assertEqual(sorted(scan.files), ["src/package.json"])
        self.assertEqual(
            sorted(d.message for d in scan.diagnostics if d.code is DiagnosticCode.IGNORED_MANIFEST),
            [
                ".idea is a hidden directory",
                "Acme.Tests is a test directory",
                "e2e is a test directory",
                "fixtures is a test directory",
                "mocks is a test directory",
                "node_modules is a dependency or build directory",
                "testing is a test directory",
            ],
        )

    def test_a_vendored_client_library_is_not_scanned(self) -> None:
        """LibMan installs client libraries into `wwwroot/lib`, so what lands there is vendored."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "src/WebApp/wwwroot/lib/bootstrap/Gemfile", "gem 'jekyll'\n")
        write(root, "src/WebApp/WebApp.csproj", "<Project />")

        self.assertEqual(sorted(Scan(root).files), ["src/WebApp/WebApp.csproj"])

    def test_gitignore_hides_a_file_and_codeboardingignore_hides_a_place(self) -> None:
        """Why both: a local untracked `.env` must not make a run disagree with the same commit in CI."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, ".gitignore", ".env\n")
        write(root, ".codeboarding/.codeboardingignore", "vendor-ui/\n*.config.*\n")
        write(root, ".env", "SECRET=1")
        write(root, "app/package.json", '{"name": "app"}')
        write(root, "app/vite.config.js", "export default {}")
        write(root, "vendor-ui/package.json", '{"name": "vendored"}')
        write(root, "vendor-ui/inner/package.json", '{"name": "inner"}')

        scan = Scan(root)

        self.assertEqual(sorted(scan.files), ["app/package.json"])
        excluded = sorted((d.message, d.paths) for d in scan.diagnostics)
        self.assertEqual(
            excluded,
            [
                (".codeboarding is a hidden directory", (".codeboarding",)),
                (".env is excluded by .gitignore", (".env",)),
                ("vendor-ui is excluded by .codeboardingignore", ("vendor-ui",)),
            ],
        )

    def test_gitignore_prunes_a_directory_during_the_walk(self) -> None:
        """One row for the place, not one per file under it, and nothing below it is read."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, ".gitignore", "generated/\n")
        write(root, "generated/a/package.json", '{"name": "a"}')
        write(root, "generated/b/package.json", '{"name": "b"}')
        write(root, "src/package.json", '{"name": "src"}')

        scan = Scan(root)

        self.assertEqual(sorted(scan.files), ["src/package.json"])
        self.assertEqual(
            [(d.message, d.paths) for d in scan.diagnostics], [("generated is excluded by .gitignore", ("generated",))]
        )
        self.assertFalse(scan.has_dir("generated/a"))

    def test_a_file_named_by_a_manifest_is_found_even_under_a_pruned_directory(self) -> None:
        """The Go standard layout keeps its Dockerfiles under `build/`, which the walk leaves out."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "build/package/Dockerfile", "FROM golang:1.23\nCOPY . /src\n")
        write(root, "go.mod", "module acme/thing\n")

        scan = Scan(root)

        self.assertFalse(scan.has_file("build/package/Dockerfile"))
        self.assertTrue(scan.on_disk("build/package/Dockerfile"))
        self.assertFalse(scan.on_disk("build/package/Containerfile"))

    def test_a_manifest_over_the_cap_is_reported_rather_than_read(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "docker-compose.yml", "# " + "x" * MAX_BYTES + "\nservices: {}\n")

        scan = Scan(root)

        self.assertEqual(scan.text("docker-compose.yml"), "")
        self.assertEqual([d.code for d in scan.diagnostics], [DiagnosticCode.IGNORED_MANIFEST])
        self.assertIn("larger than", scan.diagnostics[0].message)


class TestReading(unittest.TestCase):
    def test_a_go_template_is_not_a_broken_manifest(self) -> None:
        scan = Scan(FIXTURES / "helm-chart")
        self.assertEqual(scan.documents("charts/web/templates/deployment.yaml"), [])
        self.assertEqual([d for d in scan.diagnostics if d.code is DiagnosticCode.UNREADABLE_MANIFEST], [])

    def test_yaml_that_is_merely_broken_is_reported(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "docker-compose.yml", "services:\n  api:\n   image: x\n  \tbad: [\n")

        scan = Scan(root)
        self.assertEqual(scan.documents("docker-compose.yml"), [])
        self.assertEqual([d.code for d in scan.diagnostics], [DiagnosticCode.UNREADABLE_MANIFEST])

    def test_an_unknown_yaml_tag_does_not_stop_the_read(self) -> None:
        """Compose's `!reset` and `!override` are tags, and a service beside them still reads."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "compose.yaml", "services:\n  api:\n    image: acme/api\n    ports: !reset []\n")

        (document,) = Scan(root).documents("compose.yaml")
        self.assertEqual(document["services"]["api"]["image"], "acme/api")

    def test_a_file_is_parsed_once(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(root, "compose.yaml", "services:\n  api:\n    image: acme/api\n")
        scan = Scan(root)

        self.assertIs(scan.documents("compose.yaml"), scan.documents("compose.yaml"))
        self.assertIs(scan.key_lines("compose.yaml"), scan.key_lines("compose.yaml"))

    def test_a_key_is_found_by_its_place_in_the_document_and_not_by_its_text(self) -> None:
        """`api:` occurs inside an image name before the `api` service is declared."""
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write(
            root,
            "compose.yaml",
            "services:\n  web:\n    image: registry/api:1\n    environment:\n      - API_URL=http://api\n"
            "  api:\n    build: ./api\n    environment:\n      PORT: 8080\n",
        )

        lines = Scan(root).key_lines("compose.yaml")

        self.assertEqual(lines["services.web"], 2)
        self.assertEqual(lines["services.api"], 6)
        self.assertEqual(lines["services.web.environment.0"], 5)
        self.assertEqual(lines["services.api.environment.PORT"], 9)

    def test_a_mistake_in_a_manifest_is_read_as_nothing_rather_than_raised(self) -> None:
        self.assertEqual(mapping("text"), {})
        self.assertEqual(mapping(None), {})
        self.assertEqual(mapping({"a": 1}), {"a": 1})
        self.assertEqual(listing("debug"), ["debug"])
        self.assertEqual(listing(8080), [8080])
        self.assertEqual(listing(None), [])
        self.assertEqual(listing(True), [])
        self.assertEqual(listing(["a"]), ["a"])


if __name__ == "__main__":
    unittest.main()
