import tempfile
import unittest
from pathlib import Path

from static_analyzer.wiring.scan import (
    FileKind,
    MAX_BYTES,
    Scan,
    classify,
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
                (".env is excluded by .gitignore (1 file)", (".env",)),
                ("vendor-ui is excluded by .codeboardingignore (2 files)", ("vendor-ui",)),
            ],
        )

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


if __name__ == "__main__":
    unittest.main()
