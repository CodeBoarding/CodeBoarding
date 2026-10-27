"""C++ and CUDA analysis using clangd and the project's compiler configuration."""

from pathlib import Path

from static_analyzer.config import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
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
