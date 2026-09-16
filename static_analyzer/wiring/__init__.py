"""The wiring pass: what connects services at run time and the call graph cannot see.

`docs/design/wiring.md` is the contract. The pass runs at the end of `StaticAnalyzer.analyze`, after
the per-engine results are merged, because its edges cross engines: a compose file wires a Python
service to a Java one. It calls no model, and at this stage of the stack reads no source — build
manifests, deployment topology and compose files only — and is off unless `CODEBOARDING_WIRING=1`.
It can never break an analysis: `run_or_report` turns anything it raises into one diagnostic.

`CODEBOARDING_WIRING_DUMP=<dir>` writes what the evals checks grade before any arrow exists:
`units.json` and `diagnostics.json` in the schema of §8.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import re
import time
from pathlib import Path

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.wiring.anchors import collect, to_json
from static_analyzer.wiring.compose import compose_projects
from static_analyzer.wiring.scan import Scan
from static_analyzer.wiring.units import build_units
from static_analyzer.wiring_results import Diagnostic, DiagnosticCode, WiringResults

logger = logging.getLogger(__name__)

FLAG = "CODEBOARDING_WIRING"
DUMP = "CODEBOARDING_WIRING_DUMP"

_REMOTE = re.compile(r"(?:[:/])([^/:]+/[^/:]+?)(?:\.git)?/?$")


def enabled() -> bool:
    """Whether the pass runs at all: off until the stack is complete, so no document changes."""
    return os.environ.get(FLAG, "") == "1"


def dump_dir() -> Path | None:
    """Where the debug dumps go, or None when nothing asked for them."""
    directory = os.environ.get(DUMP, "").strip()
    return Path(directory) if directory else None


def run(results: StaticAnalysisResults, repo_root: Path, *, dump: Path | None = None) -> WiringResults:
    """Read what wires this repository together. Deterministic: one tree, one answer, sorted.

    ``results`` is the analysis the pass runs after; later stages of the stack read its graphs to
    know which units hold analysed code.
    """
    started = time.monotonic()
    scan = Scan(repo_root)
    projects = compose_projects(scan)
    units = build_units(scan, repository_name(repo_root), projects)
    anchors = collect(scan, units, projects)
    wiring = WiringResults(units=units, anchors=anchors, diagnostics=sorted(scan.diagnostics, key=_order))
    logger.info(
        "wiring: %d files read, %d units, %d anchors, %d diagnostics in %.2fs",
        len(scan.files),
        len(wiring.units),
        len(wiring.anchors),
        len(wiring.diagnostics),
        time.monotonic() - started,
    )
    if dump is not None:
        write_dump(wiring, repo_root, dump)
    return wiring


def run_or_report(results: StaticAnalysisResults, repo_root: Path, *, dump: Path | None = None) -> WiringResults:
    """``run``, except that a failure is one diagnostic and an empty result: the pass never breaks an analysis."""
    try:
        return run(results, repo_root, dump=dump)
    except Exception as error:  # noqa: BLE001 - whatever it was, the analysis goes on without wiring
        logger.exception("wiring: the pass failed and the analysis continues without it")
        message = f"the wiring pass failed and was skipped: {type(error).__name__}: {error}"
        return WiringResults(diagnostics=[Diagnostic(code=DiagnosticCode.UNREADABLE_MANIFEST, message=message)])


def write_dump(wiring: WiringResults, repo_root: Path, directory: Path) -> None:
    """The debug dumps of §8, keyed by the repository and commit so a grader knows what it reads."""
    directory.mkdir(parents=True, exist_ok=True)
    heading = {"repo": repository_slug(repo_root), "commit": head_commit(repo_root)}
    diagnostics = [diagnostic.to_json() for diagnostic in wiring.diagnostics]
    _write(
        directory / "units.json",
        {**heading, "units": [unit.to_json() for unit in wiring.units], "diagnostics": diagnostics},
    )
    _write(directory / "anchors.json", {**heading, "anchors": [to_json(anchor) for anchor in wiring.anchors]})
    _write(directory / "diagnostics.json", {**heading, "diagnostics": diagnostics})


def repository_name(repo_root: Path) -> str:
    """The repository's own name, from the remote rather than the checkout directory.

    Why: a clone is checked out under whatever name the machine gave it (`wt-3`, `repo-main`),
    and the image a repository publishes is named after the repository.
    """
    return repository_slug(repo_root).rsplit("/", 1)[-1]


def repository_slug(repo_root: Path) -> str:
    """`owner/name` from the origin remote, or the directory's name when there is no remote."""
    config = _git_dir(repo_root) / "config"
    parser = configparser.ConfigParser()
    try:
        parser.read(config, encoding="utf-8")
    except (OSError, configparser.Error):
        return repo_root.name
    url = parser.get('remote "origin"', "url", fallback="")
    found = _REMOTE.search(url.strip())
    return found.group(1) if found else repo_root.name


def head_commit(repo_root: Path) -> str:
    """The commit the tree is at, read from the git directory rather than from a subprocess."""
    git = _git_dir(repo_root)
    head = _read(git / "HEAD")
    if not head.startswith("ref:"):
        return head
    reference = head.removeprefix("ref:").strip()
    direct = _read(git / reference)
    if direct:
        return direct
    for line in _read(git / "packed-refs").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == reference:
            return parts[0]
    return ""


def _git_dir(repo_root: Path) -> Path:
    """The repository's git directory, following the `gitdir:` pointer a worktree leaves behind."""
    git = repo_root / ".git"
    if git.is_dir():
        return git
    pointer = _read(git)
    return Path(pointer.removeprefix("gitdir:").strip()) if pointer.startswith("gitdir:") else git


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def _order(diagnostic: Diagnostic) -> tuple[str, str, tuple[str, ...]]:
    return diagnostic.code.value, diagnostic.message, diagnostic.paths
