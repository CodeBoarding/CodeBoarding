import sys as _sys
from importlib import import_module as _im

_sys.modules[__name__] = _im("duckdb_crud")


