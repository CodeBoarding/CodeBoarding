"""Resolve dependent calls from actual compiler-instantiated template bodies."""

import os
from pathlib import Path

from clang.cindex import (
    CompilationDatabase,
    CompileCommand,
    Cursor,
    CursorKind,
    Diagnostic,
    Index,
    SourceLocation,
    TranslationUnitLoadError,
    conf,
)
import yaml

from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.edge_builder import EdgeMap, SymbolIndex
from static_analyzer.engine.models import CallSite
from static_analyzer.exceptions import WorkspaceSetupError


def complete_template_dependencies(
    ctx: EdgeBuildContext,
    source_files: list[Path],
    project_root: Path,
    unresolved: set[tuple[str, int, int]],
    edges: EdgeMap,
) -> None:
    """Project concrete instantiations onto their source declarations, never infer targets by spelling."""
    compiler = Index.create()
    symbols = SymbolIndex(ctx.symbol_table, ctx.source_inspector)
    project_files = set(ctx.symbol_table.file_symbols)
    databases: dict[Path, CompilationDatabase] = {}
    units = [path for path in source_files if path.suffix.lower() in {".cpp", ".cc", ".cxx", ".cu"}]
    for file_path in units or source_files:
        for command in _commands(file_path, project_root, databases):
            arguments = list(command.arguments)
            directory = Path(command.directory)
            args = [
                arg
                for arg in arguments[1:]
                if arg != "--"
                and os.path.normcase(os.path.normpath(directory / arg)) != os.path.normcase(str(file_path))
            ]
            if Path(arguments[0]).stem.lower() in {"cl", "clang-cl"}:
                args.insert(0, "--driver-mode=cl")
            args.append(f"-working-directory={directory}")
            try:
                unit = compiler.parse(str(file_path), args=args)
            except TranslationUnitLoadError as error:
                raise WorkspaceSetupError(
                    f"libclang could not parse {file_path} with its compilation database"
                ) from error
            errors = [str(diagnostic) for diagnostic in unit.diagnostics if diagnostic.severity >= Diagnostic.Error]
            if errors:
                raise WorkspaceSetupError(
                    f"Cannot resolve C++ template instantiations in {file_path}; libclang rejected the compiler context. "
                    "Provide complete compiler flags, target SDK and include paths. " + "\n".join(errors[:5])
                )

            pending = [unit.cursor]
            visited: set[Cursor] = set()
            while pending:
                cursor = pending.pop()
                if cursor in visited:
                    continue
                visited.add(cursor)
                if cursor.location.file and str((directory / cursor.location.file.name).resolve()) not in project_files:
                    continue
                pending.extend(cursor.get_children())
                if cursor.kind not in {CursorKind.CALL_EXPR, CursorKind.DECL_REF_EXPR, CursorKind.MEMBER_REF_EXPR}:
                    continue
                referenced = cursor.referenced
                if referenced is None:
                    continue
                # Ordinary TU traversal omits implicit bodies; a resolved reference retains them.
                if conf.lib.clang_getSpecializedCursorTemplate(referenced) is not None:
                    definition = referenced.get_definition()
                    if definition is not None:
                        pending.append(definition)
                if cursor.kind != CursorKind.CALL_EXPR:
                    continue
                position = _position(cursor.location, directory, ctx)
                if position is None or position not in unresolved:
                    continue
                path, line, column = position
                caller = ctx.symbol_table.find_containing_symbol(Path(path), line, column)
                definition = referenced.get_definition() or referenced
                target_position = _position(definition.location, directory, ctx)
                if caller is None or target_position is None:
                    continue
                target_path, target_line, target_column = target_position
                target = symbols.resolve(
                    {
                        "uri": Path(target_path).as_uri(),
                        "range": {"start": {"line": target_line, "character": target_column}},
                    }
                )
                if target is not None:
                    edges.setdefault((caller.qualified_name, target.qualified_name), []).append(
                        CallSite.from_lsp_position(path, line, column)
                    )


def _commands(file_path: Path, project_root: Path, databases: dict[Path, CompilationDatabase]) -> list[CompileCommand]:
    for parent in file_path.parents:
        config = parent / ".clangd"
        if config.is_file():
            for fragment in yaml.safe_load_all(config.read_text(encoding="utf-8")):
                if fragment and fragment.get("CompileFlags"):
                    raise WorkspaceSetupError(
                        f"C++ template resolution cannot reproduce CompileFlags in {config}. "
                        "Export those flags into compile_commands.json or compile_flags.txt before analysis."
                    )
        if parent == project_root:
            break
    folders = [folder for parent in file_path.parents for folder in (parent, parent / "build")]
    folder = next(
        (
            folder
            for folder in folders
            if any((folder / name).is_file() for name in ("compile_commands.json", "compile_flags.txt"))
        ),
        None,
    )
    if folder is None:
        raise WorkspaceSetupError(f"C++ template resolution needs a compilation database for {file_path}")
    if folder not in databases:
        databases[folder] = CompilationDatabase.fromDirectory(str(folder))
    commands = list(databases[folder].getCompileCommands(str(file_path)) or [])
    if not commands:
        raise WorkspaceSetupError(f"The compilation database in {folder} has no command for {file_path}")
    return commands


def _position(location: SourceLocation, directory: Path, ctx: EdgeBuildContext) -> tuple[str, int, int] | None:
    if location.file is None:
        return None
    path = (directory / location.file.name).resolve()
    line = location.line - 1
    text = ctx.source_inspector.get_source_line(path, line)
    if text is None:
        return None
    prefix = text.encode("utf-8")[: location.column - 1].decode("utf-8")
    return str(path), line, len(prefix.encode("utf-16-le")) // 2
