import logging
import os
import time

from .client import LSPClient

logger = logging.getLogger(__name__)


class TypeScriptClient(LSPClient):
    """
    TypeScript/JavaScript-specific Language Server Protocol client.
    Extends the base LSPClient with TypeScript-specific functionality.
    """

    def handle_notification(self, method: str, params: dict):
        """
        Handle notifications from the TypeScript language server.

        TypeScript language server notifications are not needed for our analysis,
        so this method intentionally does nothing. The base implementation is sufficient.

        Args:
            method: The LSP notification method name
            params: The notification parameters
        """
        # TypeScript LSP server notifications are not needed for static analysis
        # The server handles project loading internally without requiring client tracking
        pass

    def start(self):
        """Starts the language server with dependency check."""
        # Check and install dependencies if needed
        self._ensure_dependencies()

        # Call parent start method
        super().start()

    def _ensure_dependencies(self):
        """Check if node_modules exists and log an error if they don't."""
        node_modules_path = self.project_path / "node_modules"

        if node_modules_path.exists():
            logger.info(f"node_modules found at: {node_modules_path}")
            return

        logger.warning(f"node_modules not found in {self.project_path}")

        # Check if package.json exists
        package_json = self.project_path / "package.json"
        if not package_json.exists():
            logger.warning(f"package.json not found in {self.project_path}.")
            return

    def _initialize(self):
        """Performs the LSP initialization handshake."""
        logger.info(f"Initializing connection for {self.language_id}...")
        params = {
            "processId": os.getpid(),
            "rootUri": self.project_path.as_uri(),
            "capabilities": {
                "textDocument": {
                    "callHierarchy": {"dynamicRegistration": True},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "typeHierarchy": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "semanticTokens": {"dynamicRegistration": True},
                },
                "workspace": {
                    "configuration": True,
                    "workspaceFolders": True,
                    "didChangeConfiguration": {"dynamicRegistration": True},
                },
            },
            "workspace": {"applyEdit": True, "workspaceEdit": {"documentChanges": True}},
        }

        # Allow subclasses to customize initialization parameters
        params = self._customize_initialization_params(params)

        init_id = self._send_request("initialize", params)
        # Use longer timeout for initialization as it may involve full workspace indexing
        response = self._wait_for_response(init_id, timeout=360)

        if "error" in response:
            raise RuntimeError(f"Initialization failed: {response['error']}")

        logger.info("Initialization successful.")
        self._send_notification("initialized", {})

        # Allow subclasses to perform post-initialization setup
        self._configure_typescript_workspace()

    def _customize_initialization_params(self, params: dict) -> dict:
        """Add TypeScript-specific initialization parameters."""
        params["workspaceFolders"] = [{"uri": self.project_path.as_uri(), "name": self.project_path.name}]

        params["initializationOptions"] = {
            "preferences": {"includeCompletionsForModuleExports": True, "includeCompletionsWithSnippetText": True},
            "tsserver": {"logVerbosity": "off"},  # Reduce noise in logs
        }

        return params

    def _configure_typescript_workspace(self):
        """Send TypeScript-specific workspace configuration after initialization."""
        try:
            # Check if we have TypeScript/JavaScript files
            ts_files = self._find_typescript_files()

            if not ts_files:
                logger.warning(f"No TypeScript/JavaScript files found in {self.project_path}")
                return

            logger.info(f"Found {len(ts_files)} TypeScript/JavaScript files")

            # Notify workspace folders change
            self._send_notification(
                "workspace/didChangeWorkspaceFolders",
                {
                    "event": {
                        "added": [{"uri": self.project_path.as_uri(), "name": self.project_path.name}],
                        "removed": [],
                    }
                },
            )

            # Process configuration files
            config_found = self._process_config_files()

            # Bootstrap project by opening sample files
            self._bootstrap_project(ts_files, config_found)

        except Exception as e:
            logger.warning(f"Failed to configure TypeScript workspace: {e}")

    def _find_typescript_files(self) -> list:
        """Find all TypeScript/JavaScript files in the project."""
        all_files = []
        for pattern in ["*.ts", "*.tsx", "*.js", "*.jsx"]:
            all_files.extend(list(self.project_path.rglob(pattern)))

        return self.filter_src_files(all_files)

    def _process_config_files(self) -> bool:
        """Process TypeScript configuration files and return True if any found."""
        config_files = [
            self.project_path / "tsconfig.json",
            self.project_path / "jsconfig.json",
            self.project_path / "package.json",
        ]

        config_found = False
        for config_path in config_files:
            if config_path.exists():
                logger.info(f"Found configuration file: {config_path}")
                config_found = True
                self._send_notification(
                    "workspace/didChangeWatchedFiles",
                    {"changes": [{"uri": config_path.as_uri(), "type": 1}]},  # Created/Changed
                )

        return config_found

    def _bootstrap_project(self, ts_files: list, config_found: bool):
        """Bootstrap TypeScript project by opening files."""
        logger.info("Opening sample files to bootstrap TypeScript project...")
        # Files are already filtered in _find_typescript_files
        sample_files = ts_files[:3]

        # Open bootstrap files
        for file_path in sample_files:
            try:
                content = file_path.read_text(encoding="utf-8")
                file_uri = file_path.as_uri()
                self._send_notification(
                    "textDocument/didOpen",
                    {"textDocument": {"uri": file_uri, "languageId": self.language_id, "version": 1, "text": content}},
                )
                logger.debug(f"Opened bootstrap file: {file_path}")
            except Exception as e:
                logger.debug(f"Could not open bootstrap file {file_path}: {e}")

        # Wait for project initialization
        wait_time = 5 if config_found else 8
        logger.info(f"Waiting {wait_time}s for TypeScript server to initialize project...")
        time.sleep(wait_time)

        # Validate and close bootstrap files
        if self._validate_typescript_project():
            logger.info("TypeScript project successfully loaded!")
        else:
            logger.warning("TypeScript project still not loaded, but continuing...")

        self._close_bootstrap_files(sample_files)

    def _close_bootstrap_files(self, sample_files: list):
        """Close bootstrap files that were opened for project initialization."""
        for file_path in sample_files:
            try:
                self._send_notification("textDocument/didClose", {"textDocument": {"uri": file_path.as_uri()}})
            except Exception:
                pass

    def _prepare_for_analysis(self):
        """TypeScript-specific preparation before analysis."""
        logger.info("Waiting additional time for TypeScript server to fully initialize...")
        time.sleep(2)

        logger.debug("Validating TypeScript project is loaded...")

        # Use unified retry helper with single attempt
        symbols = self._retry_workspace_symbol_request(
            query="",
            max_attempts=1,
            request_timeout=10,
            log_prefix="validation/typescript/workspace/symbol",
        )

        # If we got symbols, project is loaded
        if symbols:
            logger.debug("TypeScript project validation successful")
        else:
            logger.warning("TypeScript project validation inconclusive, but continuing")
