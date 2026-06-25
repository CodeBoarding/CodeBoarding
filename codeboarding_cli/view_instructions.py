"""Tell the user how to view a generated analysis in the CodeBoarding webview.

A local ``codeboarding`` run writes ``analysis.json`` to disk but never uploads it,
so there is no URL the hosted webview can fetch. We therefore don't try to auto-open
anything; we just print how to load the file manually: open the webview and use its
"Load a file" picker. This works in every browser and needs no local server.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

WEBVIEW_BASE_URL = os.environ.get("CODEBOARDING_WEBVIEW_URL", "https://app.codeboarding.org").rstrip("/")


def print_view_instructions(analysis_path: Path) -> None:
    """Print how to view ``analysis_path`` in the webview (best-effort, never raises)."""
    try:
        analysis_path = Path(analysis_path).resolve()
        if not analysis_path.is_file():
            logger.warning("Analysis file not found at %s; nothing to view yet.", analysis_path)
            return
        logger.info(
            "\n".join(
                [
                    "",
                    "Analysis ready. View your architecture diagram:",
                    "",
                    f"  1. Open the webview:   {WEBVIEW_BASE_URL}",
                    "  2. Click 'Load a file' and pick:",
                    f"       {analysis_path}",
                    "",
                ]
            )
        )
    except Exception as exc:  # never break a successful run over this
        logger.warning("Could not print webview instructions: %s", exc)
