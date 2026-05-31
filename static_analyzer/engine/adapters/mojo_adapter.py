"""Mojo language adapter using mojo-lsp-server (Modular)."""

from __future__ import annotations

import shutil
from pathlib import Path

from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter


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
                "https://conda.modular.com/max --expose mojo-lsp-server mojo`."
            )
        return command
