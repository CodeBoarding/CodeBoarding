"""Install the generated CODEBOARDING.md into a target repo and register it in agent-instruction files.

Non-destructive: content outside the ``codeboarding:begin/end`` marker block is never touched.
Idempotent: re-running replaces only the block between the markers.
"""

import json
import logging
import re
import shutil
from pathlib import Path

from diagram_analysis.analysis_json import parse_unified_analysis
from output_generators.agents_md import generate_agents_md_file

logger = logging.getLogger(__name__)

BEGIN_MARKER = "<!-- codeboarding:begin -->"
END_MARKER = "<!-- codeboarding:end -->"
IMPORT_LINE = "@CODEBOARDING.md"
MD_LINK_LINE = "See [CODEBOARDING.md](./CODEBOARDING.md) for the generated architecture digest."
BLOCK = f"{BEGIN_MARKER}\n{IMPORT_LINE}\n{END_MARKER}"
_BLOCK_RE = re.compile(rf"{re.escape(BEGIN_MARKER)}.*?{re.escape(END_MARKER)}", re.DOTALL)

PLACEHOLDER_CONTENT = (
    "# CodeBoarding — Architecture Overview\n"
    "\n"
    "_This file is managed by CodeBoarding. It will be populated on your next"
    " `codeboarding --local .` run._\n"
)

# (relative path, inner-block content). ``@`` import for Claude-Code-aware files
# (it treats the line as a recursive @-import); plain markdown reference everywhere else.
ALL_TARGETS: list[tuple[str, str]] = [
    ("AGENTS.md", IMPORT_LINE),
    ("CLAUDE.md", IMPORT_LINE),
    (".github/copilot-instructions.md", MD_LINK_LINE),
    (".windsurfrules", MD_LINK_LINE),
]


def setup_agents_md(repo_path: Path) -> None:
    """Register the AGENTS.md import marker and drop a placeholder CODEBOARDING.md."""
    _write_placeholder(repo_path)
    _ensure_marker_block(repo_path / "AGENTS.md", IMPORT_LINE)


def setup_agents_md_all(repo_path: Path) -> list[Path]:
    """Install marker blocks across every known agent-instruction file. Returns paths touched."""
    _write_placeholder(repo_path)
    touched: list[Path] = []
    for rel_path, inner in ALL_TARGETS:
        target = repo_path / rel_path
        _ensure_marker_block(target, inner)
        touched.append(target)
    return touched


def has_install_marker(repo_path: Path) -> bool:
    """Return True if ``<repo_path>/AGENTS.md`` already contains the CodeBoarding marker block.

    Why: AGENTS.md is the canonical consent receipt even when ``--all`` wrote markers
    to sibling files; keeping detection here means one source of truth for "is install on?"
    """
    agents_md = repo_path / "AGENTS.md"
    if not agents_md.exists():
        return False
    return BEGIN_MARKER in agents_md.read_text(encoding="utf-8")


def install_codeboarding_md(analysis_path: Path, repo_path: Path, output_dir: Path, repo_name: str) -> None:
    """Render the AGENTS-style digest and install it into ``repo_path``.

    Why: single entry point for the auto-refresh path in ``main.py`` so callers do one
    call per mode rather than re-threading the analysis-json parsing + generation steps.
    """
    if not analysis_path.exists():
        logger.warning("install_codeboarding_md: no analysis.json at %s; skipping", analysis_path)
        return
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    root, sub_analyses = parse_unified_analysis(data)
    digest = generate_agents_md_file(root, sub_analyses, repo_name, output_dir)
    install_into_repo(repo_path, digest)
    logger.info("Installed CODEBOARDING.md into %s", repo_path)


def install_into_repo(repo_path: Path, generated_md: Path) -> None:
    """Copy the digest to ``<repo_path>/CODEBOARDING.md`` and ensure AGENTS.md imports it."""
    shutil.copyfile(generated_md, repo_path / "CODEBOARDING.md")
    _ensure_marker_block(repo_path / "AGENTS.md", IMPORT_LINE)


def _write_placeholder(repo_path: Path) -> None:
    placeholder = repo_path / "CODEBOARDING.md"
    if not placeholder.exists():
        placeholder.write_text(PLACEHOLDER_CONTENT, encoding="utf-8")


def _ensure_marker_block(target: Path, inner: str) -> None:
    block = f"{BEGIN_MARKER}\n{inner}\n{END_MARKER}"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(block + "\n", encoding="utf-8")
        return

    original = target.read_text(encoding="utf-8")
    if _BLOCK_RE.search(original):
        updated = _BLOCK_RE.sub(block, original)
    else:
        sep = "" if original.startswith("\n") else "\n"
        updated = f"{block}\n{sep}{original}"

    if updated != original:
        target.write_text(updated, encoding="utf-8")
