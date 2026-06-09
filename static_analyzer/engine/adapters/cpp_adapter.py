"""C++ language adapter using clangd."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

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


def _find_param_open_paren(detail: str) -> tuple[int, bool]:
    """Find the index of the parameter list's opening ``(`` in ``detail``.

    Why: a naive ``detail.find("(")`` matches the inner parens of
    ``operator()`` / ``operator[]`` overloads, so two overloads with
    different argument types both yield an empty body and collide.
    Scans forward skipping any ``operator…`` token (and the ``()`` that
    follows ``operator`` when it's the call-operator) before locating
    the first param-list ``(``.

    Returns ``(open_idx, after_operator)`` where ``after_operator`` is
    True when the returned ``(`` is preceded by an ``operator…`` token
    we just scanned (i.e. the operator is the immediate owner of these
    parens). The flag lets ``_extract_signature`` distinguish empty
    ``operator double()`` (which needs a ``"()"`` suffix to disambiguate
    overloaded conversion operators) from empty ``() const -> void``
    (a regular no-arg method, which gets no suffix).
    """
    n = len(detail)
    i = 0
    saw_operator = False
    while i < n:
        if detail[i] == "o":
            # body_start = position after ``operator`` + whitespace.
            # If ``_scan_operator_token`` returns body_start, no body
            # was consumed -> true ``operator()`` (the next ``()`` is the
            # operator's own parens, not the param list). If end >
            # body_start, a body was consumed (``operator double``,
            # ``operator new``, ``operator+``) -> the next ``(`` IS the
            # param list, so do not step over.
            body_start = i + len("operator")
            while body_start < n and detail[body_start] == " ":
                body_start += 1
            end = _scan_operator_token(detail, i)
            if end is not None:
                saw_operator = True
                if end == body_start and end + 1 < n and detail[end] == "(" and detail[end + 1] == ")":
                    i = end + 2
                else:
                    i = end
                continue
        if detail[i] == "(":
            return i, saw_operator
        i += 1
    return -1, saw_operator


def _extract_signature(detail: str) -> str | None:
    """Extract a normalized ``(param_types)`` suffix from a clangd ``detail``.

    Returns ``None`` when a suffix would add no disambiguation value (no
    ``(``, empty parens on a regular method, unbalanced parens). Empty
    parens on a conversion operator (``operator double()``) DO get a
    ``"()"`` suffix -- without it, overloaded conversion operators on
    the same class collide downstream.
    """
    if not detail:
        return None
    open_idx, after_operator = _find_param_open_paren(detail)
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
        # Conversion operators (``operator double()``) need a suffix so
        # ``operator int()`` doesn't collide via downstream signature
        # hashing; regular no-arg methods (``() const -> void``) get None.
        return "()" if after_operator else None
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

    def language_id_for_file(self, file_path: Path) -> str:
        """Announce the file's true dialect to clangd.

        Why: ``Language.CPP``'s extension set includes ``.c``/``.h`` so the
        adapter walks both dialects in one clangd process, but didOpen must
        report ``"c"`` for ``.c`` sources -- otherwise clangd parses them
        as C++ and rejects C-only syntax (``_Atomic``, ``restrict``,
        designated initializers). ``.h`` headers ride as C++ here: in
        mixed C/C++ projects they are overwhelmingly C++ (POCO, LLVM,
        Boost, ...) and announcing them as C drops every namespace /
        template / class symbol. Pure-C projects route through
        ``CAdapter`` which overrides this to always return ``"c"``.
        """
        if file_path.suffix == ".c":
            return "c"
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
        generation only run once per root. Re-stats the resolved file
        before launch so a ``make clean`` between ``prepare_project`` and
        here doesn't silently start clangd against a deleted CDB.
        """
        resolution = self._cdb_resolution or resolve_cdb(project_root)
        if resolution.cdb_dir is None:
            raise RuntimeError(
                f"No compile_commands.json or compile_flags.txt under {project_root}. " + (resolution.error_hint or "")
            )
        cdb_dir = resolution.cdb_dir
        # O(1) re-stat -- detection already ran; only confirm the chosen
        # file still exists. Both subdir user CDBs (build/, src/, ...)
        # and root user CDBs accept either compile_commands.json or
        # compile_flags.txt, matching ``locate_user_cdb``. Generated CDBs
        # under ``.codeboarding/cdb/`` are always compile_commands.json
        # but the broader check is harmless there.
        still_present = (cdb_dir / "compile_commands.json").is_file() or (cdb_dir / "compile_flags.txt").is_file()
        if not still_present:
            raise RuntimeError(
                f"No compile_commands.json or compile_flags.txt under {project_root}. " + (resolution.error_hint or "")
            )
        command = list(super().get_lsp_command(project_root))
        if resolution.needs_compile_commands_dir:
            command.append(f"--compile-commands-dir={cdb_dir.resolve()}")
        return command

    # Why: post-collapse this adapter also indexes ``.c`` files in mixed
    # projects, so a hard-coded ``-std=c++20`` here would force C-dialect
    # fallback flags for files not in the CDB. Let clangd use its
    # dialect-aware defaults (driven by ``language_id_for_file``); CAdapter
    # still pins ``-std=c17`` for pure-C projects via its own override.

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
        a stem, preserving cross-file refs within a TU. Namespaces are
        exempt: one namespace spans many files and must keep one name,
        so stem-prefixing would split it per file.
        """
        sym = _strip_template_args(symbol_name).replace("::", ".").strip()
        # Why: ``Path.stem`` only strips the final suffix, so ``simd.x86.cpp``
        # yields ``simd.x86``. Without normalising, the free function ``baz``
        # there would qualify as ``simd.x86.baz`` and collide with the method
        # ``simd::x86::baz`` from another TU.
        stem = file_path.stem.replace(".", "_")
        no_parent_name = sym if symbol_kind == int(NodeType.NAMESPACE) else f"{stem}.{sym}"

        if parent_chain:
            # Drop ``kind=STRING`` parents: clangd surfaces namespace-wrapper
            # macros (``FMT_BEGIN_NAMESPACE``, ``BOOST_NAMESPACE_BEGIN``) as
            # SymbolKind=15 and they'd prefix every qualified name.
            parents = [_normalize_cpp_parent(name) for name, kind in parent_chain if kind != int(NodeType.STRING)]
            parents = [p for p in parents if p]
            if parents:
                qualified = ".".join(parents) + "." + sym
            else:
                qualified = no_parent_name
        else:
            qualified = no_parent_name

        if symbol_kind in CALLABLE_KINDS:
            signature = _extract_signature(detail)
            if signature is not None:
                qualified += signature
        return qualified

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces so package-dependency tracking sees them (mirrors C#/PHP)."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE
