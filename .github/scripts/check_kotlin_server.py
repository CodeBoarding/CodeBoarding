"""Start the pinned kotlin-lsp, and report how long its build has left."""

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

from infra.install import ensure_tools
from infra.tool_registry import get_servers_dir
from static_analyzer.engine.adapters.kotlin_adapter import KotlinAdapter
from static_analyzer.engine.lsp_client import LSPClient

BUILD_LIFETIME_DAYS = 30


def days_left(release_date: str) -> int:
    """How long a build has before it stops starting, from its ``majorVersionReleaseDate``."""
    built = datetime.strptime(release_date, "%Y%m%d").date()
    return BUILD_LIFETIME_DAYS - (date.today() - built).days


def main() -> None:
    ensure_tools(languages={"kotlin"})
    product = json.loads((get_servers_dir() / "bin" / "kotlin-lsp" / "product-info.json").read_text())
    version = product["buildNumber"]

    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory)
        adapter = KotlinAdapter()
        with LSPClient(
            adapter.get_lsp_command(project), project, startup_timeout=adapter.get_lsp_startup_timeout()
        ) as client:
            print(f"kotlin-lsp {version} started as pid {client.pid}")

    left = days_left(product["majorVersionReleaseDate"])
    print(f"Built {product['majorVersionReleaseDate']}, about {left} days left")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as file:
            file.write(f"version={version}\ndays_left={left}\n")


if __name__ == "__main__":
    main()
