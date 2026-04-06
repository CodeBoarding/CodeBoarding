"""Tests for C# project configuration scanner."""

from pathlib import Path

from static_analyzer.csharp_config_scanner import CSharpConfigScanner, CSharpProjectConfig
from repo_utils.ignore import RepoIgnoreManager


class TestCSharpProjectConfig:
    """Tests for CSharpProjectConfig data class."""

    def test_init(self):
        config = CSharpProjectConfig(Path("/project"), "solution")
        assert config.root == Path("/project")
        assert config.project_type == "solution"

    def test_repr(self):
        config = CSharpProjectConfig(Path("/project"), "project")
        assert "CSharpProjectConfig" in repr(config)
        assert "project" in repr(config)


class TestCSharpConfigScanner:
    """Tests for CSharpConfigScanner."""

    def test_scan_no_projects(self, tmp_path: Path):
        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()
        assert len(projects) == 0

    def test_scan_solution_file(self, tmp_path: Path):
        (tmp_path / "MyApp.sln").write_text("Microsoft Visual Studio Solution File")
        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 1
        assert projects[0].root == tmp_path
        assert projects[0].project_type == "solution"

    def test_scan_csproj_file(self, tmp_path: Path):
        (tmp_path / "MyApp.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"/>')
        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 1
        assert projects[0].root == tmp_path
        assert projects[0].project_type == "project"

    def test_solution_takes_precedence_over_csproj(self, tmp_path: Path):
        """When .sln and .csproj coexist at the same root, only solution is returned."""
        (tmp_path / "MyApp.sln").write_text("solution content")
        (tmp_path / "MyApp.csproj").write_text("<Project/>")

        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 1
        assert projects[0].project_type == "solution"

    def test_csproj_in_subdirectory_covered_by_solution(self, tmp_path: Path):
        """A .csproj inside a solution root is not duplicated."""
        (tmp_path / "MyApp.sln").write_text("solution content")
        sub = tmp_path / "src" / "Api"
        sub.mkdir(parents=True)
        (sub / "Api.csproj").write_text("<Project/>")

        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 1
        assert projects[0].project_type == "solution"

    def test_standalone_csproj_outside_solution(self, tmp_path: Path):
        """A .csproj in a sibling directory not covered by any solution root."""
        sln_dir = tmp_path / "main"
        sln_dir.mkdir()
        (sln_dir / "Main.sln").write_text("solution content")

        tool_dir = tmp_path / "tools" / "migrator"
        tool_dir.mkdir(parents=True)
        (tool_dir / "Migrator.csproj").write_text("<Project/>")

        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 2
        types = {p.project_type for p in projects}
        assert types == {"solution", "project"}

    def test_nested_solutions(self, tmp_path: Path):
        """Multiple solutions in different directories."""
        api_dir = tmp_path / "api"
        api_dir.mkdir()
        (api_dir / "Api.sln").write_text("solution content")

        web_dir = tmp_path / "web"
        web_dir.mkdir()
        (web_dir / "Web.sln").write_text("solution content")

        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 2
        roots = {p.root for p in projects}
        assert roots == {api_dir, web_dir}

    def test_fallback_cs_files_no_project(self, tmp_path: Path):
        """When .cs files exist but no .sln/.csproj, falls back to repo root."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "Program.cs").write_text("class Program {}")

        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 1
        assert projects[0].root == tmp_path
        assert projects[0].project_type == "none"

    def test_no_fallback_without_cs_files(self, tmp_path: Path):
        """No projects and no .cs files means empty result."""
        (tmp_path / "README.md").write_text("# Project")

        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 0

    def test_ignored_csproj_directory_skipped(self, tmp_path: Path):
        """Projects in ignored directories are not detected."""
        ignored = tmp_path / "node_modules" / "some-pkg"
        ignored.mkdir(parents=True)
        (ignored / "Tool.csproj").write_text("<Project/>")

        (tmp_path / ".gitignore").write_text("node_modules/")

        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 0

    def test_ignored_solution_directory_skipped(self, tmp_path: Path):
        """Solution files in ignored directories are not detected."""
        ignored = tmp_path / "node_modules" / "some-pkg"
        ignored.mkdir(parents=True)
        (ignored / "Embedded.sln").write_text("solution content")

        (tmp_path / ".gitignore").write_text("node_modules/")

        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        assert len(projects) == 0

    def test_custom_ignore_manager(self, tmp_path: Path):
        """Scanner respects a provided ignore manager."""
        (tmp_path / "MyApp.sln").write_text("solution content")
        ignore_manager = RepoIgnoreManager(tmp_path)

        scanner = CSharpConfigScanner(tmp_path, ignore_manager=ignore_manager)
        projects = scanner.scan()

        assert len(projects) == 1

    def test_aspire_monorepo_structure(self, tmp_path: Path):
        """.NET Aspire-style monorepo with solution at root and multiple projects."""
        (tmp_path / "MyApp.sln").write_text("solution content")

        for proj in ["MyApp.AppHost", "MyApp.ServiceDefaults", "MyApp.Api", "MyApp.Web"]:
            proj_dir = tmp_path / "src" / proj
            proj_dir.mkdir(parents=True)
            (proj_dir / f"{proj}.csproj").write_text("<Project/>")

        scanner = CSharpConfigScanner(tmp_path)
        projects = scanner.scan()

        # Solution at root covers all sub-projects
        assert len(projects) == 1
        assert projects[0].project_type == "solution"
        assert projects[0].root == tmp_path

    def test_is_subpath(self):
        assert CSharpConfigScanner._is_subpath(Path("/a/b"), Path("/a")) is True
        assert CSharpConfigScanner._is_subpath(Path("/a"), Path("/a")) is True
        assert CSharpConfigScanner._is_subpath(Path("/a"), Path("/b")) is False
