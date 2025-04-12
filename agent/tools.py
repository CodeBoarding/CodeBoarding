from langchain.tools import tool

import importlib
import inspect

from pydantic import BaseModel, Field


class ModuleInput(BaseModel):
    python_code_reference: str = Field(
        description="Python code reference which to be loaded as source code. Example langchain.tools.tool")


@tool("read_source_code", args_schema=ModuleInput)
def read_module_tool(python_code_reference: str) -> str:
    """
    Tool which can read the source code of a python code reference. You have to provide the complete path to the module.
    Like langchain.tools.tool or langchain_core.output_parsers.JsonOutputParser and the return result will be the source code.
    """
    try:
        parts = python_code_reference.split('.')
        path, module, attrs = None, None, None
        for i in range(len(parts), 0, -1):
            try:
                path = '.'.join(parts[:i])
                module = importlib.import_module(path)
                attrs = parts[i:]
                break
            except ModuleNotFoundError:
                continue
        if module is None or attrs is None:
            raise ImportError(f"Module {path} not found.")

        if len(attrs) == 2:  # high chance that this is a method in a class!
            obj = getattr(module, attrs[0])
            if hasattr(obj, attrs[1]):
                obj = getattr(obj, attrs[1])
                return f"Source code for {python_code_reference}:\n{inspect.getsource(obj)}"

        # last resolution try to import and give any source code!
        for i in range(len(attrs), 0, -1):
            try:
                attribute = '.'.join(attrs[:i])
                obj = getattr(module, attribute)
                return f"Source code for {path + '.' + attribute}:\n{inspect.getsource(obj)}"
            except Exception as e:
                print("Bad import ", e)
                continue
        raise ImportError(f"Attribute {'.'.join(attrs)} not found in module {path}.")
    except ImportError as e:
        return f"Error: {e}. Please provide a valid python code reference."
