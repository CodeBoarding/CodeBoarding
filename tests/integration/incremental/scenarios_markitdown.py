"""Change scenarios for markitdown incremental analysis benchmarking.

These scenarios involve bigger semantic changes than the deepface scenarios:
new converter implementations, architectural modifications, and cross-module
refactoring.
"""

from tests.integration.incremental.scenarios import ChangeScenario, FileEdit


# ---------------------------------------------------------------------------
# Scenario 1: Add a complete new converter (JSON to Markdown)
# ---------------------------------------------------------------------------
_JSON_CONVERTER_CONTENT = '''\
"""Converts JSON files to well-structured Markdown documents.

Handles nested objects, arrays, and mixed types by recursively building
a Markdown representation with appropriate headers and formatting.
"""

import sys
import json
import io
from typing import BinaryIO, Any
from charset_normalizer import from_bytes
from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo

ACCEPTED_MIME_TYPE_PREFIXES = [
    "application/json",
    "text/json",
]
ACCEPTED_FILE_EXTENSIONS = [".json", ".jsonl"]

MAX_DEPTH = 6
MAX_ARRAY_ITEMS_INLINE = 5
MAX_STRING_PREVIEW = 200


class JsonConverter(DocumentConverter):
    """Converts JSON files to structured Markdown with nested headers and tables."""

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()
        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True
        for prefix in ACCEPTED_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True
        return False

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        if stream_info.charset:
            content = file_stream.read().decode(stream_info.charset)
        else:
            content = str(from_bytes(file_stream.read()).best())

        extension = (stream_info.extension or "").lower()

        if extension == ".jsonl":
            return self._convert_jsonl(content)

        data = json.loads(content)
        markdown = self._render_value(data, depth=0, key=None)
        return DocumentConverterResult(markdown=markdown.strip())

    def _convert_jsonl(self, content: str) -> DocumentConverterResult:
        """Convert JSON Lines format - each line is a separate JSON object."""
        lines = content.strip().splitlines()
        parts = ["# JSON Lines Document\\n"]
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                parts.append(f"## Record {i + 1}\\n")
                parts.append(self._render_value(obj, depth=2, key=None))
            except json.JSONDecodeError:
                parts.append(f"## Record {i + 1} (parse error)\\n")
                parts.append(f"```\\n{line}\\n```\\n")
        return DocumentConverterResult(markdown="\\n".join(parts).strip())

    def _render_value(self, value: Any, depth: int, key: str | None) -> str:
        """Recursively render a JSON value as Markdown."""
        if depth > MAX_DEPTH:
            return f"`{json.dumps(value, default=str)[:MAX_STRING_PREVIEW]}`\\n"

        if isinstance(value, dict):
            return self._render_object(value, depth, key)
        elif isinstance(value, list):
            return self._render_array(value, depth, key)
        elif isinstance(value, str):
            if len(value) > MAX_STRING_PREVIEW:
                return f"{value[:MAX_STRING_PREVIEW]}...\\n"
            return f"{value}\\n"
        elif isinstance(value, bool):
            return f"`{str(value).lower()}`\\n"
        elif value is None:
            return "`null`\\n"
        else:
            return f"`{value}`\\n"

    def _render_object(self, obj: dict, depth: int, parent_key: str | None) -> str:
        """Render a JSON object as Markdown with headers for nested objects."""
        parts = []
        # Try to render flat objects as a key-value table
        if all(not isinstance(v, (dict, list)) for v in obj.values()):
            return self._render_flat_object_as_table(obj)

        for key, value in obj.items():
            header_prefix = "#" * min(depth + 1, 6)
            if isinstance(value, (dict, list)):
                parts.append(f"{header_prefix} {key}\\n")
                parts.append(self._render_value(value, depth + 1, key))
            else:
                rendered = self._render_value(value, depth + 1, key)
                parts.append(f"**{key}**: {rendered}")
        return "\\n".join(parts)

    def _render_flat_object_as_table(self, obj: dict) -> str:
        """Render a flat object (no nested values) as a Markdown table."""
        if not obj:
            return "(empty object)\\n"
        parts = ["| Key | Value |", "| --- | --- |"]
        for key, value in obj.items():
            val_str = str(value) if value is not None else "`null`"
            parts.append(f"| {key} | {val_str} |")
        return "\\n".join(parts) + "\\n"

    def _render_array(self, arr: list, depth: int, parent_key: str | None) -> str:
        """Render a JSON array as Markdown."""
        if not arr:
            return "(empty array)\\n"

        # If all items are simple scalars, render inline
        if all(isinstance(item, (str, int, float, bool, type(None))) for item in arr):
            if len(arr) <= MAX_ARRAY_ITEMS_INLINE:
                items = ", ".join(f"`{item}`" for item in arr)
                return f"[{items}]\\n"

        # If all items are flat objects with the same keys, render as a table
        if all(isinstance(item, dict) for item in arr):
            keys = set()
            for item in arr:
                keys.update(item.keys())
            if all(not isinstance(v, (dict, list)) for item in arr for v in item.values()):
                return self._render_object_array_as_table(arr, sorted(keys))

        # Fall back to numbered list
        parts = []
        for i, item in enumerate(arr):
            parts.append(f"{i + 1}. {self._render_value(item, depth + 1, None).strip()}")
        return "\\n".join(parts) + "\\n"

    def _render_object_array_as_table(self, arr: list[dict], columns: list[str]) -> str:
        """Render an array of flat objects as a Markdown table."""
        parts = ["| " + " | ".join(columns) + " |"]
        parts.append("| " + " | ".join(["---"] * len(columns)) + " |")
        for item in arr:
            row = [str(item.get(col, "")) for col in columns]
            parts.append("| " + " | ".join(row) + " |")
        return "\\n".join(parts) + "\\n"
'''


def _register_json_converter(content: str) -> str:
    """Register JsonConverter in the converters __init__.py."""
    old = "from ._csv_converter import CsvConverter"
    new = "from ._csv_converter import CsvConverter\nfrom ._json_converter import JsonConverter"
    content = content.replace(old, new)
    old2 = '    "CsvConverter",\n]'
    new2 = '    "CsvConverter",\n    "JsonConverter",\n]'
    return content.replace(old2, new2)


def _register_json_in_markitdown(content: str) -> str:
    """Import and register JsonConverter in _markitdown.py."""
    old = "    CsvConverter,\n)"
    new = "    CsvConverter,\n    JsonConverter,\n)"
    content = content.replace(old, new)
    old2 = "            self.register_converter(CsvConverter())"
    new2 = "            self.register_converter(CsvConverter())\n            self.register_converter(JsonConverter())"
    return content.replace(old2, new2)


ADD_JSON_CONVERTER = ChangeScenario(
    name="add_json_converter",
    description="Add a complete JSON-to-Markdown converter with table rendering and JSONL support",
    edits=[
        FileEdit(
            file_path="packages/markitdown/src/markitdown/converters/_json_converter.py",
            action="create",
            new_content=_JSON_CONVERTER_CONTENT,
        ),
        FileEdit(
            file_path="packages/markitdown/src/markitdown/converters/__init__.py",
            action="modify",
            content_fn=_register_json_converter,
        ),
        FileEdit(
            file_path="packages/markitdown/src/markitdown/_markitdown.py",
            action="modify",
            content_fn=_register_json_in_markitdown,
        ),
    ],
    commit_message="feat: add JSON/JSONL to Markdown converter with table and nested object support",
)


# ---------------------------------------------------------------------------
# Scenario 2: Add conversion statistics and metadata tracking
# ---------------------------------------------------------------------------
def _add_conversion_stats_to_result(content: str) -> str:
    """Extend DocumentConverterResult with conversion metadata."""
    old = """class DocumentConverterResult:
    \"\"\"The result of converting a document to Markdown.\"\"\"

    def __init__(
        self,
        markdown: str,
        *,
        title: Optional[str] = None,
    ):
        \"\"\"
        Initialize the DocumentConverterResult.

        The only required parameter is the converted Markdown text.
        The title, and any other metadata that may be added in the future, are optional.

        Parameters:
        - markdown: The converted Markdown text.
        - title: Optional title of the document.
        \"\"\"
        self.markdown = markdown
        self.title = title"""
    new = """class ConversionMetadata:
    \"\"\"Metadata collected during the conversion process.\"\"\"

    def __init__(self):
        self.converter_name: str = ""
        self.input_size_bytes: int = 0
        self.output_size_chars: int = 0
        self.warnings: list[str] = []
        self.sections_count: int = 0
        self.tables_count: int = 0
        self.images_count: int = 0

    @property
    def compression_ratio(self) -> float:
        if self.input_size_bytes == 0:
            return 0.0
        return self.output_size_chars / self.input_size_bytes

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def to_dict(self) -> dict:
        return {
            "converter_name": self.converter_name,
            "input_size_bytes": self.input_size_bytes,
            "output_size_chars": self.output_size_chars,
            "compression_ratio": round(self.compression_ratio, 3),
            "warnings": self.warnings,
            "sections_count": self.sections_count,
            "tables_count": self.tables_count,
            "images_count": self.images_count,
        }


class DocumentConverterResult:
    \"\"\"The result of converting a document to Markdown.\"\"\"

    def __init__(
        self,
        markdown: str,
        *,
        title: Optional[str] = None,
        metadata: Optional["ConversionMetadata"] = None,
    ):
        \"\"\"
        Initialize the DocumentConverterResult.

        The only required parameter is the converted Markdown text.
        The title, and any other metadata that may be added in the future, are optional.

        Parameters:
        - markdown: The converted Markdown text.
        - title: Optional title of the document.
        - metadata: Optional conversion metadata with statistics.
        \"\"\"
        self.markdown = markdown
        self.title = title
        self.metadata = metadata or ConversionMetadata()
        self.metadata.output_size_chars = len(markdown)
        self.metadata.sections_count = markdown.count("\\n#")
        self.metadata.tables_count = markdown.count("\\n|")
        self.metadata.images_count = markdown.count("![")"""
    return content.replace(old, new)


ADD_CONVERSION_STATS = ChangeScenario(
    name="add_conversion_stats",
    description="Add ConversionMetadata class to track conversion statistics in results",
    edits=[
        FileEdit(
            file_path="packages/markitdown/src/markitdown/_base_converter.py",
            action="modify",
            content_fn=_add_conversion_stats_to_result,
        ),
    ],
    commit_message="feat: add ConversionMetadata for tracking conversion statistics",
)


# ---------------------------------------------------------------------------
# Scenario 3: Add conversion pipeline with pre/post processing hooks
# ---------------------------------------------------------------------------
_PIPELINE_MODULE_CONTENT = '''\
"""Conversion pipeline with pre-processing and post-processing hooks.

Allows users to register transformation steps that run before and after
the main converter, enabling features like content filtering, header
normalization, link rewriting, and output sanitization.
"""

from typing import Callable, Any, BinaryIO, Optional
from dataclasses import dataclass, field
from .._base_converter import DocumentConverterResult
from .._stream_info import StreamInfo


@dataclass
class PipelineStep:
    """A single step in the conversion pipeline."""

    name: str
    fn: Callable[[str], str]
    enabled: bool = True
    order: int = 0


class ConversionPipeline:
    """Manages pre and post processing steps for document conversion."""

    def __init__(self):
        self._pre_steps: list[PipelineStep] = []
        self._post_steps: list[PipelineStep] = []

    def add_pre_step(self, name: str, fn: Callable[[str], str], order: int = 0) -> None:
        """Add a pre-processing step that transforms raw input before conversion."""
        self._pre_steps.append(PipelineStep(name=name, fn=fn, order=order))
        self._pre_steps.sort(key=lambda s: s.order)

    def add_post_step(self, name: str, fn: Callable[[str], str], order: int = 0) -> None:
        """Add a post-processing step that transforms markdown output after conversion."""
        self._post_steps.append(PipelineStep(name=name, fn=fn, order=order))
        self._post_steps.sort(key=lambda s: s.order)

    def run_pre_steps(self, content: str) -> str:
        """Apply all enabled pre-processing steps in order."""
        for step in self._pre_steps:
            if step.enabled:
                content = step.fn(content)
        return content

    def run_post_steps(self, markdown: str) -> str:
        """Apply all enabled post-processing steps in order."""
        for step in self._post_steps:
            if step.enabled:
                markdown = step.fn(markdown)
        return markdown

    def disable_step(self, name: str) -> bool:
        """Disable a step by name. Returns True if found."""
        for step in self._pre_steps + self._post_steps:
            if step.name == name:
                step.enabled = False
                return True
        return False

    def enable_step(self, name: str) -> bool:
        """Enable a step by name. Returns True if found."""
        for step in self._pre_steps + self._post_steps:
            if step.name == name:
                step.enabled = True
                return True
        return False

    @property
    def step_names(self) -> list[str]:
        """Return names of all registered steps."""
        return [s.name for s in self._pre_steps + self._post_steps]


# Built-in post-processing steps
def normalize_headers(markdown: str) -> str:
    """Ensure consistent header formatting (space after #)."""
    import re
    return re.sub(r"^(#{1,6})([^ #\\n])", r"\\1 \\2", markdown, flags=re.MULTILINE)


def strip_excessive_newlines(markdown: str) -> str:
    """Collapse 3+ consecutive newlines to 2."""
    import re
    return re.sub(r"\\n{3,}", "\\n\\n", markdown)


def normalize_link_references(markdown: str) -> str:
    """Convert inline links to reference-style links for readability."""
    import re
    refs: list[tuple[str, str]] = []
    counter = [0]

    def replacer(match):
        text, url = match.group(1), match.group(2)
        counter[0] += 1
        ref_id = counter[0]
        refs.append((str(ref_id), url))
        return f"[{text}][{ref_id}]"

    result = re.sub(r"\\[([^\\]]+)\\]\\(([^)]+)\\)", replacer, markdown)
    if refs:
        result += "\\n\\n"
        for ref_id, url in refs:
            result += f"[{ref_id}]: {url}\\n"
    return result


DEFAULT_POST_STEPS = [
    ("normalize_headers", normalize_headers, 0),
    ("strip_excessive_newlines", strip_excessive_newlines, 10),
]
'''


def _integrate_pipeline_in_markitdown(content: str) -> str:
    """Add pipeline integration to the MarkItDown class."""
    old = "from ._exceptions import ("
    new = "from .converter_utils.pipeline import ConversionPipeline\nfrom ._exceptions import ("
    content = content.replace(old, new)
    old2 = "        self._converters: List[ConverterRegistration] = []"
    new2 = (
        "        self._converters: List[ConverterRegistration] = []\n"
        "        self._pipeline: Optional[ConversionPipeline] = None"
    )
    return content.replace(old2, new2)


ADD_CONVERSION_PIPELINE = ChangeScenario(
    name="add_conversion_pipeline",
    description="Add pre/post-processing pipeline with header normalization and link rewriting",
    edits=[
        FileEdit(
            file_path="packages/markitdown/src/markitdown/converter_utils/pipeline.py",
            action="create",
            new_content=_PIPELINE_MODULE_CONTENT,
        ),
        FileEdit(
            file_path="packages/markitdown/src/markitdown/_markitdown.py",
            action="modify",
            content_fn=_integrate_pipeline_in_markitdown,
        ),
    ],
    commit_message="feat: add conversion pipeline with pre/post processing hooks",
)


# ---------------------------------------------------------------------------
# Scenario 4: Enhance CSV converter with advanced table features
# ---------------------------------------------------------------------------
def _enhance_csv_converter(content: str) -> str:
    """Add column type detection, alignment, and summary statistics to CSV converter."""
    old = """        result = "\\n".join(markdown_table)

        return DocumentConverterResult(markdown=result)"""
    new = """        result = "\\n".join(markdown_table)

        # Add summary statistics if the table has numeric columns
        summary = self._compute_summary(rows)
        if summary:
            result += "\\n\\n### Summary Statistics\\n\\n"
            result += summary

        return DocumentConverterResult(markdown=result)

    def _compute_summary(self, rows: list[list[str]]) -> str:
        \"\"\"Compute summary statistics for numeric columns.\"\"\"
        if len(rows) < 3:  # Need header + at least 2 data rows
            return ""

        headers = rows[0]
        data_rows = rows[1:]
        numeric_cols: dict[int, list[float]] = {}

        for col_idx, header in enumerate(headers):
            values = []
            for row in data_rows:
                if col_idx < len(row):
                    try:
                        values.append(float(row[col_idx].replace(",", "")))
                    except (ValueError, AttributeError):
                        break
            else:
                if values:
                    numeric_cols[col_idx] = values

        if not numeric_cols:
            return ""

        parts = ["| Column | Min | Max | Mean | Count |"]
        parts.append("| --- | --- | --- | --- | --- |")
        for col_idx, values in numeric_cols.items():
            col_name = headers[col_idx] if col_idx < len(headers) else f"Col {col_idx}"
            min_val = min(values)
            max_val = max(values)
            mean_val = sum(values) / len(values)
            parts.append(
                f"| {col_name} | {min_val:.2f} | {max_val:.2f} | {mean_val:.2f} | {len(values)} |"
            )
        return "\\n".join(parts)"""
    return content.replace(old, new)


ENHANCE_CSV_CONVERTER = ChangeScenario(
    name="enhance_csv_converter",
    description="Add summary statistics computation to the CSV converter",
    edits=[
        FileEdit(
            file_path="packages/markitdown/src/markitdown/converters/_csv_converter.py",
            action="modify",
            content_fn=_enhance_csv_converter,
        ),
    ],
    commit_message="feat: add summary statistics to CSV converter output",
)


# ---------------------------------------------------------------------------
# Scenario 5: Add error recovery with partial conversion support
# ---------------------------------------------------------------------------
def _add_partial_conversion_exception(content: str) -> str:
    """Add PartialConversionResult to exceptions module."""
    return (
        content
        + '''

class PartialConversionResult:
    """Represents a partially successful conversion where some content was recovered.

    This is used when a converter encounters errors mid-conversion but has
    already successfully converted some portion of the document.
    """

    def __init__(
        self,
        recovered_markdown: str,
        total_sections: int,
        converted_sections: int,
        errors: list[str],
    ):
        self.recovered_markdown = recovered_markdown
        self.total_sections = total_sections
        self.converted_sections = converted_sections
        self.errors = errors

    @property
    def success_rate(self) -> float:
        if self.total_sections == 0:
            return 0.0
        return self.converted_sections / self.total_sections

    @property
    def is_usable(self) -> bool:
        """A partial result is considered usable if at least 50% was converted."""
        return self.success_rate >= 0.5

    def to_markdown_with_warnings(self) -> str:
        """Return the markdown with embedded conversion warnings."""
        warnings = "\\n".join(f"> **Warning**: {e}" for e in self.errors)
        return f"{warnings}\\n\\n---\\n\\n{self.recovered_markdown}"


class PartialConversionException(MarkItDownException):
    """Raised when a conversion partially succeeds.

    The partial_result attribute contains whatever content was successfully
    converted, along with error details for the failed sections.
    """

    def __init__(self, message: str, partial_result: PartialConversionResult):
        super().__init__(message)
        self.partial_result = partial_result
'''
    )


def _handle_partial_in_markitdown(content: str) -> str:
    """Import the new exception types in _markitdown.py."""
    old = "from ._exceptions import (\n    FileConversionException,\n    UnsupportedFormatException,\n    FailedConversionAttempt,\n)"
    new = "from ._exceptions import (\n    FileConversionException,\n    UnsupportedFormatException,\n    FailedConversionAttempt,\n    PartialConversionException,\n    PartialConversionResult,\n)"
    return content.replace(old, new)


ADD_ERROR_RECOVERY = ChangeScenario(
    name="add_error_recovery",
    description="Add partial conversion support with PartialConversionResult and recovery logic",
    edits=[
        FileEdit(
            file_path="packages/markitdown/src/markitdown/_exceptions.py",
            action="modify",
            content_fn=_add_partial_conversion_exception,
        ),
        FileEdit(
            file_path="packages/markitdown/src/markitdown/_markitdown.py",
            action="modify",
            content_fn=_handle_partial_in_markitdown,
        ),
    ],
    commit_message="feat: add partial conversion support for error recovery",
)


# ---------------------------------------------------------------------------
# Scenario 6: Modify core MarkItDown.convert_stream with retry logic
# ---------------------------------------------------------------------------
def _add_retry_to_markitdown(content: str) -> str:
    """Add retry logic and timeout handling to the convert flow."""
    old = "import traceback"
    new = "import traceback\nimport time"
    content = content.replace(old, new)
    old2 = "        self._magika = magika.Magika()"
    new2 = (
        "        self._magika = magika.Magika()\n"
        "        self._max_retries: int = 2\n"
        "        self._retry_delay_seconds: float = 0.5\n"
        "        self._conversion_timeout_seconds: float = 300.0"
    )
    return content.replace(old2, new2)


MODIFY_CORE_CONVERT = ChangeScenario(
    name="modify_core_convert",
    description="Add retry logic and timeout configuration to MarkItDown core",
    edits=[
        FileEdit(
            file_path="packages/markitdown/src/markitdown/_markitdown.py",
            action="modify",
            content_fn=_add_retry_to_markitdown,
        ),
    ],
    commit_message="feat: add retry and timeout configuration to MarkItDown",
)


# ---------------------------------------------------------------------------
# Scenario 7: Cross-module refactor - extract StreamInfo enhancement
# ---------------------------------------------------------------------------
def _enhance_stream_info(content: str) -> str:
    """Add content fingerprinting and format detection to StreamInfo."""
    old = "from ._stream_info import StreamInfo"
    new = "from ._stream_info import StreamInfo, ContentFingerprint"
    return content.replace(old, new, 1)


def _add_content_fingerprint_class(content: str) -> str:
    """Add ContentFingerprint to _stream_info.py."""
    return (
        content
        + '''


class ContentFingerprint:
    """Lightweight fingerprint of file content for caching and deduplication.

    Computes a hash-based fingerprint from the first and last N bytes
    of a file without reading the entire content into memory.
    """

    def __init__(self, file_stream, sample_size: int = 4096):
        import hashlib
        hasher = hashlib.sha256()

        pos = file_stream.tell()
        head = file_stream.read(sample_size)
        hasher.update(head)
        self.size = len(head)

        file_stream.seek(0, 2)  # seek to end
        total_size = file_stream.tell()
        self.total_size = total_size

        if total_size > sample_size * 2:
            file_stream.seek(-sample_size, 2)
            tail = file_stream.read(sample_size)
            hasher.update(tail)
            self.size += len(tail)

        file_stream.seek(pos)  # restore position
        self.hash = hasher.hexdigest()

    def __eq__(self, other):
        if not isinstance(other, ContentFingerprint):
            return False
        return self.hash == other.hash and self.total_size == other.total_size

    def __hash__(self):
        return hash((self.hash, self.total_size))

    def __repr__(self):
        return f"ContentFingerprint(hash={self.hash[:12]}..., size={self.total_size})"
'''
    )


CROSS_MODULE_REFACTOR = ChangeScenario(
    name="cross_module_refactor",
    description="Add ContentFingerprint to StreamInfo and integrate in MarkItDown",
    edits=[
        FileEdit(
            file_path="packages/markitdown/src/markitdown/_stream_info.py",
            action="modify",
            content_fn=_add_content_fingerprint_class,
        ),
        FileEdit(
            file_path="packages/markitdown/src/markitdown/_markitdown.py",
            action="modify",
            content_fn=_enhance_stream_info,
        ),
    ],
    commit_message="feat: add ContentFingerprint for conversion caching and deduplication",
)


# ---------------------------------------------------------------------------
# Scenario 8: Delete deprecated converter method and clean up
# ---------------------------------------------------------------------------
def _remove_text_content_property(content: str) -> str:
    """Remove the deprecated text_content property from DocumentConverterResult."""
    old = """    @property
    def text_content(self) -> str:
        \"\"\"Soft-deprecated alias for `markdown`. New code should migrate to using `markdown` or __str__.\"\"\"
        return self.markdown

    @text_content.setter
    def text_content(self, markdown: str):
        \"\"\"Soft-deprecated alias for `markdown`. New code should migrate to using `markdown` or __str__.\"\"\"
        self.markdown = markdown

    def __str__(self) -> str:"""
    new = """    def __str__(self) -> str:"""
    return content.replace(old, new)


REMOVE_DEPRECATED_API = ChangeScenario(
    name="remove_deprecated_api",
    description="Remove deprecated text_content property from DocumentConverterResult",
    edits=[
        FileEdit(
            file_path="packages/markitdown/src/markitdown/_base_converter.py",
            action="modify",
            content_fn=_remove_text_content_property,
        ),
    ],
    commit_message="refactor: remove deprecated text_content property",
)


# ---------------------------------------------------------------------------
# All markitdown scenarios
# ---------------------------------------------------------------------------
MARKITDOWN_SCENARIOS: list[ChangeScenario] = [
    ADD_JSON_CONVERTER,
    ADD_CONVERSION_STATS,
    ADD_CONVERSION_PIPELINE,
    ENHANCE_CSV_CONVERTER,
    ADD_ERROR_RECOVERY,
    MODIFY_CORE_CONVERT,
    CROSS_MODULE_REFACTOR,
    REMOVE_DEPRECATED_API,
]

MARKITDOWN_SCENARIOS_BY_NAME: dict[str, ChangeScenario] = {s.name: s for s in MARKITDOWN_SCENARIOS}
