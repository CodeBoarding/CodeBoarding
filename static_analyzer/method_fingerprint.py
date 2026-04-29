"""Whitespace-normalized fingerprints for method bodies and signatures."""

import hashlib
import re
from pathlib import Path

from static_analyzer.cosmetic_diff import strip_comments_from_source

_WHITESPACE_RE = re.compile(r"\s+")


def fingerprint_source_text(file_path: str, source: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", strip_comments_from_source(file_path, source)).strip()
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()


def fingerprint_method_signature(file_path: str, source: str) -> str | None:
    """Fingerprint the signature/header of a method slice; ``None`` if no safe boundary."""
    ext = Path(file_path).suffix.lower()
    stripped = strip_comments_from_source(file_path, source)
    signature: str | None = None

    if ext == ".py":
        collected: list[str] = []
        for line in stripped.splitlines():
            if not collected and not line.strip():
                continue
            collected.append(line)
            line_text = line.strip()
            if line_text.startswith(("def ", "async def ", "class ")) and line_text.endswith(":"):
                signature = "\n".join(collected)
                break
            if (
                collected
                and line_text.endswith(":")
                and any(token in "\n".join(collected) for token in ("def ", "async def ", "class "))
            ):
                signature = "\n".join(collected)
                break
    else:
        brace_index = stripped.find("{")
        if brace_index != -1:
            signature = stripped[:brace_index]

    if signature is None:
        return None

    normalized = _WHITESPACE_RE.sub(" ", signature).strip()
    if not normalized:
        return None
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()
