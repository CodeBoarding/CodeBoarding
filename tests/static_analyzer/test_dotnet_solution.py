from pathlib import Path

from static_analyzer.dotnet_solution import analyzer_project_references, solution_projects


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


def test_analyzer_project_references_finds_projects_referenced_as_analyzers(tmp_path: Path) -> None:
    generator = _touch(tmp_path / "src" / "Gen" / "Gen.csproj")
    _touch(tmp_path / "src" / "Plain" / "Plain.csproj")
    app = tmp_path / "src" / "App" / "App.csproj"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text(
        "<Project>\n"
        "  <ItemGroup>\n"
        '    <ProjectReference Include="..\\Gen\\Gen.csproj" OutputItemType="Analyzer" />\n'
        '    <ProjectReference Include="..\\Plain\\Plain.csproj" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    )

    assert analyzer_project_references([app]) == [generator]


def test_analyzer_project_references_deduplicates_across_projects(tmp_path: Path) -> None:
    generator = _touch(tmp_path / "Gen" / "Gen.csproj")
    consumers = []
    for name in ("A", "B"):
        consumer = tmp_path / name / f"{name}.csproj"
        consumer.parent.mkdir(parents=True, exist_ok=True)
        consumer.write_text(
            "<Project>\n"
            "  <ItemGroup>\n"
            f'    <ProjectReference Include="..\\Gen\\Gen.csproj" OutputItemType="Analyzer" />\n'
            "  </ItemGroup>\n"
            "</Project>\n"
        )
        consumers.append(consumer)

    assert analyzer_project_references(consumers) == [generator]


def test_analyzer_project_references_skips_a_reference_whose_project_is_gone(tmp_path: Path) -> None:
    app = tmp_path / "App" / "App.csproj"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text(
        "<Project>\n"
        "  <ItemGroup>\n"
        '    <ProjectReference Include="..\\Gone\\Gone.csproj" OutputItemType="Analyzer" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    )

    assert analyzer_project_references([app]) == []


def test_analyzer_project_references_survives_an_unparseable_project(tmp_path: Path) -> None:
    """A malformed csproj must not abort discovery for the projects after it."""
    broken = tmp_path / "Broken" / "Broken.csproj"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("<Project><ItemGroup>")
    generator = _touch(tmp_path / "Gen" / "Gen.csproj")
    app = tmp_path / "App" / "App.csproj"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text(
        "<Project>\n"
        "  <ItemGroup>\n"
        '    <ProjectReference Include="..\\Gen\\Gen.csproj" OutputItemType="Analyzer" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    )

    assert analyzer_project_references([broken, app]) == [generator]


def test_analyzer_project_references_reads_a_legacy_namespaced_project(tmp_path: Path) -> None:
    """Legacy-format projects namespace every tag, so a tag-name match finds nothing."""
    generator = _touch(tmp_path / "Gen" / "Gen.csproj")
    app = tmp_path / "App" / "App.csproj"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text(
        '<Project ToolsVersion="15.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">\n'
        "  <ItemGroup>\n"
        '    <ProjectReference Include="..\\Gen\\Gen.csproj" OutputItemType="Analyzer" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    )

    assert analyzer_project_references([app]) == [generator]
