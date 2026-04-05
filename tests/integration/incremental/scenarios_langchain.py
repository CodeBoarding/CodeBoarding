"""Change scenarios for incremental analysis benchmarking against langchain.

Each scenario defines a set of deterministic file edits that can be applied
to the langchain repo to exercise different code paths in the incremental
analysis pipeline.

NOTE: This is the held-out TEST SET. Do not use these results for
optimization decisions during the Ralph performance loop.
"""

from tests.integration.incremental.scenarios import ChangeScenario, FileEdit


# ---------------------------------------------------------------------------
# Scenario 1: Cosmetic docstring change (leaf utility)
# ---------------------------------------------------------------------------
def _cosmetic_formatting_edit(content: str) -> str:
    """Reword the StrictFormatter class docstring without changing code."""
    return content.replace(
        "A string formatter that enforces keyword-only argument substitution.",
        "A strict string formatter that requires all arguments to be passed as keyword arguments.",
    )


COSMETIC_DOCSTRING = ChangeScenario(
    name="cosmetic_docstring",
    description="Reword the StrictFormatter docstring in utils/formatting.py",
    edits=[
        FileEdit(
            file_path="libs/core/langchain_core/utils/formatting.py",
            action="modify",
            content_fn=_cosmetic_formatting_edit,
        ),
    ],
    commit_message="docs: reword StrictFormatter docstring for clarity",
    expected_outcome="skip",
)


# ---------------------------------------------------------------------------
# Scenario 2: Add utility function (purely additive)
# ---------------------------------------------------------------------------
_NEW_UTILITY_FUNCTION = '''

def truncate_format_string(format_string: str, max_length: int = 1000) -> str:
    """Truncate a format string while preserving placeholder integrity.

    Useful when logging or displaying long prompt templates. Ensures that
    truncation does not break placeholder syntax by finding the last
    complete placeholder boundary before the limit.

    Args:
        format_string: The format string to truncate.
        max_length: Maximum length of the returned string.

    Returns:
        The truncated format string, with '...' appended if truncated.
    """
    if len(format_string) <= max_length:
        return format_string
    # Find the last closing brace before the limit
    truncated = format_string[:max_length]
    last_close = truncated.rfind("}")
    if last_close > 0:
        truncated = truncated[: last_close + 1]
    return truncated + "..."
'''


def _add_truncate_utility(content: str) -> str:
    return content + _NEW_UTILITY_FUNCTION


ADD_UTILITY_FUNCTION = ChangeScenario(
    name="add_utility_function",
    description="Append truncate_format_string() to utils/formatting.py",
    edits=[
        FileEdit(
            file_path="libs/core/langchain_core/utils/formatting.py",
            action="modify",
            content_fn=_add_truncate_utility,
        ),
    ],
    commit_message="feat: add truncate_format_string utility for safe template truncation",
    expected_outcome="skip",
    expected_additive=True,
)


# ---------------------------------------------------------------------------
# Scenario 3: Modify function logic (single file, localized)
# ---------------------------------------------------------------------------
def _modify_json_parser_logic(content: str) -> str:
    """Add lenient whitespace stripping and nested JSON extraction to parse_result."""
    old = "        text = result[0].text\n        text = text.strip()"
    new = (
        "        text = result[0].text\n"
        "        text = text.strip()\n"
        "        # Strip common wrapper patterns (e.g. markdown fences without lang tag)\n"
        "        if text.startswith('```') and text.endswith('```'):\n"
        "            text = text[3:-3].strip()"
    )
    return content.replace(old, new)


MODIFY_JSON_PARSER = ChangeScenario(
    name="modify_json_parser",
    description="Add extra markdown fence stripping to JsonOutputParser.parse_result()",
    edits=[
        FileEdit(
            file_path="libs/core/langchain_core/output_parsers/json.py",
            action="modify",
            content_fn=_modify_json_parser_logic,
        ),
    ],
    commit_message="fix: strip bare markdown fences in JSON output parser",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 4: Add method to Document class (moderate fan-out)
# ---------------------------------------------------------------------------
def _add_document_method(content: str) -> str:
    """Add a summarize() method to the Document class."""
    old = "    def __str__(self) -> str:"
    new = (
        "    def summarize(self, max_length: int = 200) -> str:\n"
        '        """Return a truncated summary of the document content.\n'
        "\n"
        "        Args:\n"
        "            max_length: Maximum length of the summary string.\n"
        "\n"
        "        Returns:\n"
        "            A truncated version of page_content with metadata source if available.\n"
        '        """\n'
        "        text = self.page_content[:max_length]\n"
        "        if len(self.page_content) > max_length:\n"
        '            text += "..."\n'
        '        source = self.metadata.get("source", "")\n'
        "        if source:\n"
        '            return f"[{source}] {text}"\n'
        "        return text\n"
        "\n"
        "    def __str__(self) -> str:"
    )
    return content.replace(old, new)


ADD_DOCUMENT_METHOD = ChangeScenario(
    name="add_document_method",
    description="Add summarize() method to the Document class in documents/base.py",
    edits=[
        FileEdit(
            file_path="libs/core/langchain_core/documents/base.py",
            action="modify",
            content_fn=_add_document_method,
        ),
    ],
    commit_message="feat: add Document.summarize() for truncated content preview",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 5: Add new file + integration (new module)
# ---------------------------------------------------------------------------
_DOCUMENT_DEDUP_CONTENT = '''\
"""Document deduplication utilities for retrieval pipelines.

Provides content-based and metadata-based deduplication strategies to remove
duplicate documents from retrieval results before they reach the LLM.
"""

import hashlib
from collections.abc import Sequence

from langchain_core.documents import Document


def content_hash(doc: Document) -> str:
    """Compute a SHA-256 hash of the document page_content.

    Args:
        doc: The document to hash.

    Returns:
        A hex digest string.
    """
    return hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()


def deduplicate_by_content(docs: Sequence[Document]) -> list[Document]:
    """Remove duplicate documents based on page_content hash.

    Preserves the order of first occurrence.

    Args:
        docs: A sequence of documents, potentially with duplicates.

    Returns:
        A list of unique documents (by content).
    """
    seen: set[str] = set()
    unique: list[Document] = []
    for doc in docs:
        h = content_hash(doc)
        if h not in seen:
            seen.add(h)
            unique.append(doc)
    return unique


def deduplicate_by_metadata_key(
    docs: Sequence[Document], key: str = "source"
) -> list[Document]:
    """Remove duplicate documents based on a metadata key.

    Preserves the order of first occurrence. Documents missing the key
    are always included.

    Args:
        docs: A sequence of documents.
        key: The metadata key to deduplicate on.

    Returns:
        A list of unique documents (by the given metadata key).
    """
    seen: set[str] = set()
    unique: list[Document] = []
    for doc in docs:
        val = doc.metadata.get(key)
        if val is None:
            unique.append(doc)
        elif val not in seen:
            seen.add(val)
            unique.append(doc)
    return unique
'''


def _import_dedup_in_documents_init(content: str) -> str:
    """Add dedup module to documents __init__ dynamic imports."""
    old = '    "BaseDocumentTransformer": "transformers",'
    new = (
        '    "BaseDocumentTransformer": "transformers",\n'
        '    "content_hash": "dedup",\n'
        '    "deduplicate_by_content": "dedup",'
    )
    return content.replace(old, new)


ADD_NEW_FILE = ChangeScenario(
    name="add_new_file",
    description="Add document dedup module and import it in documents/__init__.py",
    edits=[
        FileEdit(
            file_path="libs/core/langchain_core/documents/dedup.py",
            action="create",
            new_content=_DOCUMENT_DEDUP_CONTENT,
        ),
        FileEdit(
            file_path="libs/core/langchain_core/documents/__init__.py",
            action="modify",
            content_fn=_import_dedup_in_documents_init,
        ),
    ],
    commit_message="feat: add document deduplication utilities for retrieval pipelines",
)


# ---------------------------------------------------------------------------
# Scenario 6: Cross-component change (runnables + callbacks + retrievers)
# ---------------------------------------------------------------------------
def _add_invoke_timing_to_runnable(content: str) -> str:
    """Add a timing_enabled class variable to the Runnable base."""
    old = "class Runnable(ABC, Generic[Input, Output]):"
    new = (
        "class Runnable(ABC, Generic[Input, Output]):\n"
        "    _timing_enabled: bool = False\n"
        '    """When True, emit timing metadata in callbacks for invoke/ainvoke."""'
    )
    # Replace only the class definition line, keeping the rest
    return content.replace(old, new, 1)


def _add_timing_callback_handler(content: str) -> str:
    """Add a timing-related helper function to callbacks/manager.py."""
    # Append at end of file
    return (
        content
        + '''

def _emit_timing_metadata(run_manager: RunManagerMixin, elapsed_ms: float) -> None:
    """Emit timing metadata through the callback system.

    Internal helper used by Runnable when timing is enabled.

    Args:
        run_manager: The active run manager.
        elapsed_ms: Elapsed time in milliseconds.
    """
    if hasattr(run_manager, "metadata"):
        run_manager.metadata["_invoke_elapsed_ms"] = elapsed_ms
'''
    )


def _add_timing_import_in_retrievers(content: str) -> str:
    """Add timing-related import to retrievers module."""
    old = "from langchain_core.runnables.config import run_in_executor"
    new = (
        "from langchain_core.runnables.config import run_in_executor\n"
        "\n"
        "# Timing support for invoke tracing\n"
        "_RETRIEVER_TIMING_ENABLED = False"
    )
    return content.replace(old, new)


CROSS_COMPONENT_CHANGE = ChangeScenario(
    name="cross_component_change",
    description="Add timing instrumentation across runnables, callbacks, and retrievers",
    edits=[
        FileEdit(
            file_path="libs/core/langchain_core/runnables/base.py",
            action="modify",
            content_fn=_add_invoke_timing_to_runnable,
        ),
        FileEdit(
            file_path="libs/core/langchain_core/callbacks/manager.py",
            action="modify",
            content_fn=_add_timing_callback_handler,
        ),
        FileEdit(
            file_path="libs/core/langchain_core/retrievers.py",
            action="modify",
            content_fn=_add_timing_import_in_retrievers,
        ),
    ],
    commit_message="feat: add timing instrumentation across runnables, callbacks, and retrievers",
)


# ---------------------------------------------------------------------------
# Scenario 7: Modify cross-module type (AIMessage + chat_models)
# ---------------------------------------------------------------------------
def _add_ai_message_field(content: str) -> str:
    """Add a reasoning_content field to AIMessage."""
    old = '    usage_metadata: UsageMetadata | None = None\n    """If present, usage metadata for a message, such as token counts.'
    new = (
        "    reasoning_content: str | None = None\n"
        '    """If present, the chain-of-thought reasoning produced by the model."""\n'
        "\n"
        "    usage_metadata: UsageMetadata | None = None\n"
        '    """If present, usage metadata for a message, such as token counts.'
    )
    return content.replace(old, new)


def _reference_reasoning_in_chat_models(content: str) -> str:
    """Add a comment referencing reasoning_content in chat_models.py."""
    old = "from langchain_core.messages import (\n    AIMessage,"
    new = (
        "# AIMessage now supports reasoning_content field for CoT models\n"
        "from langchain_core.messages import (\n    AIMessage,"
    )
    return content.replace(old, new)


MODIFY_CROSS_MODULE_TYPE = ChangeScenario(
    name="modify_cross_module_type",
    description="Add reasoning_content field to AIMessage and reference in chat_models",
    edits=[
        FileEdit(
            file_path="libs/core/langchain_core/messages/ai.py",
            action="modify",
            content_fn=_add_ai_message_field,
        ),
        FileEdit(
            file_path="libs/core/langchain_core/language_models/chat_models.py",
            action="modify",
            content_fn=_reference_reasoning_in_chat_models,
        ),
    ],
    commit_message="feat: add reasoning_content field to AIMessage for CoT model support",
)


# ---------------------------------------------------------------------------
# Scenario 8: Delete a function
# ---------------------------------------------------------------------------
def _delete_validate_input_variables(content: str) -> str:
    """Remove the validate_input_variables method from StrictFormatter."""
    start = "\n    def validate_input_variables("
    end = "        super().format(format_string, **dummy_inputs)\n"
    idx_start = content.index(start)
    idx_end = content.index(end) + len(end)
    return content[:idx_start] + content[idx_end:]


DELETE_FUNCTION = ChangeScenario(
    name="delete_function",
    description="Remove validate_input_variables() from StrictFormatter",
    edits=[
        FileEdit(
            file_path="libs/core/langchain_core/utils/formatting.py",
            action="modify",
            content_fn=_delete_validate_input_variables,
        ),
    ],
    commit_message="refactor: remove unused validate_input_variables from StrictFormatter",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# All scenarios, ordered by expected complexity
# ---------------------------------------------------------------------------
LANGCHAIN_SCENARIOS: list[ChangeScenario] = [
    COSMETIC_DOCSTRING,
    ADD_UTILITY_FUNCTION,
    MODIFY_JSON_PARSER,
    ADD_DOCUMENT_METHOD,
    ADD_NEW_FILE,
    CROSS_COMPONENT_CHANGE,
    MODIFY_CROSS_MODULE_TYPE,
    DELETE_FUNCTION,
]

LANGCHAIN_SCENARIOS_BY_NAME: dict[str, ChangeScenario] = {s.name: s for s in LANGCHAIN_SCENARIOS}
