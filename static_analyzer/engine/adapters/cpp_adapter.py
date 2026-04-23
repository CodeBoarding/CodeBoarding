"""C++ language adapter using clangd."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import NodeType
from static_analyzer.engine.adapters.cpp_cdb import (
    detect_build_system,
    ensure_cdb,
    install_hint_for,
    locate_generated_cdb,
    locate_user_cdb,
)
from static_analyzer.engine.adapters.cpp_cdb.base import CDB_SUBDIR, CPP_SOURCE_EXTENSIONS
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_constants import CALLABLE_KINDS

_OPERATOR_SYMBOL_CHARS = frozenset("<>=!+-*/%&|^~?,[]")


def _strip_template_args(name: str) -> str:
    """Strip balanced ``<...>`` template-arg blocks, preserving ``operator`` tokens.

    Why: ``operator<=>`` would otherwise collapse to ``operator`` via the
    balanced-bracket rule, merging distinct C++20 operator symbols.
    Unbalanced inputs return unchanged.
    """
    if "<" not in name:
        return name

    out: list[str] = []
    depth = 0
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        if (
            depth == 0
            and ch == "o"
            and name.startswith("operator", i)
            and (i == 0 or not (name[i - 1].isalnum() or name[i - 1] == "_"))
        ):
            j = i + len("operator")
            while j < n and name[j] == " ":
                j += 1
            if j < n and (name[j].isalpha() or name[j] == "_"):
                while j < n and (name[j].isalnum() or name[j] == "_"):
                    j += 1
            else:
                while j < n and name[j] in _OPERATOR_SYMBOL_CHARS:
                    j += 1
            out.extend(name[i:j])
            i = j
            continue
        if ch == "<" and out and (out[-1].isalnum() or out[-1] == "_"):
            depth += 1
            i += 1
            continue
        if ch == ">" and depth > 0:
            depth -= 1
            i += 1
            continue
        if depth == 0:
            out.append(ch)
        i += 1
    if depth != 0:
        return name
    return "".join(out)


_SIGNATURE_WS_RE = re.compile(r"\s+")
_SIGNATURE_SCOPE_RE = re.compile(r"::")


def _extract_signature(detail: str) -> str | None:
    """Extract a normalized ``(param_types)`` suffix from a clangd ``detail``.

    Returns ``None`` when a suffix would add no disambiguation value (no
    ``(``, empty parens, unbalanced parens).
    """
    if not detail:
        return None
    open_idx = detail.find("(")
    if open_idx < 0:
        return None
    depth = 0
    end = -1
    for i in range(open_idx, len(detail)):
        ch = detail[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    body = detail[open_idx + 1 : end].strip()
    if not body:
        return None
    # Normalize whitespace (``const  Entity &`` → ``const Entity &``),
    # tighten trailing-``&``/``*`` (``Entity &`` → ``Entity&``) and flatten
    # ``::`` to ``.`` to match the adapter's scope-operator convention.
    body = _SIGNATURE_WS_RE.sub(" ", body).strip()
    body = body.replace(" &", "&").replace(" *", "*")
    body = _SIGNATURE_SCOPE_RE.sub(".", body)
    return f"({body})"


@lru_cache(maxsize=4096)
def _normalize_cpp_parent(name: str) -> str:
    """Flatten ``foo::Bar<T>`` to ``foo.Bar`` for qualified-name consumption.

    Why: parent names repeat heavily across symbols in large C++ projects;
    an LRU cache eliminates redundant template-stripping and allocation.
    """
    stripped = _strip_template_args(name)
    if not stripped or not stripped.strip():
        return ""
    return stripped.strip().replace("::", ".")


class CppAdapter(LanguageAdapter):
    """Static-analysis adapter for C/C++ projects backed by clangd."""

    @property
    def language(self) -> str:
        return "Cpp"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return tuple(sorted(CPP_SOURCE_EXTENSIONS))

    @property
    def lsp_command(self) -> list[str]:
        return ["clangd"]

    @property
    def language_id(self) -> str:
        return "cpp"

    @property
    def references_per_query_timeout(self) -> int:
        """Gates the Phase-1.5 warmup probe so clangd finishes indexing before Phase 2."""
        return 60

    def phase1_request_timeout(self, probe_timeout: int) -> int | None:
        """Use probe_timeout for Phase 1 document_symbol calls.

        Why: on Windows, clangd background-indexes during Phase 1 and a
        single request can block for minutes — the 60s default false-fails
        POCO-sized projects.
        """
        return probe_timeout

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Fail fast without a CDB; add --compile-commands-dir for generated ones.

        Why: clangd silently returns empty refs with no CDB, and its walk-up
        search misses the hidden ``.codeboarding/cdb/`` sibling.
        """
        if not self._has_compilation_database(project_root):
            kind = detect_build_system(project_root)
            raise RuntimeError(
                f"No compile_commands.json or compile_flags.txt under {project_root}. " + install_hint_for(kind)
            )
        command = list(super().get_lsp_command(project_root))
        if locate_generated_cdb(project_root) is not None:
            command.append(f"--compile-commands-dir={(project_root / CDB_SUBDIR).resolve()}")
        return command

    @staticmethod
    def _has_compilation_database(project_root: Path) -> bool:
        return locate_user_cdb(project_root) is not None or locate_generated_cdb(project_root) is not None

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """``fallbackFlags`` is top-level — nesting under ``"clangd"`` is an editor convention clangd ignores."""
        return {"fallbackFlags": ["-std=c++20"]}

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """Debounce — clangd publishes diagnostics asynchronously with no quiescence signal."""
        client.wait_for_diagnostics_quiesce(idle_seconds=2.0, max_wait=60.0)

    def prepare_project(self, project_root: Path) -> None:
        """Optionally generate a ``compile_commands.json`` before clangd starts."""
        ensure_cdb(project_root)

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build names from namespace/class chain, not file path.

        Why: the .hpp/.cpp split would otherwise give the same method two
        distinct qualified names, breaking cross-file references. The
        no-parent case returns the bare name so dual-registration aliases
        stay strictly shorter than the canonical scoped entry (CallGraph
        picks longest-wins).
        """
        sym = _strip_template_args(symbol_name).replace("::", ".").strip()

        if parent_chain:
            # Drop ``kind=STRING`` parents: clangd surfaces namespace-wrapper
            # macros (``FMT_BEGIN_NAMESPACE``, ``BOOST_NAMESPACE_BEGIN``) as
            # SymbolKind=15 and they'd prefix every qualified name.
            parents = [_normalize_cpp_parent(name) for name, kind in parent_chain if kind != int(NodeType.STRING)]
            parents = [p for p in parents if p]
            if parents:
                qualified = ".".join(parents) + "." + sym
            else:
                qualified = sym
        else:
            qualified = sym

        if symbol_kind in CALLABLE_KINDS:
            signature = _extract_signature(detail)
            if signature is not None:
                qualified += signature
        return qualified

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces so package-dependency tracking sees them (mirrors C#/PHP)."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE
