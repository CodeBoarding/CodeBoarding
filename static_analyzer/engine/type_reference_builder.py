"""Type-reference edges from source: what names a type without calling it.

Runs over a language's merged graph rather than inside one engine, so a name resolves
across every sub-project of a monorepo the same way — a template's ``AbpModule`` finds
the framework's class even though its own solution only sees it as a package reference.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.engine.models import ImportBinding, NamespaceContext, TypeReferenceSite, UsingDirective
from static_analyzer.engine.source_inspector import (
    _PYTHON_SUFFIXES,
    _SCRIPT_SUFFIXES,
    SourceInspector,
    simple_type_name,
    split_type_name,
)
from static_analyzer.internal_references import is_self_or_container_edge
from static_analyzer.node import Node

logger = logging.getLogger(__name__)

_NAMESPACE_IMPORT = "*"
_SCRIPT_MODULE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")


@dataclass
class TypeReferenceStats:
    sites: int = 0
    resolved: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    edges: int = 0
    unresolved_names: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def summary(self) -> str:
        return (
            f"{self.sites} sites -> {self.edges} edges "
            f"({self.resolved} resolved, {self.unresolved} unknown, {self.ambiguous} ambiguous)"
        )


class TypeIndex:
    """Class-like symbols by simple name, and what each file can name unqualified."""

    def __init__(self, nodes: Iterable[Node], inspector: SourceInspector) -> None:
        self._inspector = inspector
        self._by_name: dict[str, list[Node]] = defaultdict(list)
        self._containers_by_file: dict[str, list[Node]] = defaultdict(list)
        self._namespace_cache: dict[str, NamespaceContext] = {}
        self._import_cache: dict[str, dict[str, ImportBinding]] = {}
        for node in nodes:
            if node.is_class():
                self._by_name[simple_type_name(node.fully_qualified_name)].append(node)
            if node.is_class() or node.is_callable():
                self._containers_by_file[node.file_path].append(node)
        for containers in self._containers_by_file.values():
            containers.sort(key=lambda node: (node.line_start, -(node.line_end - node.line_start)))

    def has_candidates(self, name: str) -> bool:
        """Whether any indexed type has this simple name (unresolved vs. undecidable, for stats)."""
        return bool(self._by_name.get(name))

    def container_at(self, file_path: str, line: int) -> Node | None:
        """The innermost callable or type declared around ``line``."""
        best: Node | None = None
        for node in self._containers_by_file.get(file_path, ()):
            if node.line_start > line:
                break
            if node.line_end >= line and (
                best is None or node.line_end - node.line_start <= best.line_end - best.line_start
            ):
                best = node
        return best

    def resolve(self, site: TypeReferenceSite) -> Node | None:
        """The declaration ``site`` names, or ``None`` when unknown or not decidable.

        Two families: a language that resolves a bare name against a declared scope and its
        imports (C#, Java), and one that binds each name to a module (TypeScript, Python).
        """
        suffix = os.path.splitext(site.file)[1].lower()
        if suffix in _SCRIPT_SUFFIXES:
            return self._resolve_script(site)
        if suffix in _PYTHON_SUFFIXES:
            return self._resolve_python(site)
        return self._resolve_by_namespace(site)

    def _resolve_by_namespace(self, site: TypeReferenceSite) -> Node | None:
        written = f"{site.qualifier}.{site.name}" if site.qualifier else site.name
        alias_target = _expand_alias(written, self._usings_at(site.file, site.line))
        if alias_target:
            # C# 7.8: an alias in scope decides the name outright. It never competes with a
            # namespace import, so a miss means the target is outside the graph, not ambiguous.
            bound = self._by_qualifier(alias_target)
            return _closest(bound, site.file) if bound else None
        candidates = self._by_qualifier(written)
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        visible = self._visible_namespaces(site.file, site.line)
        narrowed = [node for node in candidates if self._namespace_of(node) in visible]
        if len(narrowed) == 1:
            return narrowed[0]
        return _closest(narrowed or candidates, site.file)

    def _resolve_python(self, site: TypeReferenceSite) -> Node | None:
        bindings = self._imports_of(site.file)
        binding = bindings.get(site.qualifier or site.name)
        if binding is None:
            return self._only_local_candidate(site)
        wanted = site.name if binding.imported_name == _NAMESPACE_IMPORT else binding.imported_name
        module, anchored = _python_module(binding.source, site.file)
        candidates = [
            node for node in self._by_name.get(wanted, []) if _in_python_module(node.file_path, module, anchored)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _only_local_candidate(self, site: TypeReferenceSite) -> Node | None:
        """The name with no import behind it: this file's own, or the repository's only one."""
        candidates = self._by_name.get(site.name, [])
        same_file = [node for node in candidates if node.file_path == site.file]
        if len(same_file) == 1:
            return same_file[0]
        return candidates[0] if len(candidates) == 1 else None

    def _resolve_script(self, site: TypeReferenceSite) -> Node | None:
        bindings = self._imports_of(site.file)
        binding = bindings.get(site.qualifier or site.name)
        if binding is None:
            return self._only_local_candidate(site)
        if not binding.source.startswith("."):
            return None
        wanted = site.name if binding.imported_name in (_NAMESPACE_IMPORT, "default") else binding.imported_name
        module = _module_stem(os.path.normpath(os.path.join(os.path.dirname(site.file), binding.source)))
        candidates = [
            node
            for node in self._by_name.get(wanted, [])
            if _module_stem(node.file_path) == module or node.file_path.startswith(module + os.sep)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _by_qualifier(self, written: str) -> list[Node]:
        """Indexed types whose declared namespace matches the dotted prefix written before the name."""
        qualifier, name = split_type_name(written)
        candidates = self._by_name.get(name, [])
        if not qualifier:
            return candidates
        # ``endswith``, not equality: a name written from inside an ancestor namespace is only
        # partially qualified (``Domain.Order`` in ``namespace Lib`` against ``Lib.Domain.Order``).
        return [node for node in candidates if self._namespace_of(node).endswith(qualifier)]

    def _namespace_of(self, node: Node) -> str:
        return _innermost_namespace(self._namespace_context(node.file_path).namespaces, node.line_start)

    def _usings_at(self, file_path: str, line: int) -> list[UsingDirective]:
        """The directives in force at ``line``: the file's, plus the enclosing namespace block's."""
        return [
            directive
            for directive in self._namespace_context(file_path).usings
            if directive.first_line <= line <= directive.last_line
        ]

    def _visible_namespaces(self, file_path: str, line: int) -> set[str]:
        visible = {""}
        # Only a plain ``using N;`` makes a namespace nameable: an alias binds one name, and
        # ``using static N.T`` brings in T's members rather than T itself.
        visible.update(d.target for d in self._usings_at(file_path, line) if not d.alias and not d.static)
        enclosing = _innermost_namespace(self._namespace_context(file_path).namespaces, line)
        parts = enclosing.split(".") if enclosing else []
        visible.update(".".join(parts[:count]) for count in range(1, len(parts) + 1))
        return visible

    def _namespace_context(self, file_path: str) -> NamespaceContext:
        cached = self._namespace_cache.get(file_path)
        if cached is None:
            cached = self._namespace_cache[file_path] = self._inspector.find_namespace_context(Path(file_path))
        return cached

    def _imports_of(self, file_path: str) -> dict[str, ImportBinding]:
        cached = self._import_cache.get(file_path)
        if cached is None:
            cached = self._import_cache[file_path] = self._inspector.find_import_bindings(Path(file_path))
        return cached


def build_type_references(
    inspector: SourceInspector, index: TypeIndex, source_files: Iterable[Path], stats: TypeReferenceStats
) -> list[tuple[str, str]]:
    """``(source, target)`` qualified-name pairs for every resolvable type mention in ``source_files``."""
    pairs: dict[tuple[str, str], None] = {}
    for file_path in source_files:
        for site in inspector.find_type_reference_sites(file_path):
            stats.sites += 1
            container = index.container_at(site.file, site.line)
            if container is None:
                continue
            target = index.resolve(site)
            if target is None:
                if index.has_candidates(site.name):
                    stats.ambiguous += 1
                else:
                    stats.unresolved += 1
                    stats.unresolved_names[site.name] += 1
                continue
            stats.resolved += 1
            if is_self_or_container_edge(container.fully_qualified_name, target.fully_qualified_name):
                continue
            pairs.setdefault((container.fully_qualified_name, target.fully_qualified_name), None)
    return list(pairs)


def complete_type_references(
    graph: CallGraph, source_files: Iterable[Path], inspector: SourceInspector
) -> TypeReferenceStats:
    """Replace the graph's TYPEREF edges with what its source files name today."""
    graph.reference_edges = [ref for ref in graph.reference_edges if ref.kind is not EdgeKind.TYPEREF]
    stats = TypeReferenceStats()
    index = TypeIndex(graph.nodes.values(), inspector)
    for source, target in build_type_references(inspector, index, source_files, stats):
        graph.add_reference_edge(ReferenceEdge(source, target, EdgeKind.TYPEREF))
    stats.edges = sum(1 for ref in graph.reference_edges if ref.kind is EdgeKind.TYPEREF)
    return stats


def _expand_alias(written: str, directives: Iterable[UsingDirective]) -> str:
    """What an alias in scope binds the leftmost segment to, else empty.

    Why the leftmost only: C# looks the alias up for the first identifier of a name, which is
    what makes ``using Io = System.IO;`` resolve ``Io.File``.
    """
    head, _, rest = written.partition(".")
    for directive in directives:
        if directive.alias == head:
            return ".".join(part for part in (directive.target, rest) if part)
    return ""


def _python_module(source: str, site_file: str) -> tuple[str, bool]:
    """A Python import's module as a path, and whether it is anchored at a known directory.

    A relative import resolves against the importing file and so is anchored; an absolute one
    names a package whose root this layer does not know, and is matched as a path suffix.
    """
    if not source.startswith("."):
        return source.replace(".", os.sep), False
    depth = len(source) - len(source.lstrip("."))
    base = os.path.dirname(site_file)
    for _ in range(depth - 1):
        base = os.path.dirname(base)
    rest = source[depth:].replace(".", os.sep)
    return (os.path.join(base, rest) if rest else base), True


def _in_python_module(file_path: str, module: str, anchored: bool) -> bool:
    """Whether ``file_path`` is the module, as a file or as a package's ``__init__``."""
    stem = file_path[: -len(".py")] if file_path.endswith(".py") else file_path
    package = os.sep + "__init__"
    if stem.endswith(package):
        stem = stem[: -len(package)]
    return stem == module if anchored else stem == module or stem.endswith(os.sep + module)


def _module_stem(path: str) -> str:
    """The module a path names: no script extension, no trailing ``/index``.

    Why: a specifier and the file it resolves to spell the same module differently
    (``./svc.js`` -> ``svc.ts``, ``./lib/index.js`` -> ``lib/``), so both sides normalise.
    """
    root, suffix = os.path.splitext(path)
    stem = root if suffix.lower() in _SCRIPT_MODULE_SUFFIXES else path
    parent, name = os.path.split(stem)
    return parent if name == "index" and parent else stem


def _innermost_namespace(namespaces: Iterable[tuple[str, int, int]], line: int) -> str:
    best = ""
    best_span = -1
    for name, start, end in namespaces:
        if start <= line <= end and (best_span < 0 or end - start <= best_span):
            best, best_span = name, end - start
    return best


def _closest(candidates: list[Node], file_path: str) -> Node | None:
    """The unique candidate declared in the same file, else the one sharing the longest directory path."""
    same_file = [node for node in candidates if node.file_path == file_path]
    if len(same_file) == 1:
        return same_file[0]
    ranked = sorted(candidates, key=lambda node: -_common_prefix(node.file_path, file_path))
    if len(ranked) > 1 and _common_prefix(ranked[0].file_path, file_path) == _common_prefix(
        ranked[1].file_path, file_path
    ):
        return None
    return ranked[0]


def _common_prefix(left: str, right: str) -> int:
    try:
        return len(os.path.commonpath([left, right]))
    except ValueError:  # one absolute, one relative: nothing in common
        return 0
