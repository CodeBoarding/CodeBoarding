"""Tests for the CDB-generator config reader."""

from __future__ import annotations

import pytest

from static_analyzer.engine.adapters.cpp_cdb import config


class TestIsGenerationEnabled:
    """The opt-in switch must fail closed — an unset or garbage value keeps
    generation off, because we don't want to invoke 'make' on a user's repo
    without explicit consent.
    """

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
        """Typos like 'ture' must not accidentally enable generation."""
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
        """A zero/negative timeout would instantly fail every build — treat
        as misconfiguration and use the default.
        """
        monkeypatch.setenv(config.ENV_TIMEOUT, "0")
        assert config.generator_timeout_seconds() == 900
        monkeypatch.setenv(config.ENV_TIMEOUT, "-1")
        assert config.generator_timeout_seconds() == 900


class TestConfigureArgs:
    def test_default_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(config.ENV_CONFIGURE_ARGS, raising=False)
        assert config.configure_args() == []

    def test_shell_lexed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Quoted values survive the split — a ``--prefix="/opt/x y"`` flag
        must not be torn apart on the internal space.
        """
        monkeypatch.setenv(config.ENV_CONFIGURE_ARGS, '--prefix="/opt/x y" --disable-shared')
        assert config.configure_args() == ["--prefix=/opt/x y", "--disable-shared"]


class TestMakeTarget:
    def test_default_forces_full_rebuild(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bear only records commands it actually intercepts — a warm tree
        produces an empty CDB. Default must run ``clean`` first.
        """
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
