"""Mojo language adapter using mojo-lsp-server (Modular)."""

from __future__ import annotations

import shutil
from pathlib import Path

from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter

# Past any real source column; keeps the symbol's final line fully inside
# its range during containment checks.
_LINE_END_CHAR = 10_000


def _extend_sibling_ranges(siblings: list[dict], parent_end_line: int) -> None:
    """Extend declaration-line-only ranges to the next sibling's start.

    Mojo is indentation-structured, so a symbol's body runs until the next
    sibling declaration (or the parent's end). Symbols with real multi-line
    ranges are left untouched.
    """
    ordered = sorted((s for s in siblings if s.get("range")), key=lambda s: s["range"]["start"]["line"])
    for i, sym in enumerate(ordered):
        rng = sym["range"]
        if i + 1 < len(ordered):
            sibling_end = ordered[i + 1]["range"]["start"]["line"] - 1
        else:
            sibling_end = parent_end_line
        if rng["end"]["line"] <= rng["start"]["line"]:
            rng["end"] = {"line": max(sibling_end, rng["start"]["line"]), "character": _LINE_END_CHAR}
        children = sym.get("children") or []
        if children:
            _extend_sibling_ranges(children, rng["end"]["line"])


class MojoAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "Mojo"

    @property
    def language_enum(self) -> Language:
        return Language.MOJO

    @property
    def lsp_command(self) -> list[str]:
        return ["mojo-lsp-server"]

    @property
    def language_id(self) -> str:
        return "mojo"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Raise with an install hint if no resolvable ``mojo-lsp-server`` is reachable."""
        command = super().get_lsp_command(project_root)
        if shutil.which(command[0]) is None:
            raise RuntimeError(
                "mojo-lsp-server not found. Install via `codeboarding-setup` "
                "(requires `pixi` on PATH; install from https://pixi.sh) or "
                "manually with `pixi global install --channel "
                "https://conda.modular.com/max --channel conda-forge "
                "--expose mojo-lsp-server mojo`."
            )
        return command

    def postprocess_document_symbols(self, symbols: list[dict], file_path: Path) -> list[dict]:
        """Synthesize body extents for mojo-lsp-server's degenerate ranges.

        Why: the server reports ``range == selectionRange`` (declaration line
        only) for every symbol, so call-site containment never matches and the
        call graph comes out empty without this.
        """
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return symbols
        last_line = max(0, len(text.splitlines()) - 1)
        _extend_sibling_ranges(symbols, last_line)
        return symbols
