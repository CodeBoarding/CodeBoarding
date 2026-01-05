import abc
import logging
import os
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent_responses import AnalysisInsights, FilePath
from agents.prompts import get_file_classification_message
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class ReferenceResolverMixin(abc.ABC):
    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults):
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis

    @abc.abstractmethod
    def _parse_invoke(self, prompt, type):
        """Abstract method to be implemented by subclasses for LLM invocation."""
        pass

    def fix_source_code_reference_lines(self, analysis: AnalysisInsights):
        logger.info(f"Fixing source code reference lines for the analysis: {analysis.llm_str()}")
        for component in analysis.components:
            for reference in component.key_entities:
                # Check if the file is already resolved
                if reference.reference_file is not None and os.path.exists(reference.reference_file):
                    continue

                self._resolve_single_reference(reference, component.assigned_files)
        return self._relative_paths(analysis)

    def _resolve_single_reference(self, reference, assigned_files):
        """Orchestrates different resolution strategies for a single reference."""
        qname = reference.qualified_name.replace(os.sep, ".")

        for lang in self.static_analysis.get_languages():
            # Try exact match first
            if self._try_exact_match(reference, qname, lang):
                return

            # Try loose matching
            if self._try_loose_match(reference, qname, lang):
                return

            # Try file path resolution
            if self._try_file_path_resolution(reference, qname, lang):
                return

        # Final fallback: LLM resolution
        self._try_llm_resolution(reference, qname, assigned_files)

    def _try_exact_match(self, reference, qname, lang):
        """Attempts exact reference matching."""
        try:
            node = self.static_analysis.get_reference(lang, qname)
            reference.reference_file = node.file_path
            reference.reference_start_line = node.line_start + 1  # match 1 based indexing
            reference.reference_end_line = node.line_end + 1  # match 1 based indexing
            reference.qualified_name = qname
            logger.info(
                f"[Reference Resolution] Matched {reference.qualified_name} in {lang} at {reference.reference_file}"
            )
            return True
        except (ValueError, FileExistsError) as e:
            logger.warning(f"[Reference Resolution] Exact match failed for {reference.qualified_name} in {lang}: {e}")
            return False

    def _try_loose_match(self, reference, qname, lang):
        """Attempts loose reference matching."""
        try:
            _, node = self.static_analysis.get_loose_reference(lang, qname)
            if node is not None:
                reference.reference_file = node.file_path
                reference.reference_start_line = node.line_start + 1
                reference.reference_end_line = node.line_end + 1
                reference.qualified_name = qname
                logger.info(
                    f"[Reference Resolution] Loosely matched {reference.qualified_name} in {lang} at {reference.reference_file}"
                )
                return True
        except Exception as e:
            logger.warning(f"[Reference Resolution] Loose match failed for {qname} in {lang}: {e}")
        return False

    def _try_file_path_resolution(self, reference, qname, lang):
        """Attempts to resolve reference through file path matching."""
        # First try existing reference file path
        if self._try_existing_reference_file(reference, lang):
            return True

        # Then try qualified name as file path
        return self._try_qualified_name_as_path(reference, qname, lang)

    def _try_existing_reference_file(self, reference, lang):
        """Tries to resolve using existing reference file path."""
        if (reference.reference_file is not None) and (not Path(reference.reference_file).is_absolute()):
            joined_path = os.path.join(self.repo_dir, reference.reference_file)
            if os.path.exists(joined_path):
                reference.reference_file = joined_path
                logger.info(
                    f"[Reference Resolution] File path matched for {reference.qualified_name} in {lang} at {reference.reference_file}"
                )
                return True
            else:
                reference.reference_file = None
        return False

    def _try_qualified_name_as_path(self, reference, qname, lang):
        """Tries to resolve qualified name as various file path patterns."""
        file_path = qname.replace(".", os.sep)  # Get file path
        full_path = os.path.join(self.repo_dir, file_path)
        file_ref = ".".join(full_path.rsplit(os.sep, 1))
        paths = [full_path, f"{file_path}.py", f"{file_path}.ts", f"{file_path}.tsx", file_ref]

        for path in paths:
            if os.path.exists(path):
                reference.reference_file = str(path)
                logger.info(
                    f"[Reference Resolution] Path matched for {reference.qualified_name} in {lang} at {reference.reference_file}"
                )
                return True
        return False

    def _try_llm_resolution(self, reference, qname, assigned_files):
        """Uses LLM as final fallback for reference resolution."""
        if reference.reference_file is None:
            prompt = PromptTemplate(
                template=get_file_classification_message(), input_variables=["qname", "files"]
            ).format(qname=qname, files="\n".join(assigned_files))
            file_assignment = self._parse_invoke(prompt, FilePath)
            logger.info(f"[Reference Resolution] LLM matched {reference.qualified_name} at {file_assignment.file_path}")
            reference.reference_file = file_assignment.file_path
            reference.reference_start_line = file_assignment.start_line
            reference.reference_end_line = file_assignment.end_line

            if reference.reference_file is None:
                logger.error(
                    f"[Reference Resolution] Reference file could not be resolved for {reference.qualified_name} in any language."
                )

    def _relative_paths(self, analysis: AnalysisInsights):
        """Convert all reference file paths to relative paths."""
        for component in analysis.components:
            for reference in component.key_entities:
                if reference.reference_file and reference.reference_file.startswith(str(self.repo_dir)):
                    reference.reference_file = os.path.relpath(reference.reference_file, self.repo_dir)
        return analysis
