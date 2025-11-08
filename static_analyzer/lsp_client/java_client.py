import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from static_analyzer import LSPClient

logger = logging.getLogger(__name__)


class JavaLSPClient(LSPClient):

    def start(self):
        # Eclipse JDT.LS needs an absolute path to configuration directory
        # Fix relative config path if present
        for i, param in enumerate(self.server_start_params):
            if param == '-configuration' and i + 1 < len(self.server_start_params):
                config_path = self.server_start_params[i + 1]
                if not os.path.isabs(config_path):
                    # Make it absolute relative to the current working directory
                    abs_config_path = os.path.abspath(config_path)
                    self.server_start_params[i + 1] = abs_config_path
                    logger.info(f"Resolved configuration path to: {abs_config_path}")
        
        self.server_start_params.append(str(self.project_path))
        logger.info(f"Starting server {' '.join(self.server_start_params)}...")
        
        self._process = subprocess.Popen(
            self.server_start_params,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.getcwd()  # Explicitly set working directory
        )

        # Start stderr reader thread to capture any error messages
        self._stderr_thread = threading.Thread(target=self._read_stderr)
        self._stderr_thread.daemon = True
        self._stderr_thread.start()

        self._reader_thread = threading.Thread(target=self._read_messages)
        self._reader_thread.daemon = True
        self._reader_thread.start()
        
        # Give the server a moment to start up
        logger.info("Waiting for Java LSP server to initialize...")
        time.sleep(3)  # Increased from 2 to 3 seconds
        
        # Check if process is still alive
        if self._process.poll() is not None:
            logger.error(f"Java LSP server process died with exit code {self._process.poll()}")
            raise RuntimeError("Java LSP server failed to start")
        
        logger.info("Java LSP server process is running, attempting initialization...")
        self._initialize()

    def _read_stderr(self):
        """Read and log stderr output from the server."""
        try:
            for line in iter(self._process.stderr.readline, b''):
                if not line:
                    break
                decoded_line = line.decode('utf-8', errors='ignore').strip()
                if decoded_line:
                    # Eclipse JDT.LS outputs a lot to stderr, including info messages
                    # Only log WARNING and ERROR level messages to avoid clutter
                    lower_line = decoded_line.lower()
                    if any(keyword in lower_line for keyword in ['error', 'exception', 'failed', 'fatal']):
                        logger.error(f"[Java LSP stderr]: {decoded_line}")
                    elif 'warn' in lower_line:
                        logger.warning(f"[Java LSP stderr]: {decoded_line}")
                    else:
                        logger.debug(f"[Java LSP stderr]: {decoded_line}")
        except Exception as e:
            logger.error(f"Error reading stderr: {e}")
