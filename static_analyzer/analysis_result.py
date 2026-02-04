import logging
import pickle
import sys
import tempfile
from pathlib import Path

from static_analyzer.graph import Node, CallGraph

logger = logging.getLogger(__name__)


class AnalysisCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def get(self, repo_hash: str) -> "StaticAnalysisResults | None":
        """Load cached results for the given repo hash, or None if not found/invalid."""
        cache_file = self.cache_dir / f"{repo_hash}.pkl"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "rb") as f:
                result = pickle.load(f)
            logger.info(f"Loaded static analysis from cache: {cache_file}")
            return result
        except Exception as e:
            logger.warning(f"Failed to load static analysis cache: {e}")
            return None

    def save(self, repo_hash: str, result: "StaticAnalysisResults") -> None:
        """Save results to cache using atomic write."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / f"{repo_hash}.pkl"

        data = pickle.dumps(result)
        size_mb = sys.getsizeof(data) / (1024 * 1024)
        logger.info(f"Static analysis cache size: {size_mb:.2f} MB")

        temp_fd, temp_path = tempfile.mkstemp(dir=self.cache_dir, suffix=".tmp")
        try:
            with open(temp_fd, "wb") as f:
                f.write(data)
            Path(temp_path).replace(cache_file)
            logger.info(f"Saved static analysis to cache: {cache_file}")
        except Exception as e:
            Path(temp_path).unlink(missing_ok=True)
            logger.warning(f"Failed to save static analysis cache: {e}")


class StaticAnalysisResults:
    def __init__(self):
        self.results: dict[str, dict] = {}

    def add_class_hierarchy(self, language: str, hierarchy):
        """
        Adds/merges a class hierarchy to the results.

        Supports multiple calls for the same language (e.g., monorepo with multiple subprojects).

        :param language: The programming language.
        :param hierarchy: A dict or list representing the class hierarchy.
        """
        if language not in self.results:
            self.results[language] = {}
        if "hierarchy" not in self.results[language]:
            self.results[language]["hierarchy"] = {}

        # Merge instead of overwrite
        if isinstance(hierarchy, dict):
            self.results[language]["hierarchy"].update(hierarchy)
        elif isinstance(hierarchy, list):
            # Handle list of dicts (legacy format)
            for item in hierarchy:
                if isinstance(item, dict):
                    self.results[language]["hierarchy"].update(item)

    def add_cfg(self, language: str, cfg: CallGraph):
        """
        Adds/merges a control flow graph (CFG) to the results.

        Supports multiple calls for the same language (e.g., monorepo with multiple subprojects).

        :param language: The programming language of the CFG.
        :param cfg: The control flow graph data.
        """
        if language not in self.results:
            self.results[language] = {}

        if "cfg" not in self.results[language]:
            self.results[language]["cfg"] = cfg
        else:
            # Merge nodes and edges from the new CFG into the existing one
            existing_cfg = self.results[language]["cfg"]
            for node in cfg.nodes.values():
                existing_cfg.add_node(node)
            for edge in cfg.edges:
                try:
                    existing_cfg.add_edge(edge.get_source(), edge.get_destination())
                except ValueError:
                    pass  # Skip duplicate edges

    def add_package_dependencies(self, language: str, dependencies):
        """
        Adds/merges package dependencies to the results.

        Supports multiple calls for the same language (e.g., monorepo with multiple subprojects).

        :param language: The programming language of the dependencies.
        :param dependencies: A dict of package dependencies.
        """
        if language not in self.results:
            self.results[language] = {}
        if "dependencies" not in self.results[language]:
            self.results[language]["dependencies"] = {}

        # Merge instead of overwrite
        if isinstance(dependencies, dict):
            self.results[language]["dependencies"].update(dependencies)

    def add_references(self, language: str, references: list[Node]):
        """
        Adds/merges source code references to the results.

        Supports multiple calls for the same language (e.g., monorepo with multiple subprojects).

        :param language: The programming language of the references.
        :param references: A list of source code references.
        """
        if language not in self.results:
            self.results[language] = {}
        if "references" not in self.results[language]:
            self.results[language]["references"] = {}

        # Merge instead of overwrite - keys are lower case for case-insensitive search
        for reference in references:
            self.results[language]["references"][reference.fully_qualified_name.lower()] = reference

    def get_cfg(self, language: str) -> CallGraph:
        """
        Retrieves the control flow graph for a specific language.

        :param language: The programming language of the CFG.
        :return: The control flow graph data or None if not found.
        """
        if language in self.results and "cfg" in self.results[language]:
            return self.results[language]["cfg"]
        raise ValueError(f"Control flow graph for language '{language}' not found in results.")

    def get_hierarchy(self, language: str) -> dict:
        """
        Retrieves the class hierarchy for a specific language.

        :param language: The programming language of the hierarchy.
        :return: dict {
                        class_qualified_name: {
                            "superclasses": [],
                            "subclasses": [],
                            "file_path": str(file_path),
                            "line_start": start_line,
                            "line_end": end_line }
                    }
        """
        if language in self.results and "hierarchy" in self.results[language]:
            return self.results[language]["hierarchy"]
        raise ValueError(f"Class hierarchy for language '{language}' not found in results.")

    def get_package_dependencies(self, language: str) -> dict:
        """
        Retrieves the package dependencies for a specific language.

        :param language: The programming language of the dependencies.
        :return: The package dependencies or None if not found.
        """
        if language in self.results and "dependencies" in self.results[language]:
            return self.results[language]["dependencies"]
        raise ValueError(f"Package dependencies for language '{language}' not found in results.")

    def get_reference(self, language: str, qualified_name: str) -> Node:
        """
        Retrieves the source code reference for a specific qualified name in a language.

        :param language: The programming language of the reference.
        :param qualified_name: The fully qualified name of the source code element.
        :return: The source code reference or None if not found.
        """
        lower_qn = qualified_name.lower()
        if (
            language in self.results
            and "references" in self.results[language]
            and lower_qn in self.results[language]["references"]
        ):
            return self.results[language]["references"][lower_qn]
        # Check if the qualified name is a subset meaning it is a file path:
        if language in self.results and "references" in self.results[language]:
            for ref in self.results[language]["references"].keys():
                if ref.startswith(lower_qn):
                    raise FileExistsError(
                        f"Source code reference for '{qualified_name}' in language '{language}' is a file path, "
                        f"please use the full file path instead of the qualified name."
                    )
        raise ValueError(f"Source code reference for '{qualified_name}' in language '{language}' not found in results.")

    def get_loose_reference(self, language: str, qualified_name: str) -> tuple[str | None, Node | None]:
        lower_qn = qualified_name.lower()
        if language in self.results and "references" in self.results[language]:
            # Check if the qualified name is a subset of any reference:
            subset_refs = []
            for ref in self.results[language]["references"].keys():
                if ref.endswith(lower_qn):
                    return (
                        f"Found a loose match with a fully quantified name: {ref}",
                        self.results[language]["references"][ref],
                    )
                if lower_qn in ref:
                    subset_refs.append(ref)
            if len(subset_refs) == 1:
                return subset_refs[0], self.results[language]["references"][subset_refs[0]]
        return None, None

    def get_languages(self):
        """
        Retrieves the list of languages for which results are available.

        :return: A list of programming languages.
        """
        return list(self.results.keys())

    def add_source_files(self, language: str, source_files):
        """
        Adds/extends source files to the analysis results.

        Supports multiple calls for the same language (e.g., monorepo with multiple subprojects).

        :param language: The programming language.
        :param source_files: A list of source files.
        """
        if language not in self.results:
            self.results[language] = {}
        if "source_files" not in self.results[language]:
            self.results[language]["source_files"] = []

        # Extend instead of overwrite
        self.results[language]["source_files"].extend(source_files)

    def get_source_files(self, language: str) -> list[str]:
        """
        Retrieves the list of source files for a given language.

        :param language: The programming language.
        :return: A list of source files.
        """
        if language not in self.results:
            return []
        return self.results[language].get("source_files", [])

    def get_all_source_files(self) -> list[str]:
        """
        Retrieves the list of all source files across all languages.

        :return: A list of source files.
        """
        all_source_files = []
        for language in self.results:
            all_source_files.extend(self.get_source_files(language))
        return all_source_files
