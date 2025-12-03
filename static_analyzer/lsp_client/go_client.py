import logging
import os
import time

from .client import LSPClient, FileAnalysisResult
from .call_graph_collection import GoCallGraphCollector

logger = logging.getLogger(__name__)


class GoClient(LSPClient):
    """
    Go-specific Language Server Protocol client for gopls.
    Extends the base LSPClient with Go-specific functionality.
    """

    def __init__(self, project_path, language):
        super().__init__(project_path, language)
        self._warmup_complete = False

    def _fallback_call_graph_collection(self, call_graph, source_files: list) -> int:
        """
        Go-specific fallback for call graph collection using regex scanning.

        Args:
            call_graph: The CallGraph to update with edges
            source_files: List of source files to scan

        Returns:
            Number of edges added
        """
        logger.info("Using Go-specific fallback call graph collection...")
        collector = GoCallGraphCollector(self.project_path, call_graph, source_files)
        return collector.collect_call_edges()
