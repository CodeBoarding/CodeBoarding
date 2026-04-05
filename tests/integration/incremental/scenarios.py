"""Change scenarios for incremental analysis benchmarking.

Each scenario defines a set of deterministic file edits that can be applied
to a target repo (deepface) to exercise different code paths in the
incremental analysis pipeline.
"""

from dataclasses import dataclass, field
from typing import Callable, Literal


@dataclass(frozen=True)
class FileEdit:
    """A single file edit operation applied to the target repo."""

    file_path: str  # repo-relative path
    action: Literal["modify", "create", "delete"]
    content_fn: Callable[[str], str] | None = None  # old_content -> new_content (for modify)
    new_content: str | None = None  # full content (for create)


@dataclass(frozen=True)
class ChangeScenario:
    """A deterministic change scenario for incremental analysis benchmarking."""

    name: str
    description: str
    edits: list[FileEdit]
    commit_message: str
    expected_outcome: str | None = None  # "no_change", "skip", "patch", "reexpand", "full"
    expected_additive: bool = False


# ---------------------------------------------------------------------------
# Scenario 1: Cosmetic docstring change
# ---------------------------------------------------------------------------
def _cosmetic_docstring_edit(content: str) -> str:
    """Reword a docstring without changing any code logic."""
    return content.replace(
        "Verify if an image pair represents the same person or different persons.",
        "Determine whether two facial images belong to the same individual or to distinct individuals.",
    )


COSMETIC_DOCSTRING = ChangeScenario(
    name="cosmetic_docstring",
    description="Reword a docstring in verification.py without changing code logic",
    edits=[
        FileEdit(
            file_path="deepface/modules/verification.py",
            action="modify",
            content_fn=_cosmetic_docstring_edit,
        ),
    ],
    commit_message="docs: reword verify() docstring for clarity",
    expected_outcome="skip",
)


# ---------------------------------------------------------------------------
# Scenario 2: Add utility function (purely additive)
# ---------------------------------------------------------------------------
_NEW_UTILITY_FUNCTION = '''

def compute_image_hash_from_pixels(img_array) -> str:
    """
    Compute a perceptual hash from raw pixel data.

    This is useful for deduplication when the same image may be loaded
    from different file paths or formats.

    Args:
        img_array: numpy array in BGR format.

    Returns:
        str: hex digest of the perceptual hash.
    """
    import hashlib

    if img_array is None:
        return ""
    downsampled = img_array[::8, ::8]
    raw_bytes = downsampled.tobytes()
    return hashlib.sha256(raw_bytes).hexdigest()
'''


def _add_utility_function(content: str) -> str:
    return content + _NEW_UTILITY_FUNCTION


ADD_UTILITY_FUNCTION = ChangeScenario(
    name="add_utility_function",
    description="Append a new utility function to image_utils.py",
    edits=[
        FileEdit(
            file_path="deepface/commons/image_utils.py",
            action="modify",
            content_fn=_add_utility_function,
        ),
    ],
    commit_message="feat: add perceptual hash utility for image deduplication",
    expected_outcome="skip",
    expected_additive=True,
)


# ---------------------------------------------------------------------------
# Scenario 3: Modify function logic (single file, localized)
# ---------------------------------------------------------------------------
def _modify_analyze_logic(content: str) -> str:
    """Add a minimum-confidence filter to the analyze loop."""
    old = "        if img_content.shape[0] == 0 or img_content.shape[1] == 0:\n            continue"
    new = (
        "        if img_content.shape[0] == 0 or img_content.shape[1] == 0:\n"
        "            continue\n"
        "\n"
        "        # Skip low-confidence detections for more reliable analysis\n"
        "        if img_confidence < 0.15:\n"
        "            continue"
    )
    return content.replace(old, new)


MODIFY_FUNCTION_LOGIC = ChangeScenario(
    name="modify_function_logic",
    description="Add a minimum-confidence filter inside demography.analyze()",
    edits=[
        FileEdit(
            file_path="deepface/modules/demography.py",
            action="modify",
            content_fn=_modify_analyze_logic,
        ),
    ],
    commit_message="feat: skip low-confidence faces in demography analysis",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 4: Add parameter to public function (cross-module)
# ---------------------------------------------------------------------------
def _add_param_to_represent(content: str) -> str:
    """Add a verbose parameter to represent()."""
    old = "    return_face: bool = False,"
    new = "    return_face: bool = False,\n    verbose: bool = False,"
    content = content.replace(old, new)
    # Add logging call using the verbose flag
    old2 = "    batch_images_np = np.concatenate(batch_images, axis=0)"
    new2 = (
        "    if verbose:\n"
        '        logger.info(f"Processing batch of {len(batch_images)} face images")\n'
        "\n"
        "    batch_images_np = np.concatenate(batch_images, axis=0)"
    )
    return content.replace(old2, new2)


def _pass_verbose_in_verification(content: str) -> str:
    """Thread the verbose parameter through verification's call to represent."""
    old = '            detector_backend="skip",'
    new = '            detector_backend="skip",\n            verbose=False,'
    return content.replace(old, new, 1)


ADD_PARAMETER_CROSS_MODULE = ChangeScenario(
    name="add_parameter_cross_module",
    description="Add a verbose parameter to represent() and thread it through verification.py",
    edits=[
        FileEdit(
            file_path="deepface/modules/representation.py",
            action="modify",
            content_fn=_add_param_to_represent,
        ),
        FileEdit(
            file_path="deepface/modules/verification.py",
            action="modify",
            content_fn=_pass_verbose_in_verification,
        ),
    ],
    commit_message="feat: add verbose parameter to represent() for debug logging",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 5: Add new file to existing component
# ---------------------------------------------------------------------------
_SQLITE_BACKEND_CONTENT = '''\
"""SQLite-based vector storage backend for local development and testing."""

import sqlite3
import json
from typing import Any
from pathlib import Path

from deepface.modules.database.types import Database


class SQLiteVectorStore(Database):
    """A lightweight SQLite backend for face embedding storage.

    Suitable for local development and single-machine deployments
    where a full database server is not needed.
    """

    def __init__(self, db_path: str = "deepface_vectors.db"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS embeddings (
                id TEXT PRIMARY KEY,
                img_name TEXT NOT NULL,
                embedding TEXT NOT NULL,
                model_name TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def store_embedding(
        self, identity: str, img_name: str, embedding: list[float], model_name: str
    ) -> None:
        if self._conn is None:
            raise RuntimeError("Not connected. Call connect() first.")
        self._conn.execute(
            "INSERT OR REPLACE INTO embeddings (id, img_name, embedding, model_name) VALUES (?, ?, ?, ?)",
            (identity, img_name, json.dumps(embedding), model_name),
        )
        self._conn.commit()

    def retrieve_embeddings(self, model_name: str) -> list[dict[str, Any]]:
        if self._conn is None:
            raise RuntimeError("Not connected. Call connect() first.")
        cursor = self._conn.execute(
            "SELECT id, img_name, embedding FROM embeddings WHERE model_name = ?",
            (model_name,),
        )
        results = []
        for row in cursor.fetchall():
            results.append(
                {
                    "identity": row[0],
                    "img_name": row[1],
                    "embedding": json.loads(row[2]),
                }
            )
        return results
'''


def _import_sqlite_in_datastore(content: str) -> str:
    """Add import of the SQLite backend to datastore.py."""
    old = "from deepface.modules.database.neo4j import Neo4jClient"
    new = (
        "from deepface.modules.database.neo4j import Neo4jClient\n"
        "from deepface.modules.database.sqlite import SQLiteVectorStore"
    )
    return content.replace(old, new)


ADD_NEW_FILE = ChangeScenario(
    name="add_new_file",
    description="Add SQLite vector store backend and import it in datastore.py",
    edits=[
        FileEdit(
            file_path="deepface/modules/database/sqlite.py",
            action="create",
            new_content=_SQLITE_BACKEND_CONTENT,
        ),
        FileEdit(
            file_path="deepface/modules/datastore.py",
            action="modify",
            content_fn=_import_sqlite_in_datastore,
        ),
    ],
    commit_message="feat: add SQLite vector store backend for local development",
)


# ---------------------------------------------------------------------------
# Scenario 6: Cross-component change (4+ files)
# ---------------------------------------------------------------------------
def _add_output_format_to_detection(content: str) -> str:
    """Add output_format parameter to extract_faces."""
    old = "    max_faces: Optional[int] = None,\n) -> Union[List[Dict[str, Any]], List[List[Dict[str, Any]]]]:"
    new = (
        "    max_faces: Optional[int] = None,\n"
        '    output_format: str = "dict",\n'
        ") -> Union[List[Dict[str, Any]], List[List[Dict[str, Any]]]]:"
    )
    return content.replace(old, new)


def _thread_output_format_in_representation(content: str) -> str:
    """Pass output_format through to detection in represent()."""
    old = "                    max_faces=max_faces,"
    new = '                    max_faces=max_faces,\n                    output_format="dict",'
    return content.replace(old, new, 1)


def _thread_output_format_in_verification(content: str) -> str:
    """Add output_format mention in the verification extraction."""
    old = "            anti_spoofing=anti_spoofing,"
    new = "            anti_spoofing=anti_spoofing,\n            # output_format inherited from detection defaults"
    return content.replace(old, new, 1)


def _add_format_helper_to_image_utils(content: str) -> str:
    """Add a format validation utility."""
    return (
        content
        + '''

VALID_OUTPUT_FORMATS = {"dict", "dataclass", "json"}


def validate_output_format(fmt: str) -> str:
    """Validate and normalize an output format string.

    Args:
        fmt: requested output format.

    Returns:
        str: normalized format name.

    Raises:
        ValueError: if format is not supported.
    """
    fmt = fmt.lower().strip()
    if fmt not in VALID_OUTPUT_FORMATS:
        raise ValueError(f"Unsupported output format '{fmt}'. Choose from {VALID_OUTPUT_FORMATS}")
    return fmt
'''
    )


CROSS_COMPONENT_CHANGE = ChangeScenario(
    name="cross_component_change",
    description="Thread output_format parameter through detection, representation, verification, and image_utils",
    edits=[
        FileEdit(
            file_path="deepface/modules/detection.py",
            action="modify",
            content_fn=_add_output_format_to_detection,
        ),
        FileEdit(
            file_path="deepface/modules/representation.py",
            action="modify",
            content_fn=_thread_output_format_in_representation,
        ),
        FileEdit(
            file_path="deepface/modules/verification.py",
            action="modify",
            content_fn=_thread_output_format_in_verification,
        ),
        FileEdit(
            file_path="deepface/commons/image_utils.py",
            action="modify",
            content_fn=_add_format_helper_to_image_utils,
        ),
    ],
    commit_message="feat: add output_format parameter across detection pipeline",
)


# ---------------------------------------------------------------------------
# Scenario 7: Delete a function
# ---------------------------------------------------------------------------
def _delete_yield_images(content: str) -> str:
    """Remove the yield_images function entirely."""
    start = "\ndef yield_images(path: str) -> Generator[str, None, None]:"
    end = "                        yield exact_path\n"
    idx_start = content.index(start)
    idx_end = content.index(end) + len(end)
    return content[:idx_start] + content[idx_end:]


DELETE_FUNCTION = ChangeScenario(
    name="delete_function",
    description="Remove the yield_images() function from image_utils.py",
    edits=[
        FileEdit(
            file_path="deepface/commons/image_utils.py",
            action="modify",
            content_fn=_delete_yield_images,
        ),
    ],
    commit_message="refactor: remove unused yield_images() function",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 8: Rename function across files
# ---------------------------------------------------------------------------
def _rename_yield_images_in_utils(content: str) -> str:
    """Rename yield_images to iterate_image_paths."""
    return content.replace("def yield_images(", "def iterate_image_paths(")


def _rename_yield_images_in_recognition(content: str) -> str:
    """Update the call site in recognition.py."""
    return content.replace("image_utils.yield_images(", "image_utils.iterate_image_paths(")


RENAME_ACROSS_FILES = ChangeScenario(
    name="rename_across_files",
    description="Rename yield_images() to iterate_image_paths() in image_utils and recognition",
    edits=[
        FileEdit(
            file_path="deepface/commons/image_utils.py",
            action="modify",
            content_fn=_rename_yield_images_in_utils,
        ),
        FileEdit(
            file_path="deepface/modules/recognition.py",
            action="modify",
            content_fn=_rename_yield_images_in_recognition,
        ),
    ],
    commit_message="refactor: rename yield_images to iterate_image_paths",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# All scenarios, ordered by expected complexity
# ---------------------------------------------------------------------------
SCENARIOS: list[ChangeScenario] = [
    COSMETIC_DOCSTRING,
    ADD_UTILITY_FUNCTION,
    MODIFY_FUNCTION_LOGIC,
    ADD_PARAMETER_CROSS_MODULE,
    ADD_NEW_FILE,
    CROSS_COMPONENT_CHANGE,
    DELETE_FUNCTION,
    RENAME_ACROSS_FILES,
]

SCENARIOS_BY_NAME: dict[str, ChangeScenario] = {s.name: s for s in SCENARIOS}
