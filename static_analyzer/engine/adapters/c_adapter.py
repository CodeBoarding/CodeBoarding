"""C language adapter — clangd backend shared with C++.

Mirrors the JavaScript/TypeScript pattern: ``CAdapter`` inherits the entire
clangd integration from ``CppAdapter`` and only overrides the surface labels
plus ``config_key`` so both adapters share the single ``"cpp"`` LSP install
in ``tool_registry`` and ``vscode_constants``. The dedup post-pass in
``static_analyzer._create_engine_configs`` collapses mixed C/C++ projects
onto ``CppAdapter`` so clangd spawns once per project root.
"""

from __future__ import annotations

from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import Language
from static_analyzer.engine.adapters.cpp_adapter import CppAdapter


class CAdapter(CppAdapter):

    @property
    def language(self) -> str:
        return "C"

    @property
    def language_enum(self) -> Language:
        return Language.C

    @property
    def language_id(self) -> str:
        return "c"

    def language_id_for_file(self, file_path: Path) -> str:
        # Pure-C adapter only sees C files; explicit override avoids inheriting
        # CppAdapter's ``.c``/``.h`` -> "c" branching.
        return "c"

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """``-std=c17`` keeps clangd in C mode when no CDB pins the dialect.

        Why: ``CppAdapter`` returns ``-std=c++20``, which makes clangd parse
        ``.c`` files as C++20 and mis-flag C-only syntax.
        """
        return {"fallbackFlags": ["-std=c17"]}

    @property
    def config_key(self) -> str:
        # Shares the clangd config block in vscode_constants/tool_registry.
        return "cpp"
