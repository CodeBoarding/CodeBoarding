#!/usr/bin/env python3
"""Generate a synthetic C# monorepo to reproduce the csharp-ls multi-target load hang.

See docs/development/csharp-ls-multitarget-hang.md. With --multitarget and >= ~32 projects,
csharp-ls 0.21-0.26 never finishes loading the solution; without it, load time is linear.
"""

import argparse
import shutil
import uuid
from pathlib import Path

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
{refs}</Project>
"""

# Customer-profile variant: no TFM in the csproj at all — Directory.Build.props supplies it.
CSPROJ_PROPS = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <RootNamespace>{name}</RootNamespace>
  </PropertyGroup>
{refs}</Project>
"""

# Analyzer/source-generator projects deviate from the central TFM (netstandard2.0), like the
# customer's repo and most real-world monorepos.
CSPROJ_ANALYZER = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netstandard2.0</TargetFramework>
    <LangVersion>latest</LangVersion>
  </PropertyGroup>
{refs}</Project>
"""

DIRECTORY_BUILD_PROPS = """<Project>
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <AnalysisLevel>latest-Recommended</AnalysisLevel>
  </PropertyGroup>
</Project>
"""

CLASS_TMPL = """namespace {ns};

public class Service{c}
{{
    public int Compute{c}(int x)
    {{
        var helper = new Helper{c}();
        return helper.Add(x, {c});
    }}

    public string Describe{c}() => $"svc-{c}-{{Compute{c}({c})}}";
}}

public class Helper{c}
{{
    public int Add(int a, int b) => a + b;

    public int Chain(int x)
    {{
        var s = new Service{c}();
        return s.Compute{c}(x);
    }}
}}
"""


def write_project(root: Path, idx: int, files_per_project: int, ref_prev: bool, csproj_template: str) -> Path:
    name = f"Acme.Svc{idx:03d}"
    proj_dir = root / "src" / "services" / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    refs = ""
    if ref_prev and idx > 0:
        prev = f"Acme.Svc{idx - 1:03d}"
        refs = "  <ItemGroup>\n" f'    <ProjectReference Include="..\\{prev}\\{prev}.csproj" />\n' "  </ItemGroup>\n"
    (proj_dir / f"{name}.csproj").write_text(csproj_template.format(refs=refs, name=name))
    for c in range(files_per_project):
        (proj_dir / f"Service{c}.cs").write_text(CLASS_TMPL.format(ns=name, c=c))
    return proj_dir / f"{name}.csproj"


def write_sln(root: Path, projects: list[Path]) -> None:
    lines = [
        "Microsoft Visual Studio Solution File, Format Version 12.00",
        "# Visual Studio Version 17",
        "VisualStudioVersion = 17.0.31903.59",
        "MinimumVisualStudioVersion = 10.0.40219.1",
    ]
    guids = []
    for proj in projects:
        guid = str(uuid.uuid4()).upper()
        guids.append(guid)
        rel = str(proj.relative_to(root)).replace("/", "\\")
        lines.append(f'Project("{{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}}") = "{proj.stem}", "{rel}", "{{{guid}}}"')
        lines.append("EndProject")
    lines.append("Global")
    lines.append("\tGlobalSection(SolutionConfigurationPlatforms) = preSolution")
    lines.append("\t\tDebug|Any CPU = Debug|Any CPU")
    lines.append("\tEndGlobalSection")
    lines.append("\tGlobalSection(ProjectConfigurationPlatforms) = postSolution")
    for guid in guids:
        lines.append(f"\t\t{{{guid}}}.Debug|Any CPU.ActiveCfg = Debug|Any CPU")
        lines.append(f"\t\t{{{guid}}}.Debug|Any CPU.Build.0 = Debug|Any CPU")
    lines.append("\tEndGlobalSection")
    lines.append("EndGlobal")
    (root / "Monorepo.sln").write_text("\n".join(lines) + "\n")


def write_slnx(root: Path, projects: list[Path]) -> None:
    lines = ["<Solution>"]
    for proj in projects:
        lines.append(f'  <Project Path="{proj.relative_to(root)}" />')
    lines.append("</Solution>")
    (root / "Monorepo.slnx").write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out")
    ap.add_argument("--projects", type=int, default=50)
    ap.add_argument("--files", type=int, default=20)
    ap.add_argument("--format", choices=["sln", "slnx", "both"], default="slnx")
    ap.add_argument("--ref-prev", action="store_true", help="chain ProjectReference to previous project")
    ap.add_argument(
        "--multitarget",
        action="store_true",
        help="use <TargetFrameworks>net9.0;net10.0</TargetFrameworks> (the exponential hang trigger)",
    )
    ap.add_argument(
        "--props",
        action="store_true",
        help="customer profile: no TFM in csproj, central <TargetFramework> in Directory.Build.props",
    )
    ap.add_argument(
        "--analyzers",
        type=int,
        default=0,
        help="with --props: number of trailing projects that override to netstandard2.0",
    )
    args = ap.parse_args()

    root = Path(args.out)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    csproj_template = CSPROJ
    if args.multitarget:
        csproj_template = csproj_template.replace(
            "<TargetFramework>net10.0</TargetFramework>",
            "<TargetFrameworks>net9.0;net10.0</TargetFrameworks>",
        )
    elif args.props:
        csproj_template = CSPROJ_PROPS
        (root / "Directory.Build.props").write_text(DIRECTORY_BUILD_PROPS)

    analyzer_start = args.projects - args.analyzers if args.props else args.projects
    projects = [
        write_project(root, i, args.files, args.ref_prev, CSPROJ_ANALYZER if i >= analyzer_start else csproj_template)
        for i in range(args.projects)
    ]
    if args.format in ("sln", "both"):
        write_sln(root, projects)
    if args.format in ("slnx", "both"):
        write_slnx(root, projects)
    print(f"Generated {args.projects} projects x {args.files} files = {args.projects * args.files} .cs files at {root}")


if __name__ == "__main__":
    main()
