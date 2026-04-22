"""Runtime configuration surface for CDB generation.

Every knob here has a conservative default: generation is off unless the
user explicitly opts in, because invoking ``make`` / ``bazel build`` is
slow and has real blast radius (writes into the repo, may hit the network).
"""

from __future__ import annotations

import os
import shlex

ENV_ENABLE = "CODEBOARDING_CPP_GENERATE_CDB"
ENV_FORCE_REGENERATE = "CODEBOARDING_CPP_FORCE_REGENERATE"
ENV_TIMEOUT = "CODEBOARDING_CPP_GENERATOR_TIMEOUT"
ENV_CONFIGURE_ARGS = "CODEBOARDING_CPP_CONFIGURE_ARGS"
ENV_MAKE_TARGET = "CODEBOARDING_CPP_MAKE_TARGET"
ENV_BAZEL_QUERY = "CODEBOARDING_CPP_BAZEL_QUERY"
_TRUTHY = {"1", "true", "yes", "on"}

_DEFAULT_TIMEOUT_SECONDS = 900
_DEFAULT_MAKE_TARGET = "clean all"
_DEFAULT_BAZEL_QUERY = "deps(//...)"


def is_generation_enabled() -> bool:
    """True when the user has explicitly opted in to auto-generation.

    Accepts the usual truthy strings (``1``, ``true``, ``yes``, ``on``,
    case-insensitive). Anything else — including the var being unset —
    keeps generation disabled.
    """
    return _env_truthy(ENV_ENABLE)


def force_regenerate() -> bool:
    return _env_truthy(ENV_FORCE_REGENERATE)


def generator_timeout_seconds() -> int:
    """Upper bound on how long any single build step is allowed to run."""
    raw = os.environ.get(ENV_TIMEOUT)
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS


def configure_args() -> list[str]:
    """User-supplied flags forwarded to ``./configure`` for Autotools projects.

    Shell-lexed so quoted values like ``--prefix="/opt/x y"`` survive intact.
    """
    raw = os.environ.get(ENV_CONFIGURE_ARGS, "").strip()
    if not raw:
        return []
    return shlex.split(raw)


def make_target() -> list[str]:
    """Make targets passed after ``--`` to bear. Default forces a full rebuild
    (``clean all``) so bear actually sees compile invocations — a warm tree
    would otherwise return a near-empty CDB.
    """
    raw = os.environ.get(ENV_MAKE_TARGET, "").strip()
    if not raw:
        return shlex.split(_DEFAULT_MAKE_TARGET)
    return shlex.split(raw)


def bazel_query_scope() -> str:
    """Bazel query used in ``bazel aquery 'mnemonic("CppCompile", <scope>)'``.

    Defaults to every target in the workspace; narrow via env var to skip
    vendored third-party code.
    """
    raw = os.environ.get(ENV_BAZEL_QUERY, "").strip()
    return raw or _DEFAULT_BAZEL_QUERY


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in _TRUTHY
