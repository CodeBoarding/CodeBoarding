"""A JavaScript workspace's members, for the tsserver plugin (``typescript_plugin/``) that resolves calls into them.

Why: in a checkout neither installed nor built, ``import { f } from "@acme/core"`` resolves nowhere: there is no
``node_modules/@acme/core`` link, and the member's ``main``/``types``/``exports`` name a ``dist/`` that does not exist.
"""

import json
import re
from pathlib import Path

import yaml

from static_analyzer.exceptions import WorkspaceSetupError

PLUGIN_NAME = "codeboarding-workspace-sources"
PLUGIN_LOCATION = Path(__file__).resolve().parent / "typescript_plugin"
"""tsserver loads a plugin from ``<location>/node_modules/<name>``."""


def workspace_members(repo: Path) -> dict[str, Path]:
    """Each declared member's package name and folder, the first by path winning; empty when *repo* declares none.

    Why pnpm-workspace.yaml alone when present: pnpm reads no ``workspaces`` from package.json.
    """
    pnpm = repo / "pnpm-workspace.yaml"
    try:
        if pnpm.is_file():
            patterns = (yaml.safe_load(pnpm.read_text(encoding="utf-8")) or {}).get("packages")
        elif (repo / "package.json").is_file():
            patterns = json.loads((repo / "package.json").read_text(encoding="utf-8")).get("workspaces")
            patterns = patterns.get("packages") if isinstance(patterns, dict) else patterns
        else:
            return {}
        patterns = patterns or []
        if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
            raise ValueError(f"the member patterns are no list of strings: {patterns!r}")
        expanded = [
            (pattern.startswith("!"), alternative.removeprefix("./").strip("/"))
            for pattern in patterns
            for alternative in expand_braces(pattern.removeprefix("!"))
        ]
        excluded = [glob_regex(pattern) for negated, pattern in expanded if negated]
        members: dict[str, Path] = {}
        for negated, pattern in expanded:
            if negated or pattern in ("", "."):
                continue
            for manifest in sorted(repo.glob(f"{pattern}/package.json")):
                folder = manifest.parent.relative_to(repo)
                if "node_modules" in folder.parts or any(
                    regex.fullmatch(f"{folder.as_posix()}/") for regex in excluded
                ):
                    continue
                name = json.loads(manifest.read_text(encoding="utf-8")).get("name")
                if isinstance(name, str) and name:
                    members.setdefault(name, manifest.parent)
    except (OSError, ValueError, AttributeError, yaml.YAMLError) as error:
        raise WorkspaceSetupError(f"Cannot read the workspace {repo} declares: {error}") from error
    return members


def expand_braces(pattern: str) -> list[str]:
    """*pattern* with each ``{a,b}`` group spelled out, as the package managers' globs do."""
    group = re.search(r"\{([^{}]*,[^{}]*)\}", pattern)
    if group is None:
        return [pattern]
    head, tail = pattern[: group.start()], pattern[group.end() :]
    return [expanded for alternative in group[1].split(",") for expanded in expand_braces(head + alternative + tail)]


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A workspace glob over a folder's path from the root plus ``/``: ``*`` within a segment, ``**`` any depth."""
    return re.compile(
        "".join(
            (
                "(?:[^/]+/)*"
                if segment == "**"
                else re.escape(segment).replace(r"\*", "[^/]*").replace(r"\?", "[^/]") + "/"
            )
            for segment in pattern.split("/")
        )
    )
