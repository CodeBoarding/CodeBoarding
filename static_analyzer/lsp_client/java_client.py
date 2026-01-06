"""
Java LSP client using Eclipse JDT Language Server.
"""

import time
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List, Optional
import logging

from .client import LSPClient
from ..java_config_scanner import JavaProjectConfig
from ..java_utils import create_jdtls_command, get_java_version, find_java_21_or_later, detect_java_installations
from ..programming_language import ProgrammingLanguage
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
        jdtls_root: Optional[Path] = None,
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
        self.workspace_dir = None  # Will be created in start()
        self.temp_workspace = True

        # Try to get jdtls_root from language config first, then from parameter
        if jdtls_root is not None:
            self.jdtls_root = jdtls_root
        else:
            # Get from language config_extra
            jdtls_root_str = language.config_extra.get("jdtls_root")
            if jdtls_root_str:
                self.jdtls_root = Path(jdtls_root_str)
            else:
                self.jdtls_root = None

        # Initialize base LSPClient
        super().__init__(project_path, language, ignore_manager)

        # Track import status
        self.import_complete = False
        self.import_errors: List[str] = []
        self.java_home = None  # Will be detected in start()

    def start(self):
        """Start the JDTLS server with proper command construction."""
        # Create workspace directory
        self.workspace_dir = Path(tempfile.mkdtemp(prefix="jdtls-workspace-"))
        self.temp_workspace = True

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
            f"Starting JavaClient for {self.project_config.root} " f"(build system: {self.project_config.build_system})"
        )

        # Call parent start() which will use our server_start_params
        super().start()

    def _find_jdtls_root(self) -> Optional[Path]:
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
        """Calculate appropriate heap size for project."""
        # Count Java files as rough size indicator
        java_files = list(self.project_config.root.rglob("*.java"))
        file_count = len(java_files)

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

        import os

        params = {
            "processId": os.getpid(),
            "rootUri": self.project_path.as_uri(),
            "capabilities": self._get_capabilities(),
            "initializationOptions": self._get_initialization_options(),
        }

        init_id = self._send_request("initialize", params)
        response = self._wait_for_response(init_id)

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
        runtimes = []

        for jdk in jdks[:5]:  # Limit to 5 most recent
            java_cmd = jdk / "bin" / "java"
            version = get_java_version(str(java_cmd))
            if version > 0:
                runtime_entry = {
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

    def _validate_project_loaded(self):
        """Verify project loaded successfully."""
        try:
            # Test workspace/symbol request
            params = {"query": ""}
            req_id = self._send_request("workspace/symbol", params)
            response = self._wait_for_response(req_id)

            if "error" in response:
                logger.warning(f"Project validation failed: {response['error']}")
                return

            symbols = response.get("result", [])

            if not symbols:
                logger.warning("No workspace symbols found - project may not be fully loaded")
            else:
                logger.info(f"Project loaded successfully ({len(symbols)} symbols indexed)")

        except Exception as e:
            logger.warning(f"Project validation failed: {e}")

    def handle_notification(self, method: str, params: dict):
        """
        Handle notifications from JDTLS.

        Tracks import progress and completion.
        """
        # Track language/status notifications for import progress
        if method == "language/status":
            if params.get("type") == "Started":
                logger.debug("JDTLS: Project import started")
            elif params.get("type") == "ProjectStatus" and params.get("message") == "OK":
                self.import_complete = True
                logger.debug("JDTLS: Project import complete")

        # Track progress notifications
        elif method == "$/progress":
            if "message" in params:
                logger.debug(f"JDTLS: {params['message']}")

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
            content = file_path.read_text(encoding="utf-8")
            lines = content.split("\n")

            for line in lines[:20]:  # Check first 20 lines
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
