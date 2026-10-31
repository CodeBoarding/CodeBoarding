"""C++ and CUDA analysis using clangd and the project's compiler configuration."""

from collections.abc import Iterator
from pathlib import Path

from static_analyzer.config import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.cpp_templates import complete_template_dependencies
from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.edge_builder import SymbolIndex
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import CallSite, SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.exceptions import WorkspaceSetupError


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

    def declared_apart(self, a: SymbolInfo, b: SymbolInfo) -> bool:
        return self.is_callable(a.kind) and self.is_callable(b.kind) and a.definition_location != b.definition_location

    def refine_document_symbols(
        self, file_path: Path, symbols: list[dict], inspector: SourceInspector, client: LSPClient
    ) -> list[dict]:
        candidates = inspector.cpp_local_callables(file_path)
        if not candidates:
            return symbols
        active = {
            (node["range"]["end"]["line"], node["range"]["end"]["character"])
            for node in _walk(client.document_ast(file_path))
            if node.get("kind") in {"Function", "Lambda"} and "range" in node
        }
        for candidate in candidates:
            end = candidate["range"]["end"]
            if (end["line"], end["character"]) not in active:
                continue
            position = _position(candidate["selectionRange"]["start"])
            existing = list(_walk({"children": symbols}))[1:]
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
        return symbols

    def complete_edges(
        self,
        ctx: EdgeBuildContext,
        source_files: list[Path],
        edges: dict[tuple[str, str], list[CallSite]],
        project_root: Path,
    ) -> None:
        index = SymbolIndex(ctx.symbol_table, ctx.source_inspector)
        unresolved: set[tuple[str, int, int]] = set()
        destructors: dict[str, list[SymbolInfo]] = {}
        for symbols in ctx.symbol_table.primary_file_symbols.values():
            for symbol in symbols:
                if symbol.name.startswith("~"):
                    destructors.setdefault(symbol.owner_qualified_name, []).append(symbol)
        for file_path in source_files:
            for symbol in ctx.symbol_table.primary_file_symbols.get(str(file_path), []):
                if not self.is_callable(symbol.kind):
                    continue
                for call in ctx.lsp.outgoing_calls(file_path, symbol.start_line, symbol.start_char):
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

            ast = ctx.lsp.document_ast(file_path)
            dependent = {
                _position(node["range"]["start"])
                for node in _walk_evaluated(ast)
                if node.get("kind") == "UnresolvedLookup" and "range" in node
            }
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
                if not definitions and (line, column) in dependent:
                    unresolved.add((str(file_path), line, column))
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

        if unresolved:
            complete_template_dependencies(ctx, source_files, project_root, unresolved, edges)
        for edge, sites in edges.items():
            edges[edge] = list({(site.file, site.line, site.column): site for site in sites}.values())

    def prepare_project(self, project_root: Path) -> None:
        """Require explicit compiler context; never run CMake or overwrite project configuration."""
        candidates = (
            project_root / ".clangd",
            project_root / "compile_flags.txt",
            project_root / "compile_commands.json",
            project_root / "build" / "compile_commands.json",
        )
        if not any(path.is_file() for path in candidates):
            raise WorkspaceSetupError(
                f"C++/CUDA analysis requires compiler configuration in {project_root}. "
                "Generate compile_commands.json in the root or build directory with "
                "CMAKE_EXPORT_COMPILE_COMMANDS=ON, or provide .clangd/compile_flags.txt. "
                "For another build directory, set CompileFlags.CompilationDatabase in .clangd. "
                "CUDA projects must configure toolkit includes and Clang-compatible flags; "
                "raw NVCC flags are not necessarily understood by clangd."
            )

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """Reject incomplete Clang ASTs instead of publishing plausible but incorrect edges."""
        client.wait_for_diagnostics_quiesce(idle_seconds=0.5, max_wait=10.0)
        errors = [
            f"{path}:{diagnostic.range.start.line + 1}: {diagnostic.message}"
            for path, diagnostics in client.get_collected_diagnostics().items()
            for diagnostic in diagnostics
            if diagnostic.severity == 1
        ]
        if errors:
            raise WorkspaceSetupError(
                f"clangd reported {len(errors)} C++/CUDA error(s); refusing incomplete analysis. "
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
