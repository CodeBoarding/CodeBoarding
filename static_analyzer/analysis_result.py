import copy
import logging
import pickle
import re
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

from static_analyzer.graph import CallGraph
from static_analyzer.language_results import LanguageResults
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap
from static_analyzer.node import Node
from utils import to_absolute_path, to_relative_path

logger = logging.getLogger(__name__)

# Matches a parenthesised expression, e.g. (Entity), (*Task), or (String, int).
# Used by _reference_key to distinguish Go receiver types (followed by '.') from
# Java/TS method signatures (at end of name or not followed by '.').
_PAREN_EXPR_RE = re.compile(r"\([^)]*\)")

# Matches Java/Kotlin generic type parameters to be stripped from method signatures,
# e.g. "<Animal>" in "List<Animal>" -> "List", or trailing " <T, R>" -> "".
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
       (e.g. ``"firstordefault(t[], t) <t>"`` -> type params = {``t``}).
    1b. Also detect single-letter standalone tokens inside ``(…)`` as type params
        (e.g. ``"firstordefault(t[], t)"`` where there is no trailing ``<t>``).
    2. Replace those bare type-param names inside ``(…)`` with ``object`` so that
       ``firstordefault(t[], t)`` -> ``firstordefault(object[], object)``.
    3. Strip all ``<…>`` groups (including nested ones) until stable.
    4. Right-strip any residual whitespace.
    """
    # Step 1: collect type-param names from the trailing declaration
    type_params: set[str] = set()
    m = _JAVA_TRAILING_TYPEPARAM_RE.search(name)
    if m:
        # e.g. "t" or "t extends animal" -> take the first token of each comma-separated part
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


# Run-artifact filenames. Stored in ``<repo>/.codeboarding/`` (sibling of
# ``analysis.json``), not under ``cache/`` — losing them costs a full LSP
# re-index, so they're not safe to wipe with the rest of the cache.
STATIC_ANALYSIS_PKL = "static_analysis.pkl"
STATIC_ANALYSIS_SHA = "static_analysis.sha"
# Legacy location ``StaticAnalysisCache`` wrote to before the run-artifact
# split. Kept for one-time read fallback so CLI users transition smoothly.
_LEGACY_PKL_NAME = "static_analysis_results.pkl"
_LEGACY_CACHE_SUBDIR = "cache"
# Tag file format prefix; bump if the on-disk pickle layout changes.
# v2: StaticAnalysisResults switched from dict-of-dicts to LanguageResults
# dataclass storage. v1 pickles will be treated as cache misses and re-run.
_TAG_VERSION = "v2"


class StaticAnalysisCache:
    """Reader/writer for the persistent static-analysis run artifact.

    Owns ``static_analysis.pkl`` (the relativised ``StaticAnalysisResults``
    pickle) and ``static_analysis.sha`` (a tag file recording the source
    SHA the pickle reflects). The artifact dir is the same directory that
    holds ``analysis.json``; it is *not* the wipeable ``cache/`` dir.
    """

    def __init__(self, artifact_dir: Path, repo_root: Path):
        self.artifact_dir = artifact_dir
        self.repo_root = repo_root.resolve()

    def _to_relative(self, path: str) -> str:
        return to_relative_path(path, self.repo_root)

    def _to_absolute(self, path: str) -> str:
        return to_absolute_path(path, self.repo_root)

    def _relativize(self, result: "StaticAnalysisResults") -> "StaticAnalysisResults":
        """Return a copy of result with all file paths made repo-relative."""
        result = copy.deepcopy(result)
        for lang_data in result.results.values():
            lang_data.visit_paths(self._to_relative)
        result.diagnostics = {
            lang: {self._to_relative(fp): diags for fp, diags in file_map.items()}
            for lang, file_map in result.diagnostics.items()
        }
        return result

    def _absolutize(self, result: "StaticAnalysisResults") -> "StaticAnalysisResults":
        """Expand all repo-relative file paths in result to absolute paths."""
        for lang_data in result.results.values():
            lang_data.visit_paths(self._to_absolute)
        result.diagnostics = {
            lang: {self._to_absolute(fp): diags for fp, diags in file_map.items()}
            for lang, file_map in result.diagnostics.items()
        }
        return result

    @property
    def pkl_path(self) -> Path:
        return self.artifact_dir / STATIC_ANALYSIS_PKL

    @property
    def sha_path(self) -> Path:
        return self.artifact_dir / STATIC_ANALYSIS_SHA

    def _read_tag_sha(self) -> str | None:
        """Return the source SHA recorded in the tag file, or None.

        Format: ``<version>\\n<sha>\\n``. Unknown versions return None so
        callers treat them as a cache miss without unpickling.
        """
        try:
            text = self.sha_path.read_text().strip()
        except (OSError, FileNotFoundError):
            return None
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return None
        version, sha = lines[0], lines[-1]
        if version != _TAG_VERSION:
            logger.info(f"Static analysis tag has unknown version {version!r}; treating as cache miss")
            return None
        return sha

    def _legacy_pkl_path(self) -> Path:
        return self.artifact_dir / _LEGACY_CACHE_SUBDIR / _LEGACY_PKL_NAME

    def get(self, expected_sha: str | None = None) -> "StaticAnalysisResults | None":
        """Load the cached results, or None if absent/invalid/SHA-mismatched.

        When ``expected_sha`` is provided, the tag file is read first and
        the pickle is only unpickled if the SHA matches — protects against
        stale-cache hits when the source has drifted. When ``expected_sha``
        is None, any tag (or no tag at all) is accepted; legacy pickles
        from the previous on-disk layout are also picked up here.
        """
        if expected_sha is not None:
            cached_sha = self._read_tag_sha()
            if cached_sha is None:
                return None
            if cached_sha != expected_sha:
                logger.info(
                    "Static analysis cache SHA mismatch (cached=%s, expected=%s); skipping",
                    cached_sha,
                    expected_sha,
                )
                return None

        target = self.pkl_path
        if not target.exists():
            legacy = self._legacy_pkl_path()
            if expected_sha is None and legacy.exists():
                logger.info(
                    "Reading legacy static analysis cache from %s; "
                    "next save will write to the new artifact location.",
                    legacy,
                )
                target = legacy
            else:
                return None

        try:
            with open(target, "rb") as f:
                result = pickle.load(f)
            result = self._absolutize(result)
            logger.info(f"Loaded static analysis from cache: {target}")
            return result
        except Exception as e:
            logger.warning(f"Failed to load static analysis cache: {e}")
            return None

    def save(self, result: "StaticAnalysisResults", source_sha: str | None = None) -> None:
        """Save the result with repo-relative paths and a sibling SHA tag.

        ``source_sha`` is the canonical identifier of the source state this
        pickle reflects (e.g. a git tree SHA over HEAD + dirty overlay).
        Stored in the sibling ``static_analysis.sha`` tag so future loads
        can SHA-gate before paying the unpickle cost. Saving without a
        SHA writes the pickle but leaves the tag absent — callers that
        ``get(expected_sha=...)`` will then miss the cache.
        """
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

        portable = self._relativize(result)
        data = pickle.dumps(portable)
        size_mb = sys.getsizeof(data) / (1024 * 1024)
        logger.info(f"Static analysis cache size: {size_mb:.2f} MB")

        temp_fd, temp_path = tempfile.mkstemp(dir=self.artifact_dir, suffix=".pkl.tmp")
        try:
            with open(temp_fd, "wb") as f:
                f.write(data)
            Path(temp_path).replace(self.pkl_path)
            logger.info(f"Saved static analysis to cache: {self.pkl_path}")
        except Exception as e:
            Path(temp_path).unlink(missing_ok=True)
            logger.warning(f"Failed to save static analysis cache: {e}")
            return

        # Write the sibling tag last so a partially-written pkl never gets a
        # SHA stamp; readers that miss the tag treat it as no-cache.
        if source_sha is not None:
            tag_text = f"{_TAG_VERSION}\n{source_sha}\n"
            tag_fd, tag_tmp = tempfile.mkstemp(dir=self.artifact_dir, suffix=".sha.tmp")
            try:
                with open(tag_fd, "w") as f:
                    f.write(tag_text)
                Path(tag_tmp).replace(self.sha_path)
            except Exception as e:
                Path(tag_tmp).unlink(missing_ok=True)
                logger.warning(f"Failed to write static analysis SHA tag: {e}")
        elif self.sha_path.exists():
            # No SHA provided this run — drop any stale tag so the next
            # SHA-gated read doesn't accidentally accept a mismatched pickle.
            try:
                self.sha_path.unlink()
            except OSError:
                pass


class StaticAnalysisResults:
    def __init__(self):
        self.results: dict[str, LanguageResults] = {}
        self.diagnostics: dict[str, FileDiagnosticsMap] = {}  # Language -> file_path -> diagnostics

    def _bucket(self, language: str) -> LanguageResults:
        return self.results.setdefault(language, LanguageResults())

    def add_class_hierarchy(self, language: str, hierarchy):
        """Add/merge a class hierarchy for a language; supports repeated calls."""
        self._bucket(language).hierarchy.merge(hierarchy)

    def add_cfg(self, language: str, cfg: CallGraph):
        """Add/merge a control flow graph for a language; supports repeated calls."""
        self._bucket(language).cfg.merge(cfg)

    def add_package_dependencies(self, language: str, dependencies):
        """Add/merge package dependencies for a language; supports repeated calls."""
        self._bucket(language).dependencies.merge(dependencies)

    def add_references(self, language: str, references: list[Node]):
        """Add/merge source code references for a language; supports repeated calls.

        Why: keys use the original qualified name to preserve source-code casing
        in the output.
        """
        self._bucket(language).references.add(references)

    def get_cfg(self, language: str) -> CallGraph:
        """Return the control flow graph for ``language`` or raise ``ValueError``."""
        bucket = self.results.get(language)
        if bucket is not None and bucket.cfg.graph is not None:
            return bucket.cfg.graph
        raise ValueError(f"Control flow graph for language '{language}' not found in results.")

    def get_hierarchy(self, language: str) -> dict:
        """Return the class hierarchy dict for ``language`` or raise ``ValueError``.

        Hierarchy values have shape ``{"superclasses": [...], "subclasses": [...],
        "file_path": str, "line_start": int, "line_end": int}``.
        """
        bucket = self.results.get(language)
        if bucket is not None and bucket.hierarchy.entries is not None:
            return bucket.hierarchy.entries
        raise ValueError(f"Class hierarchy for language '{language}' not found in results.")

    def get_package_dependencies(self, language: str) -> dict:
        """Return the package dependencies for ``language`` or raise ``ValueError``."""
        bucket = self.results.get(language)
        if bucket is not None and bucket.dependencies.entries is not None:
            return bucket.dependencies.entries
        raise ValueError(f"Package dependencies for language '{language}' not found in results.")

    def get_reference(self, language: str, qualified_name: str) -> Node:
        """Return the reference node for ``qualified_name``.

        Why: lookup is case-insensitive — the query and stored keys are both
        normalised through ``_reference_key`` so e.g. ``models.base.(Entity).GetType``
        and ``models.base.(entity).gettype`` resolve to the same reference.
        """
        bucket = self.results.get(language)
        if bucket is not None and bucket.references.by_qualified_name is not None:
            refs = bucket.references.by_qualified_name
            if qualified_name in refs:
                return refs[qualified_name]
            norm_qn = _reference_key(qualified_name)
            for ref_key, ref_val in refs.items():
                if _reference_key(ref_key) == norm_qn:
                    return ref_val
            for ref in refs.keys():
                if ref.lower().startswith(norm_qn):
                    raise FileExistsError(
                        f"Source code reference for '{qualified_name}' in language '{language}' is a file path, "
                        f"please use the full file path instead of the qualified name."
                    )
        raise ValueError(f"Source code reference for '{qualified_name}' in language '{language}' not found in results.")

    def get_loose_reference(self, language: str, qualified_name: str) -> tuple[str | None, Node | None]:
        norm_qn = _reference_key(qualified_name)
        bucket = self.results.get(language)
        if bucket is not None and bucket.references.by_qualified_name is not None:
            refs = bucket.references.by_qualified_name
            subset_refs = []
            for ref in refs.keys():
                ref_lower = ref.lower()
                if ref_lower.endswith(norm_qn):
                    return (
                        f"Found a loose match with a fully quantified name: {ref}",
                        refs[ref],
                    )
                if norm_qn in ref_lower:
                    subset_refs.append(ref)
            if len(subset_refs) == 1:
                return subset_refs[0], refs[subset_refs[0]]
        return None, None

    def get_languages(self):
        """Return the list of languages for which any data has been recorded."""
        return list(self.results.keys())

    def resolve_across_languages(self, qualified_name: str) -> Node | None:
        """Try ``get_reference`` then ``get_loose_reference`` across every language.

        Why: hides the try-exact-then-loose pattern several callers re-implement.
        """
        for lang in self.get_languages():
            try:
                return self.get_reference(lang, qualified_name)
            except (ValueError, FileExistsError):
                _, node = self.get_loose_reference(lang, qualified_name)
                if node is not None:
                    return node
        return None

    def iter_reference_nodes(self, language: str | None = None) -> Iterator[Node]:
        """Yield every stored reference as a ``Node``."""
        languages = [language] if language is not None else self.get_languages()
        for lang in languages:
            bucket = self.results.get(lang)
            if bucket is None or bucket.references.by_qualified_name is None:
                continue
            for node in bucket.references.by_qualified_name.values():
                if isinstance(node, Node):
                    yield node

    def add_source_files(self, language: str, source_files):
        """Add/extend source files for a language; supports repeated calls."""
        self._bucket(language).source_files.extend(source_files)

    def get_source_files(self, language: str) -> list[str]:
        """Return the list of source files for ``language``, or ``[]`` if absent."""
        bucket = self.results.get(language)
        if bucket is None or bucket.source_files.paths is None:
            return []
        return bucket.source_files.paths

    def get_all_source_files(self) -> list[str]:
        """Return source files across all languages."""
        all_source_files = []
        for language in self.results:
            all_source_files.extend(self.get_source_files(language))
        return all_source_files
