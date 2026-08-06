"""Generate a C# solution that reproduces the csharp-ls solution-load blowup.

csharp-ls folds every project's frameworks into one global MSBuild
``TargetFramework``. A global property outranks a project's own
``<TargetFramework>``, so in a solution that does not target one framework
throughout, the odd projects out are evaluated under a framework they do not
target. The generated solution wires Roslyn analyzers in through the idiomatic
``Condition="'$(TargetFramework)' != 'netstandard2.0'"`` guard, which inverts
under that property and makes the analyzer projects reference themselves.
Roslyn's solution load then grows without bound.

    uv run python tests/static_analyzer/scripts/csharp_mixed_framework_repro.py /tmp/repro
    cd /tmp/repro && dotnet restore Synthetic.slnx

Then point CodeBoarding at ``/tmp/repro``. Before the fix the CSharp sync probe
never returns; after it the analysis completes in well under a minute.

``--variant name-guard`` keys the same guard on ``$(MSBuildProjectName)``, which
a global framework cannot invert. It is the control: it loads fine either way,
which isolates the cause to the property rather than to solution size.
"""

import argparse
import shutil
from pathlib import Path

ANALYZERS = ["Gen.Contracts", "Gen", "Analyzers", "CodeFixers"]

NUGET_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
  </packageSources>
</configuration>
"""

LIB_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
  </PropertyGroup>
</Project>
"""

ANALYZER_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netstandard2.0</TargetFramework>
  </PropertyGroup>
</Project>
"""

CS_FILE = """namespace {ns};

public sealed class {cls}
{{
    public int Value {{ get; init; }}

    public int Compute(int seed) => seed + Value + {n};
}}
"""

DIRECTORY_BUILD_PROPS = """<Project>
  <PropertyGroup>
    <Nullable>enable</Nullable>
  </PropertyGroup>

  <!-- Wire the analyzers into every project except the analyzer projects themselves. -->
  <ItemGroup Condition="{guard}">
{refs}
  </ItemGroup>
</Project>
"""


def _guard(variant: str) -> str:
    if variant == "name-guard":
        return " and ".join(f"'$(MSBuildProjectName)' != '{a}'" for a in ANALYZERS)
    return "'$(TargetFramework)' != 'netstandard2.0'"


def generate(root: Path, projects: int, files_per_project: int, variant: str) -> None:
    refs = "\n".join(
        f'    <ProjectReference Include="$(MSBuildThisFileDirectory)src/analyzers/{a}/{a}.csproj"\n'
        f'                      OutputItemType="Analyzer" ReferenceOutputAssembly="true" PrivateAssets="all" />'
        for a in ANALYZERS
    )
    (root / "nuget.config").write_text(NUGET_CONFIG)
    (root / "Directory.Build.props").write_text(DIRECTORY_BUILD_PROPS.format(guard=_guard(variant), refs=refs))

    project_paths: list[str] = []
    for analyzer in ANALYZERS:
        directory = root / "src" / "analyzers" / analyzer
        directory.mkdir(parents=True)
        (directory / f"{analyzer}.csproj").write_text(ANALYZER_CSPROJ)
        name = analyzer.replace(".", "")
        (directory / f"{name}.cs").write_text(CS_FILE.format(ns=f"Synthetic.Analyzers.{analyzer}", cls=name, n=1))
        project_paths.append(f"src/analyzers/{analyzer}/{analyzer}.csproj")

    for index in range(projects):
        name = f"Lib{index:03d}"
        directory = root / "src" / "libs" / name
        directory.mkdir(parents=True)
        (directory / f"{name}.csproj").write_text(LIB_CSPROJ)
        for file_index in range(files_per_project):
            (directory / f"Type{file_index:02d}.cs").write_text(
                CS_FILE.format(ns=f"Synthetic.{name}", cls=f"Type{file_index:02d}", n=index * 100 + file_index)
            )
        project_paths.append(f"src/libs/{name}/{name}.csproj")

    entries = "\n".join(f'  <Project Path="{path}" />' for path in project_paths)
    (root / "Synthetic.slnx").write_text(f"<Solution>\n{entries}\n</Solution>\n")

    print(f"generated {root}: {len(project_paths)} projects, variant={variant}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--projects", type=int, default=40)
    parser.add_argument("--files-per-project", type=int, default=8)
    parser.add_argument("--variant", choices=["tfm-guard", "name-guard"], default="tfm-guard")
    args = parser.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    generate(args.out, args.projects, args.files_per_project, args.variant)


if __name__ == "__main__":
    main()
