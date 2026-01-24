"""
Java LSP client using Eclipse JDT Language Server.
"""

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from static_analyzer.lsp_client.client import LSPClient
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


class JavaClient(LSPClient):
    """
    LSP client for Java using Eclipse JDT Language Server (JDTLS).

    Handles Java-specific initialization, project import, and workspace management.
    """

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
        """Try to find JDTLS root directory from various locations."""
        # Check common locations
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

    def _prepare_for_analysis(self):
        """
        Prepare for analysis by validating the Java project is indexed.

        This is called before file-by-file analysis starts. If workspace indexing
        is successful, we can use optimized class hierarchy analysis later.
        If not, we fall back to per-file analysis.

        First attempts quick validation, then if needed retries with longer timeout.
        """
        logger.info("Validating project is fully indexed...")

        # Try a few quick attempts (3 seconds total) to see if workspace/symbol works
        symbols = self._retry_workspace_symbol_request(
            query="",
            max_attempts=3,
            retry_delay=1.0,
            request_timeout=5,
            log_prefix="validation/workspace/symbol",
        )

        if symbols:
            self.workspace_indexed = True
            logger.info(f"Project loaded and indexed successfully ({len(symbols)} symbols available)")
        else:
            # workspace/symbol didn't work, but that's OK - per-file analysis will work anyway
            logger.debug(
                "workspace/symbol not available after validation and retry. "
                "This is normal for large projects. Per-file analysis will still work."
            )
            self.workspace_indexed = False

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
