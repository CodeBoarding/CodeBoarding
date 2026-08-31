"""Tests for F# project configuration scanner."""

from pathlib import Path

from static_analyzer.fsharp_config_scanner import FSharpConfigScanner, FSharpProjectConfig


class TestFSharpProjectConfig:
    """Tests for FSharpProjectConfig data class."""

    def test_init(self):
        config = FSharpProjectConfig(Path("/project"), "solution")
        assert config.root == Path("/project")
        assert config.project_type == "solution"

    def test_repr(self):
        config = FSharpProjectConfig(Path("/project"), "project")
        assert "FSharpProjectConfig" in repr(config)
        assert "project" in repr(config)


class TestFSharpConfigScanner:
    """Tests for FSharpConfigScanner."""

    def test_scan_no_projects(self, tmp_path: Path):
        assert FSharpConfigScanner(tmp_path).scan() == []

    def test_scan_solution_file(self, tmp_path: Path):
        (tmp_path / "MyApp.sln").write_text("Microsoft Visual Studio Solution File")
        (tmp_path / "Library.fs").write_text("module Library")

        projects = FSharpConfigScanner(tmp_path).scan()

        assert len(projects) == 1
        assert projects[0].root == tmp_path
        assert projects[0].project_type == "solution"

    def test_scan_slnx_solution_file(self, tmp_path: Path):
        (tmp_path / "MyApp.slnx").write_text("<Solution/>")
        (tmp_path / "Library.fs").write_text("module Library")

        projects = FSharpConfigScanner(tmp_path).scan()

        assert len(projects) == 1
        assert projects[0].project_type == "solution"

    def test_scan_fsproj_file(self, tmp_path: Path):
        (tmp_path / "MyApp.fsproj").write_text('<Project Sdk="Microsoft.NET.Sdk"/>')
        (tmp_path / "Library.fs").write_text("module Library")

        projects = FSharpConfigScanner(tmp_path).scan()

        assert len(projects) == 1
        assert projects[0].root == tmp_path
        assert projects[0].project_type == "project"

    def test_project_below_a_solution_is_not_scanned_twice(self, tmp_path: Path):
        (tmp_path / "MyApp.sln").write_text("Microsoft Visual Studio Solution File")
        (tmp_path / "Root.fs").write_text("module Root")
        nested = tmp_path / "src" / "Library"
        nested.mkdir(parents=True)
        (nested / "Library.fsproj").write_text("<Project/>")
        (nested / "Library.fs").write_text("module Library")

        projects = FSharpConfigScanner(tmp_path).scan()

        assert [p.root for p in projects] == [tmp_path]

    def test_solution_without_fsharp_sources_is_skipped(self, tmp_path: Path):
        """A C#-only solution must not reach the F# engine: both languages share
        solution files, so this filter is what keeps each scanner to its own."""
        (tmp_path / "MyApp.sln").write_text("Microsoft Visual Studio Solution File")
        (tmp_path / "Program.cs").write_text("class Program {}")

        assert FSharpConfigScanner(tmp_path).scan() == []

    def test_fallback_to_repo_root_without_project_files(self, tmp_path: Path):
        (tmp_path / "Script.fs").write_text("module Script")

        projects = FSharpConfigScanner(tmp_path).scan()

        assert len(projects) == 1
        assert projects[0].root == tmp_path
        assert projects[0].project_type == "none"

    def test_ignored_sources_do_not_trigger_the_fallback(self, tmp_path: Path):
        """F# hidden inside an ignored directory must not drag the whole root in."""
        (tmp_path / ".gitignore").write_text("vendor/\n")
        vendored = tmp_path / "vendor"
        vendored.mkdir()
        (vendored / "Vendored.fs").write_text("module Vendored")

        assert FSharpConfigScanner(tmp_path).scan() == []
