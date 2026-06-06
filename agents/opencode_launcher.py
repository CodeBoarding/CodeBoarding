"""Manages OpenCode server lifecycle with CodeBoarding MCP integration."""

import json
import logging
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_free_port() -> int:
    """Find a free port for the OpenCode server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


class OpenCodeLauncher:
    """Launches and manages OpenCode server with CodeBoarding MCP integration.

    Usage:
        with OpenCodeLauncher(repo_dir) as launcher:
            client = ChatOpenCode(base_url=launcher.base_url)
            # ... use client ...
        # Server is automatically cleaned up
    """

    def __init__(
        self,
        repo_dir: Path,
        port: int | None = None,
        hostname: str = "127.0.0.1",
        password: str | None = None,
        timeout: int = 30,
    ):
        self.repo_dir = repo_dir
        self.port = port or _find_free_port()
        self.hostname = hostname
        self.password = password
        self.timeout = timeout
        self.base_url = f"http://{hostname}:{self.port}"
        self._process: subprocess.Popen | None = None

    def _build_mcp_config(self) -> dict:
        """Build MCP config for CodeBoarding tools."""
        mcp_script = self.repo_dir / "codeboarding_mcp_server.py"
        return {
            "mcp": {
                "codeboarding": {
                    "type": "local",
                    "command": ["uv", "run", "python", str(mcp_script)],
                    "environment": {
                        "CODEBOARDING_REPO_DIR": str(self.repo_dir.resolve()),
                    },
                    "enabled": True,
                }
            }
        }

    def _build_env(self) -> dict:
        """Build environment variables for OpenCode server."""
        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(self._build_mcp_config())
        if self.password:
            env["OPENCODE_SERVER_PASSWORD"] = self.password
        return env

    def _wait_for_health(self) -> bool:
        """Wait for OpenCode server to become healthy."""
        url = f"{self.base_url}/global/health"
        for _ in range(self.timeout):
            try:
                req = urllib.request.Request(url)
                if self.password:
                    creds = b64encode(f"opencode:{self.password}".encode()).decode()
                    req.add_header("Authorization", f"Basic {creds}")
                with urllib.request.urlopen(req, timeout=2) as resp:
                    data = json.loads(resp.read().decode())
                    if data.get("healthy", False):
                        logger.info(f"OpenCode server healthy at {self.base_url}")
                        return True
            except Exception as exc:
                logger.debug("OpenCode health check attempt failed: %s", exc)
            time.sleep(1)
        return False

    def start(self) -> str:
        """Start OpenCode server and wait for it to be ready.

        Returns:
            base_url: The URL of the OpenCode server.
        """
        if self._process is not None:
            return self.base_url

        logger.info(f"Starting OpenCode server on {self.base_url}")
        cmd = ["opencode", "serve", "--port", str(self.port), "--hostname", self.hostname]

        self._process = subprocess.Popen(
            cmd,
            env=self._build_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not self._wait_for_health():
            self.stop()
            raise RuntimeError(f"OpenCode server failed to start within {self.timeout}s")

        return self.base_url

    def stop(self):
        """Stop the OpenCode server."""
        if self._process is None:
            return

        logger.info("Stopping OpenCode server")
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        finally:
            self._process = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    @property
    def is_running(self) -> bool:
        """Check if the OpenCode server is running."""
        if self._process is None:
            return False
        return self._process.poll() is None
