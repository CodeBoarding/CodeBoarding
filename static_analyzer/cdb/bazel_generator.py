"""Compilation-database generator for Bazel workspaces via ``bazel aquery``.

Shells out to ``bazel aquery`` and reshapes the JSON action graph into a
``compile_commands.json``. Scope: Bazel 6+ (aquery jsonproto), CppCompile
actions only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from static_analyzer.cdb import config
from static_analyzer.cdb.base import (
    CDB_SKIP_DIRS,
    CPP_SOURCE_EXTENSIONS,
    BuildSystemKind,
    CdbGenerator,
    run_build_step,
)
from static_analyzer.cdb.fingerprint import collect_project_sources

logger = logging.getLogger(__name__)

_BAZEL_VERSION_RE = re.compile(r"bazel\s+(\d+)\.(\d+)", re.IGNORECASE)
_MIN_BAZEL_MAJOR = 6
_SOURCE_SUFFIXES = (".cpp", ".cc", ".cxx", ".c++", ".c", ".m", ".mm")
_BAZEL_ROOT_INPUTS = (
    "MODULE.bazel",
    "MODULE.bazel.lock",
    "WORKSPACE",
    "WORKSPACE.bazel",
    "WORKSPACE.bzlmod",
    ".bazelrc",
)
_BAZEL_BUILD_FILES = {"BUILD", "BUILD.bazel"}
_BAZEL_SKIP_DIRS = set(CDB_SKIP_DIRS)


class BazelAqueryGenerator(CdbGenerator):
    """Shell out to ``bazel aquery`` and reshape the output as a CDB."""

    @property
    def kind(self) -> BuildSystemKind:
        return BuildSystemKind.BAZEL

    def _build_entries(self, project_root: Path) -> list[dict]:
        self._require_bazel()
        exec_root = self._bazel_info(project_root, "execution_root")
        aquery_json = self._run_aquery(project_root)
        entries = self._actions_to_cdb(aquery_json, exec_root)
        if not entries:
            raise RuntimeError(
                "bazel aquery returned no CppCompile actions. "
                f"Check that {config.ENV_BAZEL_QUERY!s} ({config.bazel_query_scope()!r}) matches "
                "targets that actually compile C++ (try narrowing to a known package, e.g. //pkg/...)."
            )
        logger.info("Collected %d CppCompile entries from bazel aquery", len(entries))
        return entries

    # --- internals ----------------------------------------------------

    @staticmethod
    def _fingerprint_inputs(project_root: Path) -> list[Path]:
        """Workspace markers + BUILD/.bzl files + C/C++ sources.

        Why: ``cc_library`` rules typically use ``glob()`` so adding a
        source file changes the build graph without editing any BUILD
        file — the fingerprint must include sources to catch that.
        """
        inputs = [project_root / name for name in _BAZEL_ROOT_INPUTS if (project_root / name).is_file()]
        for dirpath, dirnames, filenames in os.walk(project_root):
            root = Path(dirpath)
            dirnames[:] = [
                name
                for name in dirnames
                if name not in _BAZEL_SKIP_DIRS and not name.startswith("bazel-") and not (root / name).is_symlink()
            ]
            for filename in filenames:
                if filename in _BAZEL_BUILD_FILES or filename.endswith(".bzl"):
                    inputs.append(root / filename)
        inputs.extend(
            collect_project_sources(
                project_root,
                CPP_SOURCE_EXTENSIONS,
                skip_dirs=_BAZEL_SKIP_DIRS,
                extra_skip_prefixes=("bazel-",),
            )
        )
        return inputs

    def _run_aquery(self, project_root: Path) -> dict:
        # Wrap the scope so user-supplied content via CODEBOARDING_CPP_BAZEL_QUERY
        # can't close the outer mnemonic(...) call and inject sibling expressions.
        query = f'mnemonic("CppCompile", ({config.bazel_query_scope()}))'
        argv = [
            "bazel",
            "aquery",
            query,
            "--output=jsonproto",
        ]
        logger.info("Bazel: running %s in %s", " ".join(argv), project_root)
        result = run_build_step(
            argv,
            cwd=project_root,
            step="bazel aquery",
            timeout_hint=f"Or narrow {config.ENV_BAZEL_QUERY}.",
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"bazel aquery output was not valid JSON: {exc}. " "First 200 bytes: " + result.stdout[:200]
            ) from exc

    @staticmethod
    def _bazel_info(project_root: Path, key: str) -> str:
        """Resolve a single ``bazel info`` key to a string (e.g. exec root)."""
        try:
            result = subprocess.run(
                ["bazel", "info", key],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"'bazel info {key}': command not found (bazel)") from exc
        except OSError as exc:
            raise RuntimeError(f"'bazel info {key}': could not run bazel ({exc})") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"'bazel info {key}' timed out") from exc
        if result.returncode != 0:
            raise RuntimeError(f"'bazel info {key}' failed (exit {result.returncode}): " f"{result.stderr.strip()}")
        return result.stdout.strip()

    @staticmethod
    def _actions_to_cdb(aquery: dict, exec_root: str) -> list[dict]:
        """Translate aquery actions into compile_commands.json entries.

        Accepts any ``*Compile`` mnemonic (``CppCompile``, ``ObjcCompile``,
        ``CppModuleCompile``); drops actions without a recognisable source.

        Why: Bazel 8+ jsonproto stores mnemonics as an integer ``mnemonicId``
        keyed against a top-level ``mnemonics`` table; older schemas inline
        the label as a ``mnemonic`` string. Handle both.
        """
        mnemonic_table: dict[int, str] = {}
        for entry in aquery.get("mnemonics", []) or []:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id")
            entry_label = entry.get("label") or entry.get("mnemonic")
            if isinstance(entry_id, int) and isinstance(entry_label, str):
                mnemonic_table[entry_id] = entry_label

        entries: list[dict] = []
        for action in aquery.get("actions", []):
            mnemonic = action.get("mnemonic")
            if not isinstance(mnemonic, str):
                mid = action.get("mnemonicId")
                mnemonic = mnemonic_table.get(mid) if isinstance(mid, int) else None
            if not isinstance(mnemonic, str) or not mnemonic.endswith("Compile"):
                continue
            args = action.get("arguments") or []
            if not args:
                continue
            source = _find_source_argument(args)
            if source is None:
                continue
            entries.append(
                {
                    "directory": exec_root,
                    "arguments": list(args),
                    "file": source,
                }
            )
        return entries

    @staticmethod
    def _require_bazel() -> None:
        if shutil.which("bazel") is None:
            raise RuntimeError("'bazel' is not on PATH. " "Install Bazel 6 or later (https://bazel.build/install).")
        try:
            result = subprocess.run(
                ["bazel", "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not probe Bazel version: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"'bazel --version' exited {result.returncode}: {result.stderr.strip()}")
        match = _BAZEL_VERSION_RE.search(result.stdout) or _BAZEL_VERSION_RE.search(result.stderr)
        if not match:
            logger.warning(
                "Could not parse Bazel version from %r — proceeding optimistically",
                (result.stdout + result.stderr).strip(),
            )
            return
        major = int(match.group(1))
        if major < _MIN_BAZEL_MAJOR:
            raise RuntimeError(
                f"Bazel {major}.x is too old — the aquery jsonproto schema changed in 6.x. " "Upgrade Bazel and retry."
            )


def _find_source_argument(args: list[str]) -> str | None:
    """First ``-c <file>`` pair wins; fall back to last positional source."""
    for i, arg in enumerate(args):
        if arg == "-c" and i + 1 < len(args):
            candidate = args[i + 1]
            if candidate.endswith(_SOURCE_SUFFIXES):
                return candidate
    # Why: the old ``enumerate(args[:-1])`` form silently dropped a trailing
    # ``-o`` from bookkeeping, leaving its preceding source-looking arg
    # eligible to win the reverse scan (``["clang", "generated/foo.cc", "-o"]``
    # -> ``generated/foo.cc``). Walk the full list and, for a dangling
    # ``-o``/``--output`` at the end of argv, treat the preceding arg as
    # the implicit output slot — same as if the value were present.
    output_values: set[int] = set()
    for i, arg in enumerate(args):
        if arg in ("-o", "--output"):
            if i + 1 < len(args):
                output_values.add(i + 1)
            elif i - 1 >= 0:
                output_values.add(i - 1)
    for i in range(len(args) - 1, -1, -1):
        arg = args[i]
        if i in output_values:
            continue
        if arg.endswith(_SOURCE_SUFFIXES) and not arg.startswith("-"):
            return arg
    return None
