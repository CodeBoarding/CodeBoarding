import logging
from pathlib import Path
from typing import Optional, List

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field

from static_analyzer.analysis_result import StaticAnalysisResults


class ModuleInput(BaseModel):
    code_reference: str = Field(
        description=("The fully qualified code reference (import path) to the class, function, or method "
                     "whose source code is to be retrieved. "
                     "Examples: `langchain.tools.tool`, `langchain_core.output_parsers.JsonOutputParser`, "
                     "`langchain.agents.create_react_agent`. "
                     "Do not include file extensions (e.g., `.py`) or relative paths. "
                     "If a 'repos.' prefix is present in the agent's context, it should be omitted."))


class CodeReferenceReader(BaseTool):
    name: str = "getSourceCode"
    description: str = (
        "Retrieves source code for specific classes, methods, or functions. "
        "Use only when CFG analysis lacks critical implementation details. "
        "Provide complete import path (e.g., 'django.core.management.base.BaseCommand'). "
        "Note: Each call is expensive - prefer analyzing CFG data first. "
        "Use only when component responsibilities cannot be determined from CFG or package dependencies."
    )
    args_schema: Optional[ArgsSchema] = ModuleInput
    return_direct: bool = False
    cached_files: Optional[List[str]] = None
    static_analysis: Optional[StaticAnalysisResults] = None

    def __init__(self, static_analysis):
        super().__init__()
        self.cached_files = []
        self.static_analysis = static_analysis

    def _run(self, code_reference: str) -> str:
        """
        Run the tool with the given input.
        """
        logging.info(f"[Source Reference Tool] Reading source code for {code_reference}")

        # search for the qualified name:
        code_reference = code_reference.strip()
        if ":" in code_reference:
            code_reference = code_reference.replace(":", ".")

        languages = self.static_analysis.get_languages()
        for lang in languages:
            try:
                node = self.static_analysis.get_reference(lang, code_reference)
                return self.read_file(node.file_path, node.line_start, node.line_end)
            except ValueError:
                logging.warning(f"[Source Reference Tool] No reference found for {code_reference} in {lang}.")
                continue

    @staticmethod
    def read_file(file, start_line, end_line) -> str:
        """
        Read the file from the given path and return the specified lines.
        """
        file_path = Path(file)
        if not file_path.exists():
            logging.error(f"[Source Reference Tool] File {file_path} does not exist.")
            return f"Error: File {file_path} does not exist."

        with open(file_path, 'r') as f:
            lines = f.readlines()

        if start_line < 0 or end_line > len(lines):
            logging.error(f"[Source Reference Tool] Invalid line range: {start_line}-{end_line} for file {file_path}.")
            return f"Error: Invalid line range: {start_line}-{end_line} for file {file_path}."

        return ''.join(lines[start_line:end_line])
