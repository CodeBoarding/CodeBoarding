"""The static-analysis tag moves with the engine sources that decide what ``static_analysis.pkl`` holds.

Why: a warm start re-analyses only the files whose hash moved since the pkl, so an engine change
that ships without a tag bump leaves every unchanged file with the old engine's graph, unnoticed.
"""

import ast
import hashlib
from pathlib import Path

from static_analyzer import analysis_cache

ENGINE = Path(analysis_cache.__file__).parent
# The pkl has not carried clustering since v7; progress lines, memory readings and exception types
# cannot change a node, an edge or a diagnostic; and ``servers`` holds downloaded language servers
# in an older install, not engine sources.
NOT_ENGINE = [
    ENGINE / "clustering",
    ENGINE / "servers",
    ENGINE / "engine/progress.py",
    ENGINE / "engine/process_memory.py",
    ENGINE / "exceptions.py",
]
# The tag, and the digest of the engine sources it was last bumped (or confirmed) for.
TAGGED = ("v17", "2295dcc325172c31ba7f0c0dc73e38bc6685c99685433c29a7722914587fc4e4")


def _code(path: Path) -> bytes:
    """What a source file runs: a Python module without its comments, layout and docstrings."""
    if path.suffix != ".py":
        return path.read_bytes()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree).encode()


def test_engine_sources_match_tag_version() -> None:
    digest = hashlib.sha256()
    for path in sorted(ENGINE.rglob("*")):
        if path.suffix not in {".py", ".js", ".json"} or any(path.is_relative_to(other) for other in NOT_ENGINE):
            continue
        digest.update(path.relative_to(ENGINE).as_posix().encode() + b"\0" + _code(path) + b"\0")
    assert (analysis_cache._TAG_VERSION, digest.hexdigest()) == TAGGED, (
        "The static-analysis engine sources changed. If the change can alter what static_analysis.pkl holds, "
        "bump _TAG_VERSION in static_analyzer/analysis_cache.py (with a line saying what changed) and put the new "
        "tag and digest in TAGGED here. If it cannot (a log line, a rename with no effect on output), update the "
        "digest alone."
    )
