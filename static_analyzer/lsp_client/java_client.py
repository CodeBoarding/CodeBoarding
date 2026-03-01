"""
Java LSP client using Eclipse JDT Language Server.
"""

import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from static_analyzer.graph import CallGraph, Node
from static_analyzer.lsp_client.client import FileAnalysisResult, LSPClient
from static_analyzer.java_config_scanner import JavaProjectConfig
from static_analyzer.java_utils import (
    create_jdtls_command,
    get_java_version,
    find_java_21_or_later,
    detect_java_installations,
)
from static_analyzer.programming_language import ProgrammingLanguage, JavaConfig
from repo_utils.ignore import RepoIgnoreManager

logger = logging.getLogger(__name__)

# Strips Java generic type arguments from symbol names returned by JDTLS.
# Works on mixed-case strings (before lowercasing), so e.g.:
#   "processAnimals(List<Animal>)"  → "processAnimals(List)"
#   "describeAll(List<T>) <T extends Animal>" → "describeAll(List)"
_JAVA_GENERIC_MIXED_RE = re.compile(r"<[^<>]*>")
# Trailing JDTLS type-param declaration, e.g. " <T extends Animal>" or " <T, R>"
_JAVA_TRAILING_TP_MIXED_RE = re.compile(r"\s*<[A-Za-z][A-Za-z0-9,\s]*>\s*$")


def _strip_generics_mixed(name: str) -> str:
    """Strip Java generic type arguments from a mixed-case JDTLS symbol name.

    Also strips trailing JDTLS type-param declarations like `` <T extends Animal>``.
    Returns the result right-stripped of whitespace.
    """
    prev = None
    while prev != name:
        prev = name
        name = _JAVA_GENERIC_MIXED_RE.sub("", name)
    name = _JAVA_TRAILING_TP_MIXED_RE.sub("", name)
    return name.rstrip()


class JavaClient(LSPClient):
    """
    LSP client for Java using Eclipse JDT Language Server (JDTLS).

    Handles Java-specific initialization, project import, and workspace management.
    """

    # Java LSP uses kind=10 for enums in addition to the standard class (5),
    # interface (11), and struct (23) kinds used by the base class.
    _CLASS_LIKE_KINDS = {5, 10, 11, 23}

    def __init__(
        self,
        project_path: Path,
        language: ProgrammingLanguage,
        project_config: JavaProjectConfig,
        ignore_manager: RepoIgnoreManager,
        jdtls_root: Path | None = None,
    ):
        """
        Initialize Java LSP client.

        Args:
            project_path: Path to the Java project root
            language: ProgrammingLanguage object with LSP server config
            project_config: Java project configuration (Maven/Gradle/etc.)
            ignore_manager: Repository ignore manager
            jdtls_root: Path to JDTLS installation (if None, will try to detect from config)
        """
        self.project_config = project_config
        self.workspace_dir: Path | None = None  # Will be created in start()
        self.temp_workspace = True

        # Try to get jdtls_root from language config first, then from parameter
        if jdtls_root is not None:
            self.jdtls_root: Path | None = jdtls_root
        else:
            # Get from language-specific config
            if isinstance(language.language_specific_config, JavaConfig):
                self.jdtls_root = language.language_specific_config.jdtls_root
            else:
                self.jdtls_root = None

        # Initialize base LSPClient
        super().__init__(project_path, language, ignore_manager)

        # Track import status
        self.import_complete = False
        self.import_errors: list[str] = []
        self.java_home: Path | None = None  # Will be detected in start()
        self.workspace_indexed = False  # Track if workspace symbols are available

    def start(self):
        """Start the JDTLS server with proper command construction."""
        # Create workspace directory
        self.workspace_dir = Path(tempfile.mkdtemp(prefix="jdtls-workspace-"))
        self.temp_workspace = True

        try:
            # Find Java 21+
            self.java_home = find_java_21_or_later()
            if self.java_home is None:
                raise RuntimeError("Java 21+ required to run JDTLS. Please install JDK 21 or later.")

            # If jdtls_root not provided, try to find it from server params or environment
            if self.jdtls_root is None:
                self.jdtls_root = self._find_jdtls_root()
                if self.jdtls_root is None:
                    raise RuntimeError(
                        "JDTLS installation not found. Please ensure JDTLS is installed "
                        "and the path is configured in static_analysis_config.yml"
                    )

            # Calculate heap size
            heap_size = self._calculate_heap_size()

            # Build JDTLS command
            jdtls_command = create_jdtls_command(self.jdtls_root, self.workspace_dir, self.java_home, heap_size)

            # Override the server_start_params with our constructed command
            self.server_start_params = jdtls_command

            logger.info(
                f"Starting JavaClient for {self.project_config.root} "
                f"(build system: {self.project_config.build_system})"
            )

            # Call parent start() which will use our server_start_params
            super().start()
        except Exception:
            # Clean up workspace on failure
            if self.temp_workspace and self.workspace_dir and self.workspace_dir.exists():
                try:
                    shutil.rmtree(self.workspace_dir)
                    logger.debug(f"Cleaned up workspace after failure: {self.workspace_dir}")
                except Exception as e:
                    logger.warning(f"Failed to clean up workspace: {e}")
            raise

    def _find_jdtls_root(self) -> Path | None:
        """Try to find JDTLS root directory from common locations."""
        potential_locations = [
            Path.home() / ".jdtls",
            Path(__file__).parent.parent / "servers" / "jdtls",
            Path("/opt/jdtls"),
        ]

        for location in potential_locations:
            if location.exists() and (location / "plugins").exists():
                logger.info(f"Found JDTLS at {location}")
                return location

        return None

    def _calculate_heap_size(self) -> str:
        """Calculate appropriate heap size for project based on JVM language files."""
        # Count all JVM language files (Java, Kotlin, Groovy) as JDTLS scans full project
        # Include all files regardless of gitignore since JDTLS will process them
        jvm_files: list[Path] = []
        jvm_files.extend(self.project_config.root.rglob("*.java"))
        jvm_files.extend(self.project_config.root.rglob("*.kt"))
        jvm_files.extend(self.project_config.root.rglob("*.groovy"))

        file_count = len(jvm_files)

        if file_count < 100:
            return "1G"
        elif file_count < 500:
            return "2G"
        elif file_count < 2000:
            return "4G"
        elif file_count < 5000:
            return "6G"
        else:
            return "8G"

    def _initialize(self):
        """Performs the LSP initialization handshake with JDTLS-specific options."""
        logger.info(f"Initializing JDTLS for {self.language_id}...")
        params = {
            "processId": os.getpid(),
            "rootUri": self.project_path.as_uri(),
            "capabilities": self._get_capabilities(),
            "initializationOptions": self._get_initialization_options(),
        }

        init_id = self._send_request("initialize", params)
        response = self._wait_for_response(init_id, timeout=360)

        if "error" in response:
            raise RuntimeError(f"Initialization failed: {response['error']}")

        logger.info("Initialization successful.")
        self._send_notification("initialized", {})

    def _get_initialization_options(self) -> dict:
        """
        Build JDTLS-specific initialization options.

        Returns:
            Initialization options dictionary
        """
        # Detect available JDKs for multi-version support
        jdks = detect_java_installations()
        runtimes: list[dict[str, str | bool]] = []

        for jdk in jdks[:5]:  # Limit to 5 most recent
            java_cmd = jdk / "bin" / "java"
            version = get_java_version(str(java_cmd))
            if version > 0:
                runtime_entry: dict[str, str | bool] = {
                    "name": f"JavaSE-{version}",
                    "path": str(jdk),
                }
                runtimes.append(runtime_entry)

        # Set most recent as default
        if runtimes:
            runtimes[0]["default"] = True

        return {
            "bundles": [],
            "workspaceFolders": [self.project_path.as_uri()],
            "settings": {
                "java": {
                    "home": str(self.java_home) if self.java_home else None,
                    "configuration": {
                        "runtimes": runtimes,
                    },
                    "import": {
                        "gradle": {
                            "enabled": True,
                        },
                        "maven": {
                            "enabled": True,
                        },
                    },
                    "errors": {
                        "incompleteClasspath": {
                            "severity": "warning",  # Don't fail on incomplete classpath
                        },
                    },
                    "format": {
                        "enabled": False,  # We don't need formatting for analysis
                    },
                    "completion": {
                        "enabled": True,
                    },
                    "signatureHelp": {
                        "enabled": True,
                    },
                },
            },
        }

    def _get_capabilities(self) -> dict:
        """
        Build client capabilities for JDTLS.

        Returns:
            Client capabilities dictionary
        """
        capabilities = {
            "textDocument": {
                "callHierarchy": {"dynamicRegistration": True},
                "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                "typeHierarchy": {"dynamicRegistration": True},
                "references": {"dynamicRegistration": True},
                "semanticTokens": {"dynamicRegistration": True},
            },
            "workspace": {
                "workspaceFolders": True,
                "didChangeConfiguration": {
                    "dynamicRegistration": True,
                },
            },
        }

        return capabilities

    def wait_for_import(self, timeout: int = 300):
        """
        Wait for JDTLS to complete project import.

        Args:
            timeout: Maximum time to wait in seconds (default: 5 minutes)

        Raises:
            TimeoutError: If import doesn't complete within timeout
        """
        logger.info("Waiting for Java project import to complete...")
        start = time.time()
        last_log = start

        while not self.import_complete:
            elapsed = time.time() - start

            # Timeout check
            if elapsed > timeout:
                logger.warning(f"Project import timeout after {timeout}s. " f"Proceeding with analysis anyway.")
                break

            # Log progress every 10 seconds
            if time.time() - last_log >= 10:
                logger.info(f"Still importing... ({int(elapsed)}s elapsed)")
                last_log = time.time()

            time.sleep(1)

        total_time = time.time() - start
        logger.info(f"Project import completed in {total_time:.1f}s")

        # Validate project loaded
        self._validate_project_loaded()

    def _validate_project_loaded(self, max_wait: int = 10):
        """
        Verify project loaded successfully by polling workspace symbols.

        JDTLS may take additional time after import to index the workspace.
        This method polls workspace/symbol until symbols are available or timeout.
        The default timeout is intentionally short (10s) because workspace/symbol
        with an empty query often returns no results for Maven/Gradle projects;
        file-by-file analysis is used as the fallback.

        Args:
            max_wait: Maximum time to wait for symbols in seconds (default: 10s)
        """
        logger.info("Validating project is fully indexed...")
        start = time.time()
        last_log = start

        while time.time() - start < max_wait:
            try:
                # Test workspace/symbol request
                params = {"query": ""}
                req_id = self._send_request("workspace/symbol", params)
                response = self._wait_for_response(req_id, timeout=10)

                if "error" in response:
                    logger.debug(f"workspace/symbol error: {response['error']}")
                    time.sleep(2)
                    continue

                symbols = response.get("result", [])

                if symbols:
                    self.workspace_indexed = True
                    logger.info(f"Project loaded and indexed successfully ({len(symbols)} symbols available)")
                    return True

                # Log progress every 10 seconds
                elapsed = time.time() - start
                if time.time() - last_log >= 10:
                    logger.info(f"Waiting for workspace indexing... ({int(elapsed)}s elapsed)")
                    last_log = time.time()

                time.sleep(2)

            except Exception as e:
                logger.debug(f"Error checking workspace symbols: {e}")
                time.sleep(2)

        # Timeout reached
        logger.warning(
            f"No workspace symbols found after {max_wait}s - project may not be fully indexed. "
            "Continuing with file-by-file analysis."
        )
        return False

    def _get_all_classes_in_workspace(self) -> list:
        """
        Get all class symbols in workspace with a single attempt for JDTLS.

        workspace/symbol was already polled during wait_for_import / _validate_project_loaded.
        If workspace_indexed is True we use the base implementation; otherwise we make one
        attempt and immediately fall through to file-by-file analysis to avoid a long retry
        that would eat into the test/overall timeout.
        """
        # If we know workspace is indexed, use base implementation
        if self.workspace_indexed:
            return super()._get_all_classes_in_workspace()

        # Make a single attempt — workspace/symbol was already polled during startup.
        logger.info("Attempting workspace/symbol for class discovery (single try)...")
        try:
            params = {"query": ""}
            req_id = self._send_request("workspace/symbol", params)
            response = self._wait_for_response(req_id, timeout=10)

            if "error" not in response:
                symbols = response.get("result") or []
                if symbols:
                    self.workspace_indexed = True
                    classes = [s for s in symbols if s.get("kind") in self._CLASS_LIKE_KINDS]
                    logger.info(f"Found {len(classes)} class symbols via workspace/symbol")
                    return classes

        except Exception as e:
            logger.debug(f"Error getting workspace symbols: {e}")

        # Fall through — let file-by-file analysis handle class discovery
        logger.info("workspace/symbol returned no results. Proceeding with file-by-file analysis.")
        return []

    def handle_notification(self, method: str, params: dict):
        """
        Handle notifications from JDTLS.

        JDTLS sends various notifications during project import and build processes.
        This method tracks the import progress to determine when the project is ready
        for analysis.

        Args:
            method: The LSP notification method name
            params: The notification parameters

        Tracked notifications:
            - language/status: Project import status and service readiness
            - $/progress: Build/import progress updates
            - language/progressReport: Maven/Gradle specific progress
            - textDocument/publishDiagnostics: Compilation errors including import failures
        """
        # Track language/status notifications for import progress
        if method == "language/status":
            status_type = params.get("type", "")
            message = params.get("message", "")

            logger.debug(f"JDTLS status: type={status_type}, message={message}")

            if status_type == "Started":
                logger.debug("JDTLS: Project import started")
            elif status_type == "ProjectStatus":
                # ProjectStatus with "OK" means import is complete
                if message == "OK":
                    self.import_complete = True
                    logger.info("JDTLS: Project import complete (ProjectStatus OK)")
            elif status_type == "ServiceReady":
                # ServiceReady can also indicate readiness
                self.import_complete = True
                logger.info("JDTLS: Service ready")

        # Track progress notifications - these show build/import progress
        elif method == "$/progress":
            value = params.get("value", {})
            if isinstance(value, dict):
                kind = value.get("kind", "")
                message = value.get("message", "")
                if message:
                    logger.debug(f"JDTLS progress: {message}")
                # "end" kind often signals completion
                if kind == "end" and "import" in message.lower():
                    logger.debug(f"JDTLS: Import progress completed - {message}")

        # Alternative: language/progressReport (Maven/Gradle specific)
        elif method == "language/progressReport":
            status = params.get("complete", False)
            if status:
                logger.debug("JDTLS: Build/import progress report complete")

        # Track diagnostics for import errors
        elif method == "textDocument/publishDiagnostics":
            diagnostics = params.get("diagnostics", [])
            for diag in diagnostics:
                if diag.get("severity") == 1:  # Error
                    message = diag.get("message", "")
                    if "project" in message.lower() or "import" in message.lower():
                        self.import_errors.append(message)

    def close(self):
        """Clean up resources including temporary workspace."""
        # Shutdown LSP server
        super().close()

        # Remove temporary workspace directory
        if self.temp_workspace and self.workspace_dir and self.workspace_dir.exists():
            try:
                shutil.rmtree(self.workspace_dir)
                logger.debug(f"Cleaned up workspace: {self.workspace_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up workspace: {e}")

    def _get_package_name(self, file_path: Path) -> str:
        """
        Extract package name from Java file.

        Overrides the base implementation to use Java package declarations.
        """
        try:
            # Try to read package declaration from file
            content = file_path.read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")

            for line in lines[:100]:
                line = line.strip()
                if line.startswith("package "):
                    # Extract package name
                    package_line = line[8:].strip()
                    if package_line.endswith(";"):
                        package_line = package_line[:-1]
                    return package_line.strip()

            # If no package declaration found, use file path
            rel_path = file_path.relative_to(self.project_path)

            # Look for src/main/java or src/test/java patterns
            parts = rel_path.parts
            if "src" in parts:
                src_idx = parts.index("src")
                # Skip src/main/java or src/test/java
                if src_idx + 2 < len(parts) and parts[src_idx + 1] in ["main", "test"] and parts[src_idx + 2] == "java":
                    package_parts = parts[src_idx + 3 : -1]  # Skip file name
                else:
                    package_parts = parts[src_idx + 1 : -1]  # Skip file name

                if package_parts:
                    return ".".join(package_parts)

            # Fallback to parent directory structure
            package_parts = rel_path.parent.parts
            if package_parts and package_parts[0] != ".":
                return ".".join(package_parts)
            else:
                return "default"

        except ValueError:
            return "external"
        except Exception as e:
            logger.debug(f"Error extracting package name from {file_path}: {e}")
            return "unknown"

    # Regex to detect Java record declarations, capturing class name and component list.
    # e.g. "public record UserProfile(String name, String email) {"
    _RECORD_DECL_RE = re.compile(
        r"\brecord\s+(\w+)\s*\(([^)]*)\)",
        re.MULTILINE,
    )

    # Simple type-erasure for a Java type token: strips generic type arguments.
    # e.g. "List<String>" → "List", "String" → "String"
    _PARAM_GENERIC_RE = re.compile(r"<[^>]*>")

    def _post_process_file_result(self, result: FileAnalysisResult, file_path: Path) -> None:
        """Java-specific per-file post-processing.

        Synthesises constructor nodes that JDTLS does not expose as document symbols:
        - No-arg (default) constructors: ``QueryBuilder.QueryBuilder()``
        - Java record constructors: ``UserProfile.UserProfile(String, String)``

        Also synthesises Java record accessor edges:
        - ``displayName()`` calls ``name()`` and ``email()`` on a record.
        """
        if result.error:
            return

        # Use cached content from analysis if available, otherwise read from disk.
        content = result.content
        if content is None:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return

        LSP_KIND_CLASS = 5
        LSP_KIND_METHOD = 6

        # Collect the names of class nodes already present so we can determine
        # which ones need a synthesised constructor.
        existing_names = {n.fully_qualified_name for n in result.symbols}

        # Build a map: simple_class_name -> qualified_class_name for this file's classes
        class_node_map: dict[str, str] = {}
        for node in result.symbols:
            if node.type == LSP_KIND_CLASS:
                # e.g. "src.main.java.core.QueryBuilder.QueryBuilder"
                parts = node.fully_qualified_name.rsplit(".", 1)
                if len(parts) == 2:
                    class_node_map[parts[1]] = node.fully_qualified_name

        # 1. Synthesise no-arg constructor node for every Class node in this file
        #    (JDTLS doesn't expose default constructors as document symbols)
        extra_symbols: list[Node] = []
        for node in list(result.symbols):
            if node.type != LSP_KIND_CLASS:
                continue
            fqn = node.fully_qualified_name
            # e.g. "src.main.java.core.QueryBuilder.QueryBuilder"
            # → synthesise "src.main.java.core.QueryBuilder.QueryBuilder()"
            ctor_fqn = fqn + "()"
            if ctor_fqn not in existing_names:
                ctor_node = Node(
                    fully_qualified_name=ctor_fqn,
                    node_type=LSP_KIND_METHOD,
                    file_path=str(file_path),
                    line_start=node.line_start,
                    line_end=node.line_end,
                )
                extra_symbols.append(ctor_node)
                existing_names.add(ctor_fqn)
                logger.debug(f"Java: synthesised no-arg constructor node {ctor_fqn}")

        result.symbols.extend(extra_symbols)

        # 2. Synthesise Java record canonical constructor node and accessor nodes/edges
        for m in self._RECORD_DECL_RE.finditer(content):
            record_name = m.group(1)
            components_str = m.group(2).strip()

            if record_name not in class_node_map:
                continue

            class_fqn = class_node_map[record_name]
            # e.g. "src.main.java.core.UserProfile.UserProfile"
            file_prefix = class_fqn[: class_fqn.rfind(f".{record_name}")]

            # Parse record components: "String name, String email" → [("String", "name"), ...]
            components: list[tuple[str, str]] = []
            if components_str:
                for comp in components_str.split(","):
                    tokens = comp.strip().split()
                    if len(tokens) >= 2:
                        type_tok = self._PARAM_GENERIC_RE.sub("", tokens[-2]).strip()
                        name_tok = tokens[-1].strip()
                        components.append((type_tok, name_tok))

            # Synthesise canonical record constructor  e.g. "UserProfile(String, String)"
            # The constructor FQN is class_fqn + "(params)" e.g.
            # "src.main.java.core.UserProfile.UserProfile(String, String)"
            param_types = ", ".join(t for t, _ in components)
            ctor_fqn = f"{class_fqn}({param_types})"
            if ctor_fqn not in existing_names:
                ctor_node = Node(
                    fully_qualified_name=ctor_fqn,
                    node_type=LSP_KIND_METHOD,
                    file_path=str(file_path),
                    line_start=0,
                    line_end=0,
                )
                result.symbols.append(ctor_node)
                existing_names.add(ctor_fqn)
                logger.debug(f"Java: synthesised record constructor node {ctor_fqn}")

            # Synthesise accessor nodes for each record component
            # e.g. "name()" and "email()" for UserProfile
            # Accessor FQN uses the same namespace as other methods on the class:
            # "src.main.java.core.UserProfile.name()"  (NOT "...UserProfile.UserProfile.name()")
            accessor_fqns: list[str] = []
            for _type_tok, comp_name in components:
                acc_fqn = f"{file_prefix}.{comp_name}()"
                if acc_fqn not in existing_names:
                    acc_node = Node(
                        fully_qualified_name=acc_fqn,
                        node_type=LSP_KIND_METHOD,
                        file_path=str(file_path),
                        line_start=0,
                        line_end=0,
                    )
                    result.symbols.append(acc_node)
                    existing_names.add(acc_fqn)
                    logger.debug(f"Java: synthesised record accessor node {acc_fqn}")
                accessor_fqns.append(acc_fqn)

            # Synthesise accessor call edges from any method in this file that calls
            # the accessor methods.  We look for function symbols in this file and
            # check if they call accessor patterns like "name()" or "email()".
            content_lines = content.split("\n")
            for func_sym in result.function_symbols:
                func_fqn = self._create_qualified_name(file_path, func_sym.get("name", ""))
                # Check if the function body contains calls to the record accessors
                func_range = func_sym.get("range", {})
                start_ln = func_range.get("start", {}).get("line", 0)
                end_ln = func_range.get("end", {}).get("line", start_ln)
                func_body = "\n".join(content_lines[start_ln : end_ln + 1])

                for acc_fqn, (_type_tok, comp_name) in zip(accessor_fqns, components):
                    # Look for accessor call pattern: "comp_name()"
                    if re.search(rf"\b{re.escape(comp_name)}\(\)", func_body):
                        edge = (func_fqn, acc_fqn)
                        if edge not in result.call_relationships:
                            result.call_relationships.append(edge)
                            logger.debug(f"Java: synthesised record accessor edge {func_fqn} -> {acc_fqn}")

    def _create_qualified_name(self, file_path: Path, symbol_name: str) -> str:
        """Create a fully qualified name, stripping Java generic type arguments.

        JDTLS includes raw generic types in symbol names (e.g. ``processAnimals(List<Animal>)``).
        We strip those to get erased signatures (e.g. ``processAnimals(List)``) so that the
        call-graph node names match the fixture expectations.
        """
        return super()._create_qualified_name(file_path, _strip_generics_mixed(symbol_name))

    def _normalize_call_item_name(self, item: dict) -> str:
        """Normalise a JDTLS callHierarchy item name for use in the call graph.

        JDTLS reports no-arg constructor calls (``new QueryBuilder()``) as a callHierarchy
        item with ``kind=5`` (Class) and a name equal to the bare class name (``QueryBuilder``),
        without parentheses.  We append ``()`` so that the edge destination matches the
        fixture expectation ``QueryBuilder.QueryBuilder()``.

        For all other item kinds the name is returned unchanged (after generic stripping
        which happens in ``_create_qualified_name``).
        """
        LSP_KIND_CLASS = 5
        name = item.get("name", "")
        kind = item.get("kind")
        if kind == LSP_KIND_CLASS and "(" not in name:
            # This is a constructor call to a no-arg (or default) constructor.
            # Append "()" so the edge matches constructor-style fixture expectations.
            return f"{name}()"
        return name

    # Regex to find bare method-call tokens in Java source (identifier followed by open paren).
    # Matches calls like "from(", ".getLabel(", "name(" etc.
    _METHOD_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

    def _post_process_analysis(
        self,
        call_graph: CallGraph,
        reference_nodes: list[Node],
        file_results: list[FileAnalysisResult],
    ) -> None:
        """Java-specific analysis-level post-processing.

        Supplements JDTLS call-hierarchy results with lightweight source-scanning to
        recover edges that JDTLS systematically misses:

        1. The first method in a builder chain after a constructor call
           e.g. ``new QueryBuilder().from(table)`` — JDTLS captures ``where()``,
           ``build()`` etc. but misses ``from()``.
        2. Inner-class methods calling outer-class methods via implicit outer reference
           e.g. ``Container.Item.describe()`` calling ``Container.getLabel()``.
        3. Record/class constructor calls: JDTLS reports the constructor as a kind=5
           Class item (no params), but the fixture expects the parametrized constructor.
           We scan the caller body for ``new ClassName(`` and if a parametrized ctor
           node exists we add an edge to it.
        """
        # Build a map: simple_method_name → list of FQNs in the call graph
        # e.g. "from" → ["src.main.java.core.QueryBuilder.from(String)"]
        name_to_fqns: dict[str, list[str]] = {}
        for fqn in call_graph.nodes:
            simple = fqn.rsplit(".", 1)[-1]
            method_name = simple.split("(")[0]
            if method_name:
                name_to_fqns.setdefault(method_name, []).append(fqn)

        # Build the set of existing edges ONCE (not per-file).
        existing_edge_fqns: set[tuple[str, str]] = {
            (e.src_node.fully_qualified_name, e.dst_node.fully_qualified_name) for e in call_graph.edges
        }

        # Pre-index: caller_fqn → set of callee class prefixes, for fast sibling lookups.
        # e.g. "pkg.Foo.bar()" → {"pkg.Foo", "pkg.Baz"} means bar() already calls into Foo and Baz.
        caller_to_callee_classes: dict[str, set[str]] = {}
        for src_fqn, dst_fqn in existing_edge_fqns:
            dst_class = dst_fqn.rsplit(".", 1)[0]
            caller_to_callee_classes.setdefault(src_fqn, set()).add(dst_class)

        # Pre-index: file_path → list of method/constructor nodes in the call graph.
        file_path_to_nodes: dict[str, list[Node]] = {}
        for node in call_graph.nodes.values():
            if node.type in (6, 9):  # method / constructor
                file_path_to_nodes.setdefault(node.file_path, []).append(node)

        # Process each file result
        for file_result in file_results:
            if file_result.error:
                continue

            # Use cached content from analysis if available, otherwise read from disk.
            content = file_result.content
            if content is None:
                try:
                    content = file_result.file_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

            lines = content.split("\n")
            file_path = file_result.file_path
            file_path_str = str(file_path)

            # Get nodes for this file from the pre-built index.
            file_nodes = file_path_to_nodes.get(file_path_str, [])
            if not file_nodes:
                continue

            # Build a range lookup from function_symbols for body extraction
            sym_ranges: list[tuple[int, int]] = [
                (
                    fs.get("range", {}).get("start", {}).get("line", 0),
                    fs.get("range", {}).get("end", {}).get("line", 0),
                )
                for fs in file_result.function_symbols
            ]

            for node in file_nodes:
                caller_fqn = node.fully_qualified_name
                # Find the best matching function symbol range for this node
                node_start = node.line_start
                node_end = node.line_end
                best_start, best_end = node_start, node_end
                for s, e in sym_ranges:
                    if s == node_start or e == node_end:
                        best_start, best_end = s, e
                        break

                func_body = "\n".join(lines[best_start : best_end + 1])

                # Collect all method names called in this function body
                called_names: set[str] = {m.group(1) for m in self._METHOD_CALL_RE.finditer(func_body)}

                # For each called method name, check if there's an unlinked edge in the graph
                caller_classes = caller_to_callee_classes.get(caller_fqn, set())
                for method_name in called_names:
                    candidates = name_to_fqns.get(method_name, [])
                    for candidate_fqn in candidates:
                        if candidate_fqn == caller_fqn:
                            continue
                        if candidate_fqn not in call_graph.nodes:
                            continue
                        edge_key = (caller_fqn, candidate_fqn)
                        if edge_key in existing_edge_fqns:
                            continue
                        # Check that the call is plausible: either
                        # (a) the caller already has at least one edge to a node in the
                        #     same class as the candidate (sibling method call), or
                        # (b) the caller is a nested class of the candidate's class
                        #     (inner class calling outer class method).
                        candidate_class = candidate_fqn.rsplit(".", 1)[0]
                        caller_has_sibling = candidate_class in caller_classes
                        caller_is_inner = caller_fqn.startswith(candidate_class + ".")
                        if not caller_has_sibling and not caller_is_inner:
                            continue
                        try:
                            call_graph.add_edge(caller_fqn, candidate_fqn)
                            existing_edge_fqns.add(edge_key)
                            # Update the caller→callee-class index incrementally.
                            caller_to_callee_classes.setdefault(caller_fqn, set()).add(candidate_class)
                            caller_classes = caller_to_callee_classes[caller_fqn]
                            logger.debug(f"Java: synthesised missing call edge {caller_fqn} -> {candidate_fqn}")
                        except ValueError:
                            pass

                # 3. Record/parametrized constructor: if the body contains "new ClassName("
                #    and we have a parametrized constructor node for that class, add the edge.
                for new_match in re.finditer(r"\bnew\s+(\w+)\s*\(", func_body):
                    class_name = new_match.group(1)
                    ctor_candidates = [
                        fqn
                        for fqn in name_to_fqns.get(class_name, [])
                        if fqn.endswith(f".{class_name}(") is False  # not no-arg
                        and f".{class_name}(" in fqn
                        and "," in fqn  # has params (simplistic check)
                    ]
                    for ctor_fqn in ctor_candidates:
                        if ctor_fqn not in call_graph.nodes:
                            continue
                        edge_key = (caller_fqn, ctor_fqn)
                        if edge_key in existing_edge_fqns:
                            continue
                        try:
                            call_graph.add_edge(caller_fqn, ctor_fqn)
                            existing_edge_fqns.add(edge_key)
                            logger.debug(f"Java: synthesised parametrised ctor edge {caller_fqn} -> {ctor_fqn}")
                        except ValueError:
                            pass
