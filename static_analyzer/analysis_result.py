import logging
import pickle
import re
import sys
import tempfile
from pathlib import Path

from static_analyzer.constants import Node
from static_analyzer.graph import CallGraph
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap

logger = logging.getLogger(__name__)

# Matches a parenthesised expression, e.g. (Entity), (*Task), or (String, int).
# Used by _reference_key to distinguish Go receiver types (followed by '.') from
# Java/TS method signatures (at end of name or not followed by '.').
_PAREN_EXPR_RE = re.compile(r"\([^)]*\)")

# Matches Java/Kotlin generic type parameters to be stripped from method signatures,
# e.g. "<Animal>" in "List<Animal>" → "List", or trailing " <T, R>" → "".
_JAVA_GENERIC_RE = re.compile(r"<[^<>]*>")

# Matches a trailing type-parameter declaration that JDTLS appends to generic methods,
# e.g. " <t>" or " <t, r>" or " <t extends animal>" at the very end of the lowercased
# qualified name. Captured group 1 contains the comma-separated list of type param tokens.
_JAVA_TRAILING_TYPEPARAM_RE = re.compile(r"\s*<([a-z][a-z0-9,\s]*)>\s*$")

# Matches a word boundary-delimited word for type param substitution.
_WORD_RE = re.compile(r"\b([a-z]+)\b")

# Matches a standalone single lowercase letter (not preceded or followed by a word char).
# Used to detect generic type params like T or E in lowercased method signatures.
_STANDALONE_SINGLE_LETTER_RE = re.compile(r"(?<![a-z])([a-z])(?!\w)")


def _strip_java_generics(name: str) -> str:
    """Remove Java generic type params from a (already lowercased) qualified name.

    Steps:
    1. Extract type-param names from the trailing JDTLS type-param declaration
       (e.g. ``"firstordefault(t[], t) <t>"`` → type params = {``t``}).
    1b. Also detect single-letter standalone tokens inside ``(…)`` as type params
        (e.g. ``"firstordefault(t[], t)"`` where there is no trailing ``<t>``).
    2. Replace those bare type-param names inside ``(…)`` with ``object`` so that
       ``firstordefault(t[], t)`` → ``firstordefault(object[], object)``.
    3. Strip all ``<…>`` groups (including nested ones) until stable.
    4. Right-strip any residual whitespace.
    """
    # Step 1: collect type-param names from the trailing declaration
    type_params: set[str] = set()
    m = _JAVA_TRAILING_TYPEPARAM_RE.search(name)
    if m:
        # e.g. "t" or "t extends animal" → take the first token of each comma-separated part
        for token_group in m.group(1).split(","):
            first_token = token_group.strip().split()[0]
            # Only single-letter or all-caps short tokens are generic type params
            if len(first_token) <= 2:
                type_params.add(first_token)

    # Step 1b: also scan paren groups for single-letter standalone tokens —
    # JDTLS sometimes omits the trailing <T> declaration but still uses T in the sig.
    # e.g. "firstordefault(t[], t)" where t is a type param with no trailing <t>.
    for paren_match in _PAREN_EXPR_RE.finditer(name):
        for tok in _STANDALONE_SINGLE_LETTER_RE.finditer(paren_match.group(0)):
            type_params.add(tok.group(1))

    # Step 2: replace type param names inside (…) with "object"
    if type_params:

        def _replace_in_parens(match: re.Match) -> str:
            inner = match.group(0)  # includes the parens

            def _subst(w: re.Match) -> str:
                return "object" if w.group(1) in type_params else w.group(0)

            return _WORD_RE.sub(_subst, inner)

        name = _PAREN_EXPR_RE.sub(_replace_in_parens, name)

    # Step 3: strip <…> groups until stable
    prev = None
    while prev != name:
        prev = name
        name = _JAVA_GENERIC_RE.sub("", name)

    # Step 4: remove residual whitespace
    return name.rstrip()


def _reference_key(fully_qualified_name: str) -> str:
    """Return the canonical storage key for a reference.

    The key is case-normalised: everything is lowercased.  The only exception is
    content inside parentheses that is **immediately followed by '.'**, which
    corresponds to Go's receiver-method notation like ``(Entity).GetType``.  In that
    case the type name inside ``(…)`` keeps its original casing (producing the key
    ``(Entity).gettype``) while all other text is lowercased.

    For Java, generic type parameters are also stripped so that
    ``filterAnimals(List<Animal>, Predicate<Animal>)`` normalises to
    ``filteranimals(list, predicate)`` and matches fixtures that use erased types.
    """
    parts: list[str] = []
    last_end = 0
    for m in _PAREN_EXPR_RE.finditer(fully_qualified_name):
        parts.append(fully_qualified_name[last_end : m.start()].lower())
        # Only preserve case if this parenthesised group is a Go receiver type,
        # i.e. it is immediately followed by a dot (e.g. "(Entity).method").
        after = fully_qualified_name[m.end() : m.end() + 1]
        if after == ".":
            parts.append(m.group(0))  # preserve original casing for Go receivers
        else:
            parts.append(m.group(0).lower())  # lowercase Java/TS method params
        last_end = m.end()
    parts.append(fully_qualified_name[last_end:].lower())
    result = "".join(parts)
    # Strip Java generic type params (no-op for Go/Python/TS which have none).
    return _strip_java_generics(result)


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
        self.diagnostics: dict[str, FileDiagnosticsMap] = {}  # Language -> file_path -> diagnostics

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

        # Merge instead of overwrite.  Keys are normalised via _reference_key which lowercases
        # everything except parenthesised receiver-type expressions (Go method notation like
        # "(Entity).GetType" → stored as "(Entity).gettype").
        for reference in references:
            self.results[language]["references"][_reference_key(reference.fully_qualified_name)] = reference

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

        Lookup is case-insensitive: both the query and the stored keys are lowercased for
        comparison, so "models.base.(Entity).GetType" and "models.base.(entity).gettype"
        resolve to the same reference.

        :param language: The programming language of the reference.
        :param qualified_name: The fully qualified name of the source code element.
        :return: The source code reference or None if not found.
        """
        norm_qn = _reference_key(qualified_name)
        if language in self.results and "references" in self.results[language]:
            refs = self.results[language]["references"]
            if norm_qn in refs:
                return refs[norm_qn]
            # Check if the qualified name is a subset meaning it is a file path:
            for ref in refs.keys():
                if ref.startswith(norm_qn):
                    raise FileExistsError(
                        f"Source code reference for '{qualified_name}' in language '{language}' is a file path, "
                        f"please use the full file path instead of the qualified name."
                    )
        raise ValueError(f"Source code reference for '{qualified_name}' in language '{language}' not found in results.")

    def get_loose_reference(self, language: str, qualified_name: str) -> tuple[str | None, Node | None]:
        norm_qn = _reference_key(qualified_name)
        if language in self.results and "references" in self.results[language]:
            # Check if the qualified name is a subset of any reference:
            subset_refs = []
            for ref in self.results[language]["references"].keys():
                if ref.endswith(norm_qn):
                    return (
                        f"Found a loose match with a fully quantified name: {ref}",
                        self.results[language]["references"][ref],
                    )
                if norm_qn in ref:
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
