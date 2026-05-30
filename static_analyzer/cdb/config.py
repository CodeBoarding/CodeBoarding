"""Runtime configuration surface for CDB generation."""

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
_DEFAULT_BAZEL_QUERY = "//..."


def is_generation_enabled() -> bool:
    """True when the user has explicitly opted in to auto-generation.

    Why: fail-closed — invoking ``make``/``bazel build`` writes into the
    user's repo and may hit the network, so an unset or garbage value
    keeps generation off.
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
    """Shell-lexed ``./configure`` flags for Autotools projects."""
    raw = os.environ.get(ENV_CONFIGURE_ARGS, "").strip()
    if not raw:
        return []
    return shlex.split(raw)


def make_target() -> list[str]:
    """Make targets passed after ``--`` to bear; default ``clean all``."""
    raw = os.environ.get(ENV_MAKE_TARGET, "").strip()
    if not raw:
        return shlex.split(_DEFAULT_MAKE_TARGET)
    return shlex.split(raw)


def bazel_query_scope() -> str:
    """Scope for ``bazel aquery 'mnemonic("CppCompile", <scope>)'``."""
    raw = os.environ.get(ENV_BAZEL_QUERY, "").strip()
    return raw or _DEFAULT_BAZEL_QUERY


def fingerprint_options() -> list[tuple[str, str]]:
    """Normalized ``(env_name, value)`` pairs for every knob that affects CDB output.

    Why: ``CdbGenerator.generate`` fingerprints inputs to decide whether a cached
    ``compile_commands.json`` is reusable. Without these knobs in the key, flipping
    ``CODEBOARDING_CPP_MAKE_TARGET`` (or configure args, or Bazel query scope)
    silently reuses a stale CDB built for a different intent. Empty env vars are
    dropped so unset knobs don't pollute the key. Values are shell-lex normalized
    where they're shell-lexed at use-site, so semantically equivalent quoting
    collapses to the same key.
    """
    out: list[tuple[str, str]] = []
    raw_target = os.environ.get(ENV_MAKE_TARGET, "").strip()
    if raw_target:
        out.append((ENV_MAKE_TARGET, " ".join(shlex.split(raw_target))))
    raw_configure = os.environ.get(ENV_CONFIGURE_ARGS, "").strip()
    if raw_configure:
        out.append((ENV_CONFIGURE_ARGS, " ".join(shlex.split(raw_configure))))
    raw_bazel = os.environ.get(ENV_BAZEL_QUERY, "").strip()
    if raw_bazel:
        out.append((ENV_BAZEL_QUERY, raw_bazel))
    out.sort()
    return out


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in _TRUTHY
