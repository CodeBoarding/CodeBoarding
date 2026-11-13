"""C++ and CUDA analysis using clangd and the project's compiler configuration."""

from collections.abc import Iterator
import logging
from pathlib import Path

from clang.cindex import LibclangError

from static_analyzer.config import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.cpp_templates import complete_template_dependencies
from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.edge_builder import SymbolIndex
from static_analyzer.engine.lsp_client import LSPClient, MethodNotFoundError
from static_analyzer.engine.models import CallSite, SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.exceptions import WorkspaceSetupError
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap, LSPDiagnostic

logger = logging.getLogger(__name__)


class CppAdapter(LanguageAdapter):
    @property
    def language(self) -> str:
        return "CPP"

    @property
    def language_enum(self) -> Language:
        return Language.CPP

    @property
    def lsp_command(self) -> list[str]:
        return ["clangd", "--background-index", "--clang-tidy=false", "--header-insertion=never"]

    @property
    def fail_on_empty_symbols(self) -> bool:
        return True

    @property
    def interleave_did_open_with_symbols(self) -> bool:
        # A bulk open queues thousands of expensive template parses ahead of symbol requests.
        return True

    @property
    def resolves_bases_by_type_definition(self) -> bool:
        """clangd's type-hierarchy preparation can crash on dependent C++ bases."""
        return True

    def declared_apart(self, a: SymbolInfo, b: SymbolInfo) -> bool:
        return self.is_callable(a.kind) and self.is_callable(b.kind) and a.definition_location != b.definition_location

    def refine_document_symbols(
        self,
        file_path: Path,
        symbols: list[dict],
        inspector: SourceInspector,
        client: LSPClient,
        diagnostics: FileDiagnosticsMap,
    ) -> list[dict]:
        candidates = inspector.cpp_local_callables(file_path)
        active = {
            (node["range"]["end"]["line"], node["range"]["end"]["character"])
            for node in (_walk(self._document_ast(file_path, client, diagnostics)) if candidates else [])
            if node.get("kind") in {"Function", "Lambda"} and "range" in node
        }
        existing = list(_walk({"children": symbols}))[1:]
        for candidate in candidates:
            end = candidate["range"]["end"]
            if (end["line"], end["character"]) not in active:
                continue
            position = _position(candidate["selectionRange"]["start"])
            if any(_position(item["selectionRange"]["start"]) == position for item in existing):
                continue
            owners = [
                item
                for item in existing
                if _position(item["range"]["start"]) <= position < _position(item["range"]["end"])
            ]
            if owners:
                owner = max(owners, key=lambda item: _position(item["range"]["start"]))
                owner.setdefault("children", []).append(candidate)
            else:
                symbols.append(candidate)
            existing.extend(_walk(candidate))

        declarations = list(_walk({"children": symbols}))[1:]
        queries = [(file_path, *_position(item["selectionRange"]["start"])) for item in declarations]
        # USRs distinguish C++ overloads and identify redeclarations across files.
        for item, details in zip(declarations, client.symbol_info_batch(queries)):
            if len(details) != 1 or not details[0].get("usr"):
                continue
            identity = details[0]
            item["name"] = identity["name"]
            display = ((identity.get("containerName") or "") + identity["name"]).replace("::", ".")
            item["qualified_name"] = f"{display}[{identity['usr']}]"
            definition = identity.get("definitionRange") or {}
            item["is_definition"] = (
                definition.get("uri") == file_path.resolve().as_uri()
                and definition.get("range", {}).get("start") == item["selectionRange"]["start"]
            )
        return symbols

    def prepare_edges(self, ctx: EdgeBuildContext) -> None:
        for path in sorted({symbol.file_path for symbol in ctx.symbol_table.known}):
            ctx.lsp.did_open(path)

    def complete_edges(
        self,
        ctx: EdgeBuildContext,
        source_files: list[Path],
        edges: dict[tuple[str, str], list[CallSite]],
        project_root: Path,
    ) -> None:
        index = SymbolIndex(ctx.symbol_table, ctx.source_inspector)
        unresolved: set[tuple[str, int, int]] = set()
        dependent_ranges: dict[str, list[tuple[tuple[int, int], tuple[int, int]]]] = {}
        destructors: dict[str, list[SymbolInfo]] = {}
        for symbols in ctx.symbol_table.file_symbols.values():
            for symbol in symbols:
                if symbol.name.rsplit("::", 1)[-1].startswith("~"):
                    destructors.setdefault(symbol.owner_qualified_name, []).append(symbol)
        for file_path in source_files:
            callables = [
                (file_path, symbol.start_line, symbol.start_char)
                for symbol in ctx.symbol_table.primary_file_symbols.get(str(file_path), [])
                if self.is_callable(symbol.kind)
            ]
            try:
                outgoing = ctx.lsp.outgoing_calls_batch(callables)
            except MethodNotFoundError as error:
                raise RuntimeError(
                    "C++ analysis requires clangd 20 or newer for outgoing call hierarchy support. "
                    "Upgrade the configured clangd executable or use CodeBoarding's bundled clangd."
                ) from error
            for calls in outgoing:
                for call in calls:
                    item = call["to"]
                    target = index.resolve({"uri": item["uri"], "range": item["selectionRange"]})
                    if target is None:
                        continue
                    for source_range in call["fromRanges"]:
                        line, column = _position(source_range["start"])
                        caller = ctx.symbol_table.find_containing_symbol(file_path, line, column)
                        if caller is not None and self.is_callable(caller.kind):
                            site = CallSite.from_lsp_position(str(file_path), line, column)
                            edges.setdefault((caller.qualified_name, target.qualified_name), []).append(site)

            ast = self._document_ast(file_path, ctx.lsp, ctx.diagnostics)
            # CUDA calls through device functors/lambdas remain partial: clangd exposes
            # a parameter reference, not the instantiated operator(), at these call sites.
            dependent_nodes = [
                node
                for node in _walk_evaluated(ast)
                if node.get("kind") in {"UnresolvedLookup", "CXXDependentScopeMember"} and "range" in node
            ]
            dependent = {_position(node["range"]["start"]) for node in dependent_nodes}
            dependent_ranges[str(file_path)] = [
                (_position(node["range"]["start"]), _position(node["range"].get("end", node["range"]["start"])))
                for node in dependent_nodes
            ]
            unresolved.update((str(file_path), line, column) for line, column in dependent)
            references = [
                node["range"]["start"]
                for node in _walk_evaluated(ast)
                if node.get("kind") in {"DeclRef", "Member"} and "range" in node
            ]
            queries = sorted(
                {(file_path, *position) for position in dependent}
                | {(file_path, *_position(position)) for position in references}
            )
            for query, definitions in zip(queries, ctx.lsp.send_definition_batch(queries)):
                _, line, column = query
                caller = ctx.symbol_table.find_containing_symbol(file_path, line, column)
                if caller is None or not self.is_callable(caller.kind):
                    continue
                if (line, column) in dependent:
                    continue
                for definition in definitions:
                    target = index.resolve(definition)
                    if target is not None and self.is_callable(target.kind):
                        site = CallSite.from_lsp_position(str(file_path), line, column)
                        edges.setdefault((caller.qualified_name, target.qualified_name), []).append(site)

            constructions = [
                child["range"]["start"]
                for node in _walk_evaluated(ast)
                if node.get("kind") == "Var"
                for child in node.get("children", [])
                if child.get("kind") == "CXXConstruct" and "range" in child
            ]
            queries = [
                (file_path, *_position(position))
                for position in constructions
                if ctx.source_inspector.cpp_has_automatic_storage(file_path, *_position(position))
            ]
            for query, definitions in zip(queries, ctx.lsp.send_type_definition_batch(queries)):
                _, line, column = query
                caller = ctx.symbol_table.find_containing_symbol(file_path, line, column)
                if caller is None or not self.is_callable(caller.kind):
                    continue
                for definition in definitions:
                    owner = index.resolve(definition)
                    if owner is None:
                        continue
                    for destructor in destructors.get(owner.qualified_name, []):
                        site = CallSite.from_lsp_position(str(file_path), line, column)
                        edges.setdefault((caller.qualified_name, destructor.qualified_name), []).append(site)

        # Navigation candidates for dependent calls are not proven instantiations.
        for edge, sites in list(edges.items()):
            remaining = [
                site
                for site in sites
                if not any(
                    start <= (site.lsp_line, site.lsp_column) < end
                    for start, end in dependent_ranges.get(site.file, [])
                )
            ]
            if remaining:
                edges[edge] = remaining
            else:
                del edges[edge]
        if unresolved:
            try:
                complete_template_dependencies(ctx, source_files, project_root, unresolved, edges)
            except (WorkspaceSetupError, LibclangError) as error:
                message = (
                    "C++ template supplementation stopped; retaining resolved relationships. "
                    f"Template-dependent calls may be missing. {error}"
                )
                logger.exception(message)
                ctx.diagnostics.setdefault(str(project_root), []).append(
                    LSPDiagnostic(code="cpp-template-analysis-incomplete", message=message, severity=2)
                )
        for edge, sites in edges.items():
            edges[edge] = list({(site.file, site.line, site.column): site for site in sites}.values())

    def prepare_project(self, project_root: Path) -> None:
        """Prefer explicit compiler context without requiring a project build."""
        candidates = (
            project_root / ".clangd",
            project_root / "compile_flags.txt",
            project_root / "compile_commands.json",
            project_root / "build" / "compile_commands.json",
        )
        if not any(path.is_file() for path in candidates):
            logger.warning(
                f"C++/CUDA analysis has no compiler configuration in {project_root}; using clangd defaults. "
                "Relationships may be incomplete. "
                "Generate compile_commands.json in the root or build directory with "
                "CMAKE_EXPORT_COMPILE_COMMANDS=ON, or provide .clangd/compile_flags.txt. "
                "For another build directory, set CompileFlags.CompilationDatabase in .clangd. "
                "CUDA projects must configure toolkit includes and Clang-compatible flags; "
                "raw NVCC flags are not necessarily understood by clangd."
            )

    def wait_for_diagnostics(self, client: LSPClient, project_root: Path) -> None:
        """Retain compiler diagnostics without discarding resolved relationships."""
        client.wait_for_diagnostics_quiesce(idle_seconds=0.5, max_wait=10.0)
        errors = [
            f"{path}:{diagnostic.range.start.line + 1}: {diagnostic.message}"
            for path, diagnostics in client.get_collected_diagnostics().items()
            for diagnostic in diagnostics
            if diagnostic.severity == 1
        ]
        if errors:
            logger.warning(
                f"clangd reported {len(errors)} C++/CUDA error(s); analysis may be incomplete. "
                "Check the compilation database, toolkit/standard-library includes and unsupported compiler flags. "
                + "\n".join(errors[:5])
            )

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Keep overloads distinct and prevent same-stem headers and sources from colliding."""
        module = ".".join(file_path.relative_to(project_root).parts)
        parents = [name for name, _ in parent_chain]
        if self.is_callable(symbol_kind) and "(" in detail:
            symbol_name += detail[detail.index("(") :]
        return ".".join([module, *parents, symbol_name])

    def _document_ast(self, file_path: Path, client: LSPClient, diagnostics: FileDiagnosticsMap) -> dict:
        ast = client.document_ast(file_path)
        if ast is not None:
            return ast
        warnings = diagnostics.setdefault(str(file_path.resolve()), [])
        if not any(warning.code == "cpp-ast-unavailable" for warning in warnings):
            message = (
                f"clangd returned no AST for {file_path}; retaining resolved symbols and relationships. "
                "AST-dependent declarations and calls may be missing."
            )
            logger.warning(message)
            warnings.append(
                LSPDiagnostic(code="cpp-ast-unavailable", message=message, severity=2, source="codeboarding")
            )
        return {}


def _position(position: dict) -> tuple[int, int]:
    return position["line"], position["character"]


def _walk(node: dict) -> Iterator[dict]:
    yield node
    for child in node.get("children", []):
        yield from _walk(child)


def _walk_evaluated(node: dict) -> Iterator[dict]:
    if node.get("role") == "type" or node.get("kind") in {"UnaryExprOrTypeTrait", "CXXNoexcept", "Requires"}:
        return
    yield node
    for child in node.get("children", []):
        yield from _walk_evaluated(child)
