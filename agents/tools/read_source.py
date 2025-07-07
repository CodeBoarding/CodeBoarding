import logging
from typing import Optional, List
import re
from pathlib import Path

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field

from static_analyzer.reference_lines import find_fqn_location


class ModuleInput(BaseModel):
    python_code_reference: str = Field(
        description=("The fully qualified Python reference (import path) to the module, class, function, or method "
                     "whose source code is to be retrieved. "
                     "Examples: `langchain.tools.tool`, `langchain_core.output_parsers.JsonOutputParser`, "
                     "`langchain.agents.create_react_agent`. "
                     "Do not include file extensions (e.g., `.py`) or relative paths. "
                     "If a 'repos.' prefix is present in the agent's context, it should be omitted."))


class CodeReferenceReader(BaseTool):
    name: str = "getPythonSourceCode"
    description: str = (
        "Retrieves the source code for a specified Python module, class, function, or method. "
        "Provide the complete Python import path (fully qualified name) to the target. "
        "For example, to get the source code for a module, use `langchain.tools.tool`. "
        "To get the source code for a specific class or function, use `langchain_core.output_parsers.JsonOutputParser` "
        "or `langchain.agents.create_react_agent`. "
        "The tool will return the relevant block of source code, including line numbers for context."
    )
    args_schema: Optional[ArgsSchema] = ModuleInput
    return_direct: bool = False
    cached_files: Optional[List[str]] = None

    def __init__(self, repo_dir):
        super().__init__()
        self.cached_files = []
        self.walk_dir(repo_dir)

    def walk_dir(self, root_project_dir):
        """
        Walk the directory and collect all files.
        """
        for path in root_project_dir.rglob('*.py'):
            self.cached_files.append(path)

    def _run(self, python_code_reference: str) -> str:
        """
        Run the tool with the given input.
        """
        logging.info(f"[Source Reference Tool] Reading source code for {python_code_reference}")
        file_path, file_contents = self.read_file(python_code_reference=python_code_reference)

        if file_path is None:
            return (
                f"Error: The specified python element '{python_code_reference}' "
                f"was not found in the indexed source files. {', '.join([str(f) for f in self.cached_files])}. "
                f"Please ensure the full qualified name is correct and points to an existing module, class, function, or method."
            )

        qname = python_code_reference.replace(":", ".")
        parts = qname.split(".")
        start_line, end_line = -1, -1
        for i in range(len(parts)):
            sub_fqn = ".".join(parts[i:])
            result = find_fqn_location(file_contents, sub_fqn)
            if result:
                start_line, end_line = result[0], result[1]
                break

        if start_line == -1 or end_line == -1:
            logging.warning(
                f"[Source Reference Tool] Qualified name {python_code_reference} not found in file contents.")
            return (
                f"The specified Python element '{python_code_reference}' (qualified name '{qname}') "
                f"was not found in its corresponding source file '{file_path}'. "
                f"Please ensure the full qualified name is correct and points to an existing module, class, function, or method."
                f"You can try to use a more specific name or check the file with the readFile tool."
            )

        relevant_lines = file_contents.split("\n")[start_line - 1:end_line]
        final_content = ""
        i = 0

        for line in relevant_lines:
            final_content += f"{start_line + i}:{line}\n"
            i += 1
        logging.info(f"[Source Reference Tool] Found code reference for {sub_fqn} in {file_path}")
        return f"Found code reference for {sub_fqn} in {file_path}:\n{final_content}"

    def read_file(self, python_code_reference: str):
        """
        Read the file from the given path.
        """
        if ":" in python_code_reference:
            python_code_reference = python_code_reference.split(":")[0]

        for path in self.cached_files:
            sub_path = python_code_reference.replace('.', '/')
            if sub_path.endswith("/py"):
                sub_path = sub_path[:-3] + ".py"
            elif not sub_path.endswith(".py"):
                sub_path += ".py"
            sub_path = Path(sub_path)
            if self.is_subsequence(sub_path, path):
                logging.info(f"[Source Reference Tool] Found file {path} for {python_code_reference}")
                with open(path, 'r') as f:
                    return path, f.read()

        # maybe the path is to function so we have to check if the path is in the file
        for path in self.cached_files:
            sub_path = "/".join(python_code_reference.split('.')[:-1])
            if sub_path.endswith("/py"):  # In the case that it is normal path like /path/to/it.py
                sub_path = sub_path[:-3] + ".py"
            elif not sub_path.endswith(".py"):
                sub_path += ".py"
            sub_path = Path(sub_path)

            # Check if the path leads to a file and not a directory
            if self.is_subsequence(sub_path, path):
                logging.info(f"[Source Reference Tool] Found file {path} for {python_code_reference}")
                with open(path, 'r') as f:
                    return path, f.read()

            # Check for a file with __init__.py
            sub_path_init = Path(sub_path) / '__init__.py'
            if self.is_subsequence(sub_path_init, path):
                logging.info(f"[Source Reference Tool] Found file {path} for {python_code_reference}")
                with open(path, 'r') as f:
                    return path, f.read()

        # Last resolution the packages is file.Class.method:
        for path in self.cached_files:
            # Maybe the file is one ClassFile.method ->
            sub_path = "/".join(python_code_reference.split('.')[:-2])
            if not sub_path.endswith(".py"):
                sub_path += ".py"
            sub_path = Path(sub_path)

            if self.is_subsequence(sub_path, path):
                logging.info(f"[Source Reference Tool] Found file {path} for {python_code_reference}")
                with open(path, 'r') as f:
                    return path, f.read()

            # Check for a file with __init__.py
            sub_path_init = Path(sub_path) / '__init__.py'
            if self.is_subsequence(sub_path_init, path):
                logging.info(f"[Source Reference Tool] Found file {path} for {python_code_reference}")
                with open(path, 'r') as f:
                    return path, f.read()

        # Last chance: retry with class name being transformed to file name:
        transformed_path = transform_path(python_code_reference)
        if transformed_path != python_code_reference:
            logging.info(f"[Source Reference Tool] Found file {transformed_path} for {python_code_reference}")
            return self.read_file(transformed_path)

        logging.error(
            f"[Source Reference Tool] File for {python_code_reference} not found. # of available files is {len(self.cached_files)}")
        return None, None

    def is_subsequence(self, sub: Path, full: Path) -> bool:
        sub = sub.parts
        full = full.parts
        for i in range(len(full) - len(sub) + 1):
            if full[i:i + len(sub)] == sub:
                return True
        return False


def pascal_to_snake_segment(text):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', text).lower()


def transform_path(path):
    parts = path.split('.')
    parts[-1] = pascal_to_snake_segment(parts[-1])  # only transform the last segment
    return '.'.join(parts)


if __name__ == '__main__':
    CodeReferenceReader(repo_dir=Path("/home/ivan/StartUp/CodeBoarding/repos/django"))._run(
        "django.django.core.handlers.base.BaseHandler")
