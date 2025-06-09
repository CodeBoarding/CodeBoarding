import logging
from typing import Optional, List
import re
from pathlib import Path

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field


class ModuleInput(BaseModel):
    python_code_reference: str = Field(
        description="Python code reference which to be loaded as source code. Example langchain.tools.tool")
    read_part: int = Field(
        description="Read part of the file. If the file is too large, you can specify which part to read.",
        default=0
    )


class CodeExplorerTool(BaseTool):
    name: str = "read_source_code"
    description: str = ("Tool which can read the source code of a python code reference. "
                        "You have to provide the complete path to the module."
                        "Like langchain.tools.tool or langchain_core.output_parsers.JsonOutputParser"
                        " and the return result will be the source code.")
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

    def _run(self, python_code_reference: str, read_part: int) -> str:
        """
        Run the tool with the given input.
        """
        logging.info(f"[Source Tool] Reading source code for {python_code_reference}")
        _, file_contents = self.read_file(python_code_reference=python_code_reference, read_part=read_part)
        return file_contents

    def read_file(self, python_code_reference: str, read_part: int = 0):
        """
        Read the file from the given path.
        """
        if ":" in python_code_reference:
            python_code_reference = python_code_reference.split(":")[0]

        for path in self.cached_files:
            sub_path = python_code_reference.replace('.', '/')
            if not sub_path.endswith(".py"):
                sub_path += ".py"
            sub_path = Path(sub_path)
            if self.is_subsequence(sub_path, path):
                logging.info(f"[Source Tool] Found file {path}")
                with open(path, 'r') as f:
                    return path, f"Source code for {python_code_reference}:\n{self.read_content(f, read_part)}"

        # maybe the path is to function so we have to check if the path is in the file
        for path in self.cached_files:
            sub_path = "/".join(python_code_reference.split('.')[:-1])
            if not sub_path.endswith(".py"):
                sub_path += ".py"
            sub_path = Path(sub_path)

            # Check if the path leads to a file and not a directory
            if self.is_subsequence(sub_path, path):
                logging.info(f"[Source Tool] Found file {path}")
                with open(path, 'r') as f:
                    return path, f"Source code for {python_code_reference}:\n{self.read_content(f, read_part)}"

            # Check for a file with __init__.py
            sub_path_init = Path(sub_path) / '__init__.py'
            if self.is_subsequence(sub_path_init, path):
                logging.info(f"[Source Tool] Found file {path}")
                with open(path, 'r') as f:
                    return path, f"Source code for {python_code_reference}:\n{self.read_content(f, read_part)}"

        # Last resolution the packages is file.Class.method:
        for path in self.cached_files:
            # Maybe the file is one ClassFile.method ->
            sub_path = "/".join(python_code_reference.split('.')[:-2])
            if not sub_path.endswith(".py"):
                sub_path += ".py"
            sub_path = Path(sub_path)

            if self.is_subsequence(sub_path, path):
                logging.info(f"[Source Tool] Found file {path}")
                with open(path, 'r') as f:
                    return path, f"Source code for {python_code_reference}:\n{self.read_content(f, read_part)}"

            # Check for a file with __init__.py
            sub_path_init = Path(sub_path) / '__init__.py'
            if self.is_subsequence(sub_path_init, path):
                logging.info(f"[Source Tool] Found file {path}")
                with open(path, 'r') as f:
                    return path, f"Source code for {python_code_reference}:\n{self.read_content(f, read_part)}"

        # Last chance: retry with class name being transformed to file name:
        transformed_path = transform_path(python_code_reference)
        if transformed_path != python_code_reference:
            logging.info(f"[Source Tool] Found file {transformed_path}")
            return self.read_file(transformed_path)

        logging.error(
            f"[Source Tool] File for {python_code_reference} not found. Available files are: {self.cached_files}")
        return None, f"[Source Tool -  Error] File for {python_code_reference} not found. Available files are: {', '.join([str(p) for p in self.cached_files])}"

    @staticmethod
    def read_content(file, part):
        contents = file.read()
        lines = contents.splitlines()
        content_lines = []
        if len(lines) > 1000:
            # The file is too long!
            # Now get the parts in 1000s of lines:
            for i in range(0, len(lines), 1000):
                content_lines.append("\n".join(lines[i:i + 1000]))
            logging.warning(
                f"[Source Tool] File is too long, splitting into parts of 1000 lines")
            return content_lines[part]
        return contents

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
