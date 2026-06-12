"""C++ language adapter using clangd."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.cdb import CdbResolution, resolve_cdb
from static_analyzer.constants import Language, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_constants import CALLABLE_KINDS

_OPERATOR_SYMBOL_CHARS = frozenset("<>=!+-*/%&|^~?,[]")


def _scan_operator_token(name: str, i: int) -> int | None:
    """If ``name[i:]`` starts an ``operator…`` token, return its end index.

    Returns ``None`` when ``i`` does not begin a fresh ``operator`` keyword
    (e.g. ``foperator`` or mid-identifier). Eats trailing whitespace plus
    either an identifier (``operator new``) or a punctuation run
    (``operator<=>``).
    """
    if not name.startswith("operator", i):
        return None
    if i > 0 and (name[i - 1].isalnum() or name[i - 1] == "_"):
        return None
    n = len(name)
    j = i + len("operator")
    while j < n and name[j] == " ":
        j += 1
    if j < n and (name[j].isalpha() or name[j] == "_"):
        while j < n and (name[j].isalnum() or name[j] == "_"):
            j += 1
    else:
        while j < n and name[j] in _OPERATOR_SYMBOL_CHARS:
            j += 1
    return j


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
        if depth == 0 and name[i] == "o":
            end = _scan_operator_token(name, i)
            if end is not None:
                out.extend(name[i:end])
                i = end
                continue
        ch = name[i]
        if ch == "<" and out and (out[-1].isalnum() or out[-1] == "_"):
            depth += 1
        elif ch == ">" and depth > 0:
            depth -= 1
        elif depth == 0:
            out.append(ch)
        i += 1
    if depth != 0:
        return name
    return "".join(out)


_SIGNATURE_WS_RE = re.compile(r"\s+")
_SIGNATURE_SCOPE_RE = re.compile(r"::")
_SIGNATURE_STD_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9_])std\.")


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
    # Normalize whitespace (``const  Entity &`` -> ``const Entity &``),
    # tighten trailing-``&``/``*`` (``Entity &`` -> ``Entity&``) and flatten
    # ``::`` to ``.`` to match the adapter's scope-operator convention.
    body = _SIGNATURE_WS_RE.sub(" ", body).strip()
    body = body.replace(" &", "&").replace(" *", "*")
    body = _SIGNATURE_SCOPE_RE.sub(".", body)
    # Why: clangd builds (22.x) drop the ``std::`` prefix from common
    # standard-library template tokens; older builds keep it. Strip ``std.``
    # at fresh identifier boundaries so signature hashes survive clangd
    # version drift (M11). ``mystd.vector`` and similar pseudo-namespaces
    # are preserved by the negative lookbehind.
    body = _SIGNATURE_STD_PREFIX_RE.sub("", body)
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

    def __init__(self) -> None:
        # Cached so get_lsp_command doesn't re-run detection/generation.
        self._cdb_resolution: CdbResolution | None = None

    @property
    def language(self) -> str:
        return "Cpp"

    @property
    def language_enum(self) -> Language:
        return Language.CPP

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
        search misses the hidden ``.codeboarding/cdb/`` sibling. Consumes
        the resolution cached by ``prepare_project`` so detection and
        generation only run once per root.
        """
        resolution = self._cdb_resolution or resolve_cdb(project_root)
        if resolution.cdb_dir is None:
            raise RuntimeError(
                f"No compile_commands.json or compile_flags.txt under {project_root}, and writing "
                f"synthesized fallback flags failed (is .codeboarding/ writable?). " + (resolution.error_hint or "")
            )
        command = list(super().get_lsp_command(project_root))
        # Only set --compile-commands-dir for generated CDBs; user CDBs are
        # found by clangd's walk-up search from each source file.
        if resolution.cdb_dir != project_root:
            command.append(f"--compile-commands-dir={resolution.cdb_dir.resolve()}")
        return command

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """``fallbackFlags`` is top-level — nesting under ``"clangd"`` is an editor convention clangd ignores."""
        return {"fallbackFlags": ["-std=c++20"]}

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """Debounce — clangd publishes diagnostics asynchronously with no quiescence signal."""
        client.wait_for_diagnostics_quiesce(idle_seconds=2.0, max_wait=60.0)

    def prepare_project(self, project_root: Path) -> None:
        """Resolve CDB once (optionally generating) and cache for ``get_lsp_command``."""
        self._cdb_resolution = resolve_cdb(project_root)

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build names from namespace/class chain, falling back to file stem.

        Why: the .hpp/.cpp split must give the same method one qualified
        name when a namespace/class parent exists. For no-parent globals
        (file-scope helpers, free functions), bare names collide across
        translation units (M11) — so prefix with ``file_path.stem`` to
        give each TU its own scope. Headers and matching sources share
        a stem, preserving cross-file refs within a TU.
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
                qualified = f"{file_path.stem}.{sym}"
        else:
            qualified = f"{file_path.stem}.{sym}"

        if symbol_kind in CALLABLE_KINDS:
            signature = _extract_signature(detail)
            if signature is not None:
                qualified += signature
        return qualified

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces so package-dependency tracking sees them (mirrors C#/PHP)."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE
