"""The projects a .NET solution file lists."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

PROJECT_SUFFIXES = (".csproj", ".fsproj")
# ``Project("{type guid}") = "Name", "relative\path.csproj", "{project guid}"``
_SLN_PROJECT_LINE = re.compile(r'^Project\("\{[^}]*\}"\)\s*=\s*"[^"]*",\s*"([^"]+)"', re.MULTILINE)


def solution_projects(solution: Path) -> list[Path]:
    """The project files a ``.sln`` or ``.slnx`` lists, absolute, in listing order.

    Solution folders and files that do not exist are left out; a ``.sln`` names
    its solution folders with the same ``Project(...)`` syntax as its projects.
    """
    if solution.suffix.lower() == ".slnx":
        listed = [element.get("Path") or "" for element in ElementTree.parse(solution).iter("Project")]
    else:
        listed = _SLN_PROJECT_LINE.findall(solution.read_text(encoding="utf-8-sig", errors="replace"))
    projects: list[Path] = []
    for raw in listed:
        path = (solution.parent / raw.replace("\\", "/")).resolve()
        if path.suffix.lower() in PROJECT_SUFFIXES and path.is_file() and path not in projects:
            projects.append(path)
    return projects


def analyzer_project_references(projects: list[Path]) -> list[Path]:
    """The projects referenced with ``OutputItemType="Analyzer"``, absolute, in listing order.

    Why: an analyzer reference resolves to the referenced project's output assembly, so
    it stays unresolved until that project is built.
    """
    analyzers: list[Path] = []
    for project in projects:
        try:
            root = ElementTree.parse(project).getroot()
        except (ElementTree.ParseError, OSError):
            continue
        for reference in root.iter():
            # Legacy-format projects namespace every tag, so match on the local name.
            if reference.tag.rpartition("}")[2] != "ProjectReference":
                continue
            if (reference.get("OutputItemType") or "").strip().lower() != "analyzer":
                continue
            raw = reference.get("Include") or ""
            if not raw:
                continue
            path = (project.parent / raw.replace("\\", "/")).resolve()
            if path.suffix.lower() in PROJECT_SUFFIXES and path.is_file() and path not in analyzers:
                analyzers.append(path)
    return analyzers
