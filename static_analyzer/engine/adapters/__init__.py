"""Language adapter registry."""

from __future__ import annotations

from static_analyzer.config import AdapterName
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.adapters.csharp_adapter import CSharpAdapter
from static_analyzer.engine.adapters.fsharp_adapter import FSharpAdapter
from static_analyzer.engine.adapters.go_adapter import GoAdapter
from static_analyzer.engine.adapters.java_adapter import JavaAdapter
from static_analyzer.engine.adapters.php_adapter import PHPAdapter
from static_analyzer.engine.adapters.python_adapter import PythonAdapter
from static_analyzer.engine.adapters.rust_adapter import RustAdapter
from static_analyzer.engine.adapters.typescript_adapter import JavaScriptAdapter, TypeScriptAdapter

ADAPTER_REGISTRY: dict[str, type[LanguageAdapter]] = {
    AdapterName.PYTHON: PythonAdapter,
    AdapterName.JAVASCRIPT: JavaScriptAdapter,
    AdapterName.TYPESCRIPT: TypeScriptAdapter,
    AdapterName.CSHARP: CSharpAdapter,
    AdapterName.FSHARP: FSharpAdapter,
    AdapterName.GO: GoAdapter,
    AdapterName.JAVA: JavaAdapter,
    AdapterName.PHP: PHPAdapter,
    AdapterName.RUST: RustAdapter,
}


def get_adapter(language: str) -> LanguageAdapter:
    """Get an adapter instance for the given language."""
    cls = ADAPTER_REGISTRY.get(language)
    if cls is None:
        raise ValueError(f"No adapter for language: {language}")
    return cls()


def get_all_adapters() -> dict[str, LanguageAdapter]:
    """Get instances of all registered adapters."""
    return {lang: cls() for lang, cls in ADAPTER_REGISTRY.items()}
