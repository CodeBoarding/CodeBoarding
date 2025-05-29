import importlib
import inspect
import logging
from typing import Optional, List
import re
from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field


class ModuleInput(BaseModel):
    python_code_reference: str = Field(
        description="Python code reference which to be loaded as source code. Example langchain.tools.tool")


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

    def _run(self, python_code_reference: str) -> str:
        """
        Run the tool with the given input.
        """
        logging.info(f"[Source Tool] Reading source code for {python_code_reference}")
        _, file_contents = self.read_file(python_code_reference=python_code_reference)
        return file_contents

    def read_file(self, python_code_reference: str):
        """
        Read the file from the given path.
        """
        if ":" in python_code_reference:
            python_code_reference = python_code_reference.split(":")[0]
        for path in self.cached_files:
            sub_path = python_code_reference.replace('.', '/')
            if sub_path in str(path):
                logging.info(f"[Source Tool] Found file {path}")
                with open(path, 'r') as f:
                    return path, f"Source code for {python_code_reference}:\n{f.read()}"

        # maybe the path is to function so we have to check if the path is in the file
        for path in self.cached_files:
            sub_group = "/".join(python_code_reference.split('.')[:-1])
            # Check if the path leads to a file and not a directory
            if sub_group in str(path) and str(path).endswith('.py'):
                logging.info(f"[Source Tool] Found file {path}")
                with open(path, 'r') as f:
                    return path, f"Source code for {python_code_reference}:\n{f.read()}"

        # Last resolution the packages is file.Class.method:
        for path in self.cached_files:
            # Maybe the file is one ClassFile.method ->
            sub_group = "/".join(python_code_reference.split('.')[:-2])
            if sub_group in str(path) and str(path).endswith('.py'):
                logging.info(f"[Source Tool] Found file {path}")
                with open(path, 'r') as f:
                    return path, f"Source code for {python_code_reference}:\n{f.read()}"

        # Last chance: retry with class name being transformed to file name:
        transformed_path = transform_path(python_code_reference)
        if transformed_path != python_code_reference:
            logging.info(f"[Source Tool] Found file {transformed_path}")
            return self.read_file(transformed_path)

        logging.error(
            f"[Source Tool] File for {python_code_reference} not found. Available files are: {self.cached_files}")
        return None, f"[Source Tool -  Error] File for {python_code_reference} not found. Available files are: {self.cached_files}"


def pascal_to_snake_segment(text):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', text).lower()


def transform_path(path):
    parts = path.split('.')
    parts[-1] = pascal_to_snake_segment(parts[-1])  # only transform the last segment
    return '.'.join(parts)
