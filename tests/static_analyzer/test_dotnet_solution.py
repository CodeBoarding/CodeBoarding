from pathlib import Path

from static_analyzer.dotnet_solution import solution_projects


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<Project />")
    return path


def test_a_sln_lists_its_projects_and_not_its_solution_folders(tmp_path: Path) -> None:
    core = _touch(tmp_path / "src" / "Core" / "Core.csproj")
    tests = _touch(tmp_path / "test" / "Core.Tests" / "Core.Tests.fsproj")
    (tmp_path / "App.sln").write_text(
        "\ufeffMicrosoft Visual Studio Solution File, Format Version 12.00\n"
        'Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "Solution Items", "Solution Items", "{79FE9DBE}"\n'
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Core", "src\\Core\\Core.csproj", "{E273E6D8}"\n'
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Core.Tests", "test\\Core.Tests\\Core.Tests.fsproj", "{F771DF22}"\n'
        'Project("{9A19103F-16F7-4668-BE54-9A1E7A4F7556}") = "Gone", "src\\Gone\\Gone.csproj", "{BED2624C}"\n'
    )

    assert solution_projects(tmp_path / "App.sln") == [core, tests]


def test_a_slnx_lists_its_projects_including_those_in_folders(tmp_path: Path) -> None:
    app = _touch(tmp_path / "App" / "App.csproj")
    lib = _touch(tmp_path / "src" / "Lib" / "Lib.csproj")
    (tmp_path / "App.slnx").write_text(
        "<Solution>\n"
        '  <Project Path="App/App.csproj" />\n'
        '  <Folder Name="/src/">\n'
        '    <Project Path="src/Lib/Lib.csproj" />\n'
        '    <Project Path="src/Missing/Missing.csproj" />\n'
        "  </Folder>\n"
        "</Solution>\n"
    )

    assert solution_projects(tmp_path / "App.slnx") == [app, lib]


def test_a_project_listed_twice_appears_once(tmp_path: Path) -> None:
    app = _touch(tmp_path / "App" / "App.csproj")
    (tmp_path / "App.slnx").write_text(
        '<Solution><Project Path="App/App.csproj" /><Project Path="App\\App.csproj" /></Solution>'
    )

    assert solution_projects(tmp_path / "App.slnx") == [app]
