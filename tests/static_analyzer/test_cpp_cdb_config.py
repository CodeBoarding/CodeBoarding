"""Tests for the CDB-generator config reader."""

from __future__ import annotations

import pytest

from static_analyzer.cdb import config


class TestIsGenerationEnabled:
    def test_unset_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_ENABLE, raising=False)
        assert config.is_generation_enabled() is False

    def test_empty_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_ENABLE, "")
        assert config.is_generation_enabled() is False

    def test_zero_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_ENABLE, "0")
        assert config.is_generation_enabled() is False

    def test_false_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_ENABLE, "false")
        assert config.is_generation_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on"])
    def test_truthy_values(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_ENABLE, value)
        assert config.is_generation_enabled() is True

    def test_garbage_string_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_ENABLE, "ture")
        assert config.is_generation_enabled() is False


class TestForceRegenerate:
    def test_unset_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_FORCE_REGENERATE, raising=False)
        assert config.force_regenerate() is False

    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    def test_truthy_values(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_FORCE_REGENERATE, value)
        assert config.force_regenerate() is True


class TestGeneratorTimeoutSeconds:
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_TIMEOUT, raising=False)
        assert config.generator_timeout_seconds() == 900

    def test_custom_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_TIMEOUT, "120")
        assert config.generator_timeout_seconds() == 120

    def test_garbage_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_TIMEOUT, "five minutes")
        assert config.generator_timeout_seconds() == 900

    def test_nonpositive_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Zero/negative would instantly fail every build — treat as misconfigured."""
        monkeypatch.setenv(config.ENV_TIMEOUT, "0")
        assert config.generator_timeout_seconds() == 900
        monkeypatch.setenv(config.ENV_TIMEOUT, "-1")
        assert config.generator_timeout_seconds() == 900


class TestConfigureArgs:
    def test_default_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_CONFIGURE_ARGS, raising=False)
        assert config.configure_args() == []

    def test_shell_lexed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_CONFIGURE_ARGS, '--prefix="/opt/x y" --disable-shared')
        assert config.configure_args() == ["--prefix=/opt/x y", "--disable-shared"]


class TestMakeTarget:
    def test_default_forces_full_rebuild(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default runs ``clean`` first — a warm tree would otherwise emit an empty CDB."""
        monkeypatch.delenv(config.ENV_MAKE_TARGET, raising=False)
        assert config.make_target() == ["clean", "all"]

    def test_custom_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_MAKE_TARGET, "release")
        assert config.make_target() == ["release"]


class TestBazelQueryScope:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_BAZEL_QUERY, raising=False)
        assert config.bazel_query_scope() == "deps(//...)"

    def test_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(config.ENV_BAZEL_QUERY, "//src/...")
        assert config.bazel_query_scope() == "//src/..."


class TestFingerprintOptions:
    """Knobs that change CDB output must land in the fingerprint cache key.

    Why: M9 — without these the cache reuses a stale ``compile_commands.json``
    when the user flips ``CODEBOARDING_CPP_MAKE_TARGET`` / ``_CONFIGURE_ARGS``
    / ``_BAZEL_QUERY``.
    """

    def test_fingerprint_options_empty_when_no_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_MAKE_TARGET, raising=False)
        monkeypatch.delenv(config.ENV_CONFIGURE_ARGS, raising=False)
        monkeypatch.delenv(config.ENV_BAZEL_QUERY, raising=False)
        assert config.fingerprint_options() == []

    def test_fingerprint_options_includes_make_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_CONFIGURE_ARGS, raising=False)
        monkeypatch.delenv(config.ENV_BAZEL_QUERY, raising=False)
        monkeypatch.setenv(config.ENV_MAKE_TARGET, "release")
        assert config.fingerprint_options() == [(config.ENV_MAKE_TARGET, "release")]

    def test_fingerprint_options_includes_configure_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_MAKE_TARGET, raising=False)
        monkeypatch.delenv(config.ENV_BAZEL_QUERY, raising=False)
        monkeypatch.setenv(config.ENV_CONFIGURE_ARGS, "--disable-shared")
        assert config.fingerprint_options() == [(config.ENV_CONFIGURE_ARGS, "--disable-shared")]

    def test_fingerprint_options_includes_bazel_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_MAKE_TARGET, raising=False)
        monkeypatch.delenv(config.ENV_CONFIGURE_ARGS, raising=False)
        monkeypatch.setenv(config.ENV_BAZEL_QUERY, "//src/...")
        assert config.fingerprint_options() == [(config.ENV_BAZEL_QUERY, "//src/...")]

    def test_fingerprint_options_pairs_are_sorted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stable key order so callers don't have to sort and dict-order
        shuffles don't bust the cache.
        """
        monkeypatch.setenv(config.ENV_BAZEL_QUERY, "//src/...")
        monkeypatch.setenv(config.ENV_MAKE_TARGET, "release")
        monkeypatch.setenv(config.ENV_CONFIGURE_ARGS, "--disable-shared")
        result = config.fingerprint_options()
        assert result == sorted(result)
        assert dict(result) == {
            config.ENV_BAZEL_QUERY: "//src/...",
            config.ENV_MAKE_TARGET: "release",
            config.ENV_CONFIGURE_ARGS: "--disable-shared",
        }

    @pytest.mark.parametrize(
        ("raw", "normalized"),
        [
            ("  release  ", "release"),
            ("clean  all", "clean all"),
            ("'clean' 'all'", "clean all"),
            ('"clean install"', "clean install"),
        ],
    )
    def test_fingerprint_options_normalizes_make_target(
        self, raw: str, normalized: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whitespace and quoting that ``shlex.split`` collapses must hash
        the same — otherwise ``"clean install"`` and ``clean install`` would
        spuriously bust the cache.
        """
        monkeypatch.delenv(config.ENV_CONFIGURE_ARGS, raising=False)
        monkeypatch.delenv(config.ENV_BAZEL_QUERY, raising=False)
        monkeypatch.setenv(config.ENV_MAKE_TARGET, raw)
        assert config.fingerprint_options() == [(config.ENV_MAKE_TARGET, normalized)]

    def test_fingerprint_options_skips_empty_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An env var set to whitespace shouldn't pollute the key."""
        monkeypatch.setenv(config.ENV_MAKE_TARGET, "   ")
        monkeypatch.setenv(config.ENV_CONFIGURE_ARGS, "")
        monkeypatch.delenv(config.ENV_BAZEL_QUERY, raising=False)
        assert config.fingerprint_options() == []
