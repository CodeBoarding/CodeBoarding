"""Type-reference edges from source: what names a type without calling it.

Runs over a language's merged graph rather than inside one engine, so a name resolves
across every sub-project of a monorepo the same way — a template's ``AbpModule`` finds
the framework's class even though its own solution only sees it as a package reference.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from static_analyzer.cfg import CallGraph, CallSiteLocation, EdgeKind, ReferenceEdge
from static_analyzer.engine.models import DEFAULT_EXPORT_NAME, ImportDirective, NameScope, TypeReferenceSite
from static_analyzer.engine.source_inspector import SourceInspector, simple_type_name, split_type_name
from static_analyzer.internal_references import is_self_or_container_edge
from static_analyzer.node import Node

logger = logging.getLogger(__name__)

_SCRIPT_MODULE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
# What marks the root of a compilation, by source suffix: a ``global using`` governs every
# file under it. Languages without an entry have no compilation-wide directive.
_COMPILATION_MARKERS = {".cs": "*.csproj"}


@dataclass
class TypeReferenceStats:
    sites: int = 0
    resolved: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    edges: int = 0
    unresolved_names: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # Listed source files the pass could not read: their references are missing from the graph.
    unreadable_files: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = (
            f"{self.sites} sites -> {self.edges} edges "
            f"({self.resolved} resolved, {self.unresolved} unknown, {self.ambiguous} ambiguous)"
        )
        if self.unreadable_files:
            text += f", {len(self.unreadable_files)} unreadable file(s)"
        return text


class TypeIndex:
    """Class-like symbols by simple name, and what each file can name where.

    A written name is matched against *containers*: the dotted path source writes to reach a
    declaration. In C# that is the declared namespace and the types enclosing it, read from the
    file; in TS/JS it is the module, which is the file's own path. The graph's qualified names
    stay path-based in every language; a container only decides what a written name means.
    """

    def __init__(self, nodes: Iterable[Node], inspector: SourceInspector) -> None:
        self._inspector = inspector
        self._by_name: dict[str, list[Node]] = defaultdict(list)
        self._containers_by_file: dict[str, list[Node]] = defaultdict(list)
        self._scope_cache: dict[str, NameScope] = {}
        self._container_cache: dict[str, str] = {}
        # Every container an indexed type sits in, with its prefixes: what a relative using can name.
        self._known_containers: set[str] = set()
        # ``global using`` directives by the compilation that declared them.
        self._compilation_imports: dict[str, list[ImportDirective]] = defaultdict(list)
        self._compilation_cache: dict[str, str] = {}
        # What each TS/JS module exports as ``default``, by module stem.
        self._default_export_by_module: dict[str, str] = {}
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

    def container_of(self, node: Node) -> str:
        """The path source writes to reach ``node``: namespace and enclosing types in C#, the module in TS/JS."""
        cached = self._container_cache.get(node.fully_qualified_name)
        if cached is None:
            if _is_script(node.file_path):
                cached = _module_stem(node.file_path)
            else:
                enclosing = self._enclosing_types(node.file_path, node.line_start, node.line_end, node)
                names = [simple_type_name(outer.fully_qualified_name) for outer in enclosing]
                cached = ".".join(
                    part for part in (self._namespace_at(node.file_path, node.line_start), *names) if part
                )
            self._container_cache[node.fully_qualified_name] = cached
        return cached

    def scope_of(self, file_path: str) -> NameScope:
        """The file's name scope, read once, registering what other files can see of it.

        Why other files: a ``global using`` governs every file of its compilation and a module's
        ``export default`` names what an importer's default binding means, so every file's scope
        is read before any site is resolved (``build_type_references``).
        """
        cached = self._scope_cache.get(file_path)
        if cached is not None:
            return cached
        scope = self._scope_cache[file_path] = self._inspector.find_name_scope(Path(file_path))
        compilation_wide = [directive for directive in scope.imports if directive.compilation_wide]
        if compilation_wide:
            self._compilation_imports[self._compilation_root(file_path)].extend(compilation_wide)
        if scope.default_export:
            self._default_export_by_module[_module_stem(file_path)] = scope.default_export
        for node in self._containers_by_file.get(file_path, ()):
            if node.is_class():
                self._known_containers.update(_prefixes(self.container_of(node)))
        return scope

    def resolve(self, site: TypeReferenceSite) -> Node | None:
        """The declaration ``site`` names, or ``None`` when unknown or not decidable."""
        if _is_script(site.file_path):
            return self._resolve_script(site)
        return self._resolve_csharp(site)

    def _resolve_csharp(self, site: TypeReferenceSite) -> Node | None:
        """C# lookup: an alias in scope decides outright; otherwise the scopes around the site, inward out.

        A name unique in the graph resolves from anywhere: the graph holds only the repository's
        own types, so an import it cannot see (implicit usings, a package) is the usual reason a
        name is not in scope, not a sign that it means something else.
        """
        written = f"{site.qualifier}.{site.name}" if site.qualifier else site.name
        head, _, rest = written.partition(".")
        alias = self._binding_at(site.file_path, site.line, head)
        if alias is not None:
            # C# 7.8: the alias decides the leftmost segment (``using Io = System.IO;`` makes
            # ``Io.File`` ``System.IO.File``) and never competes with a namespace import, so a
            # miss means the target is outside the graph, not that it is ambiguous.
            full = ".".join(part for part in (alias.container, alias.name, rest) if part)
            bound = self._in_container(*split_type_name(full))
            return _closest(bound, site.file_path) if bound else None
        candidates = [node for node in self._in_container(site.qualifier, site.name) if self._is_nameable(node, site)]
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        # What the site did not write must be reachable from where it stands: the first scope,
        # inward out, that holds any candidate's unwritten prefix decides.
        for scope in self._lookup_scopes(site.file_path, site.line):
            found = [node for node in candidates if _unwritten_prefix(self.container_of(node), site.qualifier) in scope]
            if found:
                return found[0] if len(found) == 1 else _closest(found, site.file_path)
        return _closest(candidates, site.file_path)

    def _resolve_script(self, site: TypeReferenceSite) -> Node | None:
        """TS/JS lookup: an import decides the module, else the name must be unique in the file or the graph."""
        binding = self._binding_at(site.file_path, site.line, site.qualifier or site.name)
        if binding is None:
            candidates = self._by_name.get(site.name, [])
            own = _module_stem(site.file_path)
            same_module = [node for node in candidates if self.container_of(node) == own]
            if len(same_module) == 1:
                return same_module[0]
            return candidates[0] if len(candidates) == 1 else None
        if not binding.container.startswith("."):
            return None  # a package import is external, even when a repo class shares the name
        module = _module_stem(os.path.normpath(os.path.join(os.path.dirname(site.file_path), binding.container)))
        if binding.name == DEFAULT_EXPORT_NAME:
            # The module says what its default is; an importer may bind it under any name.
            wanted = self._default_export_by_module.get(module, site.name)
        else:
            # A namespace import (``NS.Thing``) names the member written after it.
            wanted = binding.name or site.name
        candidates = [
            node
            for node in self._by_name.get(wanted, [])
            if self.container_of(node) == module or node.file_path.startswith(module + os.sep)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _in_container(self, qualifier: str, name: str) -> list[Node]:
        """Indexed types of this simple name whose container is, or ends in, the written qualifier.

        Why a suffix: a name written from inside an ancestor namespace is only partially qualified
        (``Domain.Order`` in ``namespace Lib`` against ``Lib.Domain.Order``). Segment-bounded, so
        ``Domain`` never matches ``FooDomain``.
        """
        candidates = self._by_name.get(name, [])
        if not qualifier:
            return candidates
        return [node for node in candidates if _container_matches(self.container_of(node), qualifier)]

    def _is_nameable(self, node: Node, site: TypeReferenceSite) -> bool:
        """Whether the name ``site`` wrote can mean ``node`` from where it stands.

        Only a type nested in another type is asked. A namespace can be opened by an import
        this layer cannot see, so a top-level type answers from anywhere; a nested type is
        named unqualified only inside its enclosing type or under ``using static``, and
        without the rule a repository's ``TranslateCommand.Options.File`` answers every
        ``File`` in the project.
        """
        unwritten = _unwritten_prefix(self.container_of(node), site.qualifier)
        if unwritten == self._namespace_at(node.file_path, node.line_start):
            return True
        return any(unwritten in scope for scope in self._lookup_scopes(site.file_path, site.line))

    def _binding_at(self, file_path: str, line: int, local_name: str) -> ImportDirective | None:
        """The import in force at ``line`` that binds ``local_name``, the compilation's global ones included."""
        for directive in self._imports_in_force(file_path, line):
            if directive.local_name == local_name:
                return directive
        return None

    def _lookup_scopes(self, file_path: str, line: int) -> Iterator[set[str]]:
        """The containers a simple name at ``line`` is looked up in, in C#'s order.

        The types enclosing the site, innermost first, each for what it nests. Then every
        namespace enclosing the site, innermost first, and the global namespace last: what
        the namespace itself holds, then what the usings written in that namespace's own
        declaration open. Compilation-unit usings, the ``global`` ones included, belong to
        the global namespace. Why the order matters: a using written after a file-scoped
        namespace beats a project-wide one that brings a second type of the same name.
        """
        namespace = self._namespace_at(file_path, line)
        own = self._container_path_at(file_path, line)
        while own != namespace:
            yield {own}
            own = own.rpartition(".")[0]
        in_force = self._imports_in_force(file_path, line)
        for level in [*sorted(_prefixes(namespace), key=len, reverse=True), ""]:
            yield {level}
            opened = {
                self._opened_container(directive, file_path)
                for directive in in_force
                if not directive.local_name and self._declared_in(directive, file_path) == level
            }
            if opened:
                yield opened

    def _declared_in(self, directive: ImportDirective, file_path: str) -> str:
        """The namespace a using is written in; a ``global using`` precedes every namespace."""
        return "" if directive.compilation_wide else self._namespace_at(file_path, directive.first_line)

    def _imports_in_force(self, file_path: str, line: int) -> list[ImportDirective]:
        own = [
            directive
            for directive in self.scope_of(file_path).imports
            if directive.first_line <= line <= directive.last_line
        ]
        compilation = self._compilation_imports.get(self._compilation_root(file_path), [])
        return own + [directive for directive in compilation if directive not in own]

    def _opened_container(self, directive: ImportDirective, file_path: str) -> str:
        """The container a plain using names, resolved from the namespace it is written in outward.

        Why: inside ``namespace Root``, ``using Models;`` means ``Root.Models`` when that exists.
        """
        enclosing = self._declared_in(directive, file_path)
        for prefix in sorted(_prefixes(enclosing), key=len, reverse=True):
            candidate = f"{prefix}.{directive.container}"
            if candidate in self._known_containers:
                return candidate
        return directive.container

    def _container_path_at(self, file_path: str, line: int) -> str:
        """The container a site at ``line`` is written in: its namespace, then the types around it."""
        names = [simple_type_name(node.fully_qualified_name) for node in self._enclosing_types(file_path, line, line)]
        return ".".join(part for part in (self._namespace_at(file_path, line), *names) if part)

    def _enclosing_types(
        self, file_path: str, first_line: int, last_line: int, exclude: Node | None = None
    ) -> list[Node]:
        """The class-like declarations whose range holds the lines, outermost first."""
        found: list[Node] = []
        for node in self._containers_by_file.get(file_path, ()):
            if node.line_start > first_line:
                break
            if node is not exclude and node.is_class() and node.line_end >= last_line:
                found.append(node)
        return found

    def _namespace_at(self, file_path: str, line: int) -> str:
        return _innermost_namespace(self.scope_of(file_path).namespaces, line)

    def _compilation_root(self, file_path: str) -> str:
        """The nearest directory above the file holding a project file, else empty: one compilation."""
        marker = _COMPILATION_MARKERS.get(os.path.splitext(file_path)[1].lower())
        if marker is None:
            return ""
        directory = os.path.dirname(file_path)
        visited: list[str] = []
        root = ""
        while directory not in self._compilation_cache:
            visited.append(directory)
            if next(Path(directory).glob(marker), None) is not None:
                root = directory
                break
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
        else:
            root = self._compilation_cache[directory]
        for seen in visited:
            self._compilation_cache[seen] = root
        return root


def build_type_references(
    inspector: SourceInspector, index: TypeIndex, source_files: Iterable[Path], stats: TypeReferenceStats
) -> list[ReferenceEdge]:
    """A TYPEREF edge for every resolvable type mention in ``source_files``, with the sites that made it."""
    # Every file is read before any site is resolved: a ``global using`` in one file governs
    # its siblings, and a module's ``export default`` names what an importer binds.
    scanned: list[list[TypeReferenceSite]] = []
    for file_path in source_files:
        sites = inspector.find_type_reference_sites(file_path)
        if sites is None:
            stats.unreadable_files.append(str(file_path))
            continue
        index.scope_of(str(file_path))
        scanned.append(sites)

    sites_by_pair: dict[tuple[str, str], list[CallSiteLocation]] = {}
    for sites in scanned:
        for site in sites:
            stats.sites += 1
            container = index.container_at(site.file_path, site.line)
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
            pair = (container.fully_qualified_name, target.fully_qualified_name)
            # The site is in the source's own file, so its position is all the edge needs.
            sites_by_pair.setdefault(pair, []).append(CallSiteLocation(line=site.line, column=site.column))
    return [
        ReferenceEdge(source, target, EdgeKind.TYPEREF, tuple(sites))
        for (source, target), sites in sites_by_pair.items()
    ]


def complete_type_references(
    graph: CallGraph, source_files: Iterable[Path], inspector: SourceInspector
) -> TypeReferenceStats:
    """Replace the graph's TYPEREF edges with what its source files name today."""
    graph.reference_edges = [ref for ref in graph.reference_edges if ref.kind is not EdgeKind.TYPEREF]
    stats = TypeReferenceStats()
    index = TypeIndex(graph.nodes.values(), inspector)
    # A base written in a base list is the inheritance the graph already records, not a second edge.
    inherited = {(ref.src, ref.dst) for ref in graph.reference_edges if ref.kind is EdgeKind.INHERITS}
    for edge in build_type_references(inspector, index, source_files, stats):
        if (edge.src, edge.dst) not in inherited:
            graph.add_reference_edge(edge)
    stats.edges = sum(1 for ref in graph.reference_edges if ref.kind is EdgeKind.TYPEREF)
    return stats


def _is_script(file_path: str) -> bool:
    return file_path.lower().endswith(_SCRIPT_MODULE_SUFFIXES)


def _prefixes(container: str) -> set[str]:
    """``A.B.C`` -> ``{A, A.B, A.B.C}``; empty for the global namespace."""
    segments = container.split(".") if container else []
    return {".".join(segments[:count]) for count in range(1, len(segments) + 1)}


def _container_matches(container: str, qualifier: str) -> bool:
    return container == qualifier or container.endswith("." + qualifier)


def _unwritten_prefix(container: str, qualifier: str) -> str:
    """What a site left unwritten of a candidate's container: ``Lib`` for ``Lib.Domain`` written as ``Domain``."""
    if not qualifier:
        return container
    return "" if container == qualifier else container[: -len(qualifier) - 1]


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
