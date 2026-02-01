import logging
import os
import re
from fnmatch import fnmatch

from health.models import (
    FindingEntity,
    FindingGroup,
    HealthCheckConfig,
    Severity,
    StandardCheckSummary,
)
from repo_utils.ignore import is_test_or_infrastructure_file
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)

# Entry point filename patterns (language agnostic)
_ENTRY_POINT_PATTERNS = ["main.*", "cli.*", "app.*", "setup.*", "index.*", "__main__.*"]

# Entry point content markers across languages
_ENTRY_POINT_MARKERS = [
    "if __name__",  # Python
    "func main()",  # Go
    "public static void main",  # Java
    "FastAPI(",  # Python web apps
    "Flask(",  # Python web apps
]


def _read_file_cached(file_path: str, cache: dict[str, str | None]) -> str | None:
    """Read file content with caching. Returns None if file cannot be read."""
    if file_path in cache:
        return cache[file_path]
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        cache[file_path] = content
        return content
    except (OSError, UnicodeDecodeError):
        cache[file_path] = None
        return None


def _is_interface_or_annotation_file(
    file_path: str | None, content_cache: dict[str, str | None], result_cache: dict[str, bool]
) -> bool:
    """Check if a file contains interface or annotation definitions.

    Interface methods are meant to be implemented, not called directly.
    Java annotation types (@interface) define metadata, not executable code.
    """
    if not file_path:
        return False

    if file_path in result_cache:
        return result_cache[file_path]

    content = _read_file_cached(file_path, content_cache)
    if content and re.search(r"\b(?:public\s+)?(?:@)?interface\s+\w+", content):
        result_cache[file_path] = True
        return True

    result_cache[file_path] = False
    return False


def _is_entry_point_file(
    file_path: str | None, content_cache: dict[str, str | None], result_cache: dict[str, bool]
) -> bool:
    """Check if file is an entry point using simple heuristics.

    Entry points contain code invoked by runtime/framework, not explicit calls.
    """
    if not file_path:
        return False

    if file_path in result_cache:
        return result_cache[file_path]

    basename = os.path.basename(file_path)
    for pattern in _ENTRY_POINT_PATTERNS:
        if fnmatch(basename, pattern):
            result_cache[file_path] = True
            return True

    content = _read_file_cached(file_path, content_cache)
    if content:
        for marker in _ENTRY_POINT_MARKERS:
            if marker in content:
                result_cache[file_path] = True
                return True

    result_cache[file_path] = False
    return False


def _is_init_module_function(node_name: str, file_path: str | None, delimiter: str) -> bool:
    """Check if node is a dunder method in __init__.py (invoked by Python runtime)."""
    if not file_path:
        return False

    if os.path.basename(file_path) != "__init__.py":
        return False

    short_name = node_name.rsplit(delimiter, 1)[-1]
    return short_name.startswith("__") and short_name.endswith("__")


def _is_likely_exported(
    node_name: str, file_path: str | None, delimiter: str, content_cache: dict[str, str | None]
) -> bool:
    """Check if a function is likely exported (public API surface).

    Exported functions are low-confidence orphans - they may be called externally
    in ways the call graph cannot detect (imports, dynamic calls, framework hooks).
    """
    if not file_path:
        return True  # Assume exported if no file context

    short_name = node_name.rsplit(delimiter, 1)[-1]

    # Leading underscore = private/internal (Python, JS, and many languages)
    # This also covers dunder methods (__init__, __getattr__, etc.)
    if short_name.startswith("_"):
        return False

    content = _read_file_cached(file_path, content_cache)
    if not content:
        return False

    # Check for export/public keywords in function definition
    export_patterns = [
        rf"^\s*export\s+(?:async\s+|default\s+)?(?:function|const|let|var)?\s*{re.escape(short_name)}\b",  # TS/JS
        rf"^\s*public\s+(?:static\s+)?(?:void|def|func)?\s*{re.escape(short_name)}\b",  # Java/C#
    ]
    for pattern in export_patterns:
        if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
            return True

    return False


def _is_public_api_method(node_name: str, file_path: str | None, delimiter: str) -> bool:
    """Check if a method is in a public API directory (e.g. src/main/ for Maven projects)."""
    if not file_path:
        return False

    if "src/main/" in file_path.lower():
        short_name = node_name.rsplit(delimiter, 1)[-1]
        if not short_name.startswith("_"):
            return True

    return False


def _build_import_index(source_files: list[str], content_cache: dict[str, str | None]) -> dict[str, set[str]]:
    """Build a mapping of imported names to the files that import them."""
    imported_by: dict[str, set[str]] = {}
    for src_file in source_files:
        abs_file = os.path.abspath(src_file)
        content = _read_file_cached(src_file, content_cache)
        if not content:
            continue
        names_in_file: set[str] = set()
        # Extract names from import statements across languages
        for match in re.finditer(r"\b(?:from\s+\S+\s+)?import\s+(.+)", content):
            for name in re.findall(r"\b(\w+)\b", match.group(1)):
                names_in_file.add(name)
        # TS/JS named imports: import { foo, bar } from '...'
        for match in re.finditer(r"\bimport\s+\{([^}]+)\}\s*from", content):
            for name in re.findall(r"\b(\w+)\b", match.group(1)):
                names_in_file.add(name)
        # CommonJS: require('...')
        for match in re.finditer(r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", content):
            path_name = match.group(1).rsplit("/", 1)[-1]
            names_in_file.add(path_name)
        for name in names_in_file:
            imported_by.setdefault(name, set()).add(abs_file)
    return imported_by


def _is_imported_elsewhere(
    node_name: str, file_path: str | None, delimiter: str, import_index: dict[str, set[str]]
) -> bool:
    """Check if function name is imported by a file other than its own."""
    if not file_path or not import_index:
        return False
    short_name = node_name.rsplit(delimiter, 1)[-1]
    importing_files = import_index.get(short_name)
    if not importing_files:
        return False
    # Exclude the function's own file
    file_abs = os.path.abspath(file_path)
    return any(f != file_abs for f in importing_files)


def check_orphan_code(
    call_graph: CallGraph,
    config: HealthCheckConfig | None = None,
    source_files: list[str] | None = None,
) -> StandardCheckSummary:
    """Detect orphan code — functions with no incoming or outgoing calls.

    Only reports HIGH CONFIDENCE orphans: internal (non-exported) functions
    with zero call relationships. These are implementation details that should
    definitely be called internally if they exist.

    LOW CONFIDENCE orphans (exported functions, entry points, type declarations)
    are skipped to avoid false positives - they may be used externally in ways
    the call graph cannot capture.
    """
    exclude_patterns = config.orphan_exclude_patterns if config else []
    src_files = source_files or []
    delimiter = call_graph.delimiter

    # Invocation-scoped caches (no module-level mutable state)
    content_cache: dict[str, str | None] = {}
    entry_point_cache: dict[str, bool] = {}
    interface_cache: dict[str, bool] = {}
    import_index: dict[str, set[str]] | None = None  # Built lazily on first orphan candidate

    warning_entities: list[FindingEntity] = []
    nx_graph = call_graph.to_networkx()
    total_nodes = nx_graph.number_of_nodes()

    skipped = 0
    for node_name in nx_graph.nodes:
        node = call_graph.nodes.get(node_name)

        # Skip non-callable entities (classes, data, callbacks)
        if not node or not node.is_callable() or node.is_callback_or_anonymous():
            skipped += 1
            continue

        file_path = node.file_path

        # Skip test, infrastructure, and build/config files
        if is_test_or_infrastructure_file(file_path):
            skipped += 1
            continue

        # Skip interface/annotation files (methods meant to be implemented)
        if _is_interface_or_annotation_file(file_path, content_cache, interface_cache):
            skipped += 1
            continue

        # Skip entry point files (runtime/framework invoked)
        if _is_entry_point_file(file_path, content_cache, entry_point_cache):
            skipped += 1
            continue

        # Skip __init__.py special methods (invoked by Python runtime)
        if _is_init_module_function(node_name, file_path, delimiter):
            skipped += 1
            continue

        # Skip user-configured patterns
        if exclude_patterns and _matches_exclude_pattern(node_name, file_path, exclude_patterns):
            skipped += 1
            continue

        # Check for orphan condition
        in_deg = nx_graph.in_degree(node_name)
        out_deg = nx_graph.out_degree(node_name)

        if in_deg == 0 and out_deg == 0:
            # LOW CONFIDENCE: Skip if likely exported (public API)
            if _is_likely_exported(node_name, file_path, delimiter, content_cache):
                skipped += 1
                continue

            # LOW CONFIDENCE: Skip if imported elsewhere (public API usage)
            if src_files:
                if import_index is None:
                    import_index = _build_import_index(src_files, content_cache)
                if _is_imported_elsewhere(node_name, file_path, delimiter, import_index):
                    skipped += 1
                    continue

            # LOW CONFIDENCE: Skip public API methods in library projects
            if _is_public_api_method(node_name, file_path, delimiter):
                skipped += 1
                continue

            # HIGH CONFIDENCE: Internal function with no calls - likely dead code
            warning_entities.append(
                FindingEntity(
                    entity_name=node_name,
                    file_path=file_path,
                    line_start=node.line_start,
                    line_end=node.line_end,
                    metric_value=0.0,
                )
            )

    checked_nodes = total_nodes - skipped
    connected = checked_nodes - len(warning_entities)
    score = connected / checked_nodes if checked_nodes > 0 else 1.0

    finding_groups: list[FindingGroup] = []
    if warning_entities:
        finding_groups.append(
            FindingGroup(
                severity=Severity.WARNING,
                threshold=0,
                description="Functions with no incoming or outgoing calls (high confidence dead code)",
                entities=warning_entities,
            )
        )

    return StandardCheckSummary(
        check_name="orphan_code",
        description="Detects functions with no incoming or outgoing calls (potential dead code)",
        total_entities_checked=checked_nodes,
        findings_count=len(warning_entities),
        warning_count=len(warning_entities),
        score=score,
        finding_groups=finding_groups,
    )


def _matches_exclude_pattern(entity_name: str, file_path: str | None, patterns: list[str]) -> bool:
    """Check if entity matches any exclusion pattern using fnmatch."""
    for pattern in patterns:
        if fnmatch(entity_name, pattern):
            return True
        if file_path and fnmatch(file_path, pattern):
            return True
    return False
