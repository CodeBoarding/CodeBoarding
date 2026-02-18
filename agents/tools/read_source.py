import logging
from pathlib import Path
from typing import Optional
from langchain_core.tools import ArgsSchema
from pydantic import BaseModel, Field
from agents.tools.base import BaseRepoTool

logger = logging.getLogger(__name__)


class ModuleInput(BaseModel):
    code_reference: str = Field(
        description=(
            "The fully qualified code reference (import path) to the class, function, or method "
            "whose source code is to be retrieved. "
            "Examples: `langchain.tools.tool`, `langchain_core.output_parsers.JsonOutputParser`, "
            "`langchain.agents.create_agent`. "
            "Do not include file extensions (e.g., `.py`) or relative paths. "
            "If a 'repos.' prefix is present in the agent's context, it should be omitted."
        )
    )


class CodeReferenceReader(BaseRepoTool):
    name: str = "getSourceCode"
    description: str = (
        "Retrieves source code for specific classes, methods, or functions. "
        "Use only when CFG analysis lacks critical implementation details. "
        "Provide complete import path (e.g., 'django.core.management.base.BaseCommand'). DO NOT USE WITH FILE PATHS i.e. file.ext (.py, .ts, .c, etc.) for that use the `readFile` tool"
        "Note: Each call is expensive - prefer analyzing CFG data first. "
        "Use only when component responsibilities cannot be determined from CFG or package dependencies."
    )
    args_schema: Optional[ArgsSchema] = ModuleInput
    return_direct: bool = False

    def _run(self, code_reference: str) -> str:
        """
        Run the tool with the given input.
        """
        logger.info(f"[Source Reference Tool] Reading source code for {code_reference}")

        if self.static_analysis is None:
            logger.error("[Source Reference Tool] static_analysis is not set")
            return "Error: Static analysis is not set."

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
                logger.warning(f"[Source Reference Tool] No reference found for {code_reference} in {lang}.")
                # retry with loose matching
                text, loose_node = self.static_analysis.get_loose_reference(lang, code_reference)
                if loose_node is None:
                    logger.warning(f"[Source Reference Tool] No loose reference found for {code_reference} in {lang}.")
                    continue
                source_code = self.read_file(loose_node.file_path, loose_node.line_start, loose_node.line_end)
                logger.info(
                    f"[Source Reference Tool] Loose match found {code_reference} -> {text}, reading source code."
                )
                if text is None:
                    return source_code
                return text + "\n\n" + source_code
            except FileExistsError:
                logger.warning(
                    f"[Source Reference Tool] File not found for {code_reference} in {lang}. Make use of the `readFile` tool to read the file content directly."
                )
                return (
                    f"INFO: {code_reference} is a reference to a file/package and not a specific class or method. "
                    f"Please use the `readFile` tool to read the file content."
                )
        logger.warning(
            f"[Source Reference Tool] No source code reference found for {code_reference} in any language. "
            f"Suggesting to use our file read tooling."
        )
        return (
            "No source code reference was found for the given code reference. "
            "However it is possible that this is a directory use the `getFileStructure` tool to retrieve the file structure of the project. "
            "It can also be a source file path for that use the `readFile` tool and retrieve the document."
        )

    @staticmethod
    def read_file(file, start_line, end_line) -> str:
        """
        Read the file from the given path and return the specified lines.
        """
        file_path = Path(file)
        if not file_path.exists():
            logger.error(f"[Source Reference Tool] File {file_path} does not exist.")
            return f"Error: File {file_path} does not exist."

        with open(file_path, "r") as f:
            lines = f.readlines()

        if start_line < 0 or end_line > len(lines):
            logger.error(f"[Source Reference Tool] Invalid line range: {start_line}-{end_line} for file {file_path}.")
            return f"Error: Invalid line range: {start_line}-{end_line} for file {file_path}."
        logger.info(f"[Source Reference Tool] Success, reading from: {file_path}.")
        return "".join(lines[start_line:end_line])
