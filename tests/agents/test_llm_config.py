"""Tests for LLM configuration and model detection."""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

from agents.llm_config import (
    LLM_ENV_VARS,
    LLM_PROVIDERS,
    LLM_PROVIDER_ENV_VARS,
    LLMConfigError,
    MONITORING_CALLBACK,
    _model_accepts_temperature,
    get_current_agent_context_window,
    get_current_prompt_profile,
    initialize_agent_llm,
    validate_api_key_provided,
)
from agents.model_capabilities import ContextWindow
from agents.prompts import PromptProfile


class TestValidateApiKeyProvided:
    def test_no_keys_raises_value_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="No LLM provider selected"):
                validate_api_key_provided()

    def test_single_key_passes(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            validate_api_key_provided()  # should not raise

    def test_multiple_keys_raises_value_error(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            with pytest.raises(ValueError, match="Multiple LLM providers selected"):
                validate_api_key_provided()

    def test_base_url_without_key_passes_with_warning(self, caplog):
        # Self-hosted / OpenAI-compatible endpoint: active via OPENAI_BASE_URL,
        # no OPENAI_API_KEY. Should not raise; should warn.
        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://127.0.0.1:8000/v1"}, clear=True):
            with caplog.at_level(logging.WARNING, logger="agents.llm_config"):
                validate_api_key_provided()  # should not raise
        assert any("keyless local endpoint" in r.message for r in caplog.records)

    def test_base_url_with_key_passes_without_warning(self, caplog):
        # base_url + a real key: valid and no keyless warning.
        env = {"OPENAI_BASE_URL": "http://127.0.0.1:8000/v1", "OPENAI_API_KEY": "sk-test"}
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level(logging.WARNING, logger="agents.llm_config"):
                validate_api_key_provided()  # should not raise
        assert not any("keyless local endpoint" in r.message for r in caplog.records)

    def test_base_url_plus_other_provider_still_ambiguous(self):
        # Multi-key detection is preserved even when a base URL is set.
        env = {"OPENAI_BASE_URL": "http://127.0.0.1:8000/v1", "ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="Multiple LLM providers selected"):
                validate_api_key_provided()

    def test_litellm_proxy_base_url_passes(self):
        with patch.dict(os.environ, {"LITELLM_BASE_URL": "http://localhost:4000"}, clear=True):
            validate_api_key_provided()  # should not raise; base URL activates the proxy

    def test_litellm_key_without_base_url_raises_with_hint(self):
        # A key alone does not activate litellm; the error must point at the missing URL.
        with patch.dict(os.environ, {"LITELLM_API_KEY": "sk-litellm-test"}, clear=True):
            with pytest.raises(LLMConfigError, match="is selected by LITELLM_BASE_URL"):
                validate_api_key_provided()

    def test_stray_inactive_key_warns_but_passes(self, caplog):
        # A leftover key for an inactive provider is reported, not treated as ambiguity.
        env = {"OPENAI_API_KEY": "sk-test", "LITELLM_API_KEY": "sk-litellm-test"}
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level(logging.WARNING, logger="agents.llm_config"):
                validate_api_key_provided()  # should not raise
        assert any("LITELLM_API_KEY is set" in r.message for r in caplog.records)


class TestProviderSelection:
    def test_provider_env_vars_are_derived_from_config(self):
        config_env_vars = {
            var
            for config in LLM_PROVIDERS.values()
            for var in [*config.selection_envs, config.api_key_env, config.base_url_env]
            if var
        }

        assert config_env_vars == LLM_PROVIDER_ENV_VARS
        assert LLM_ENV_VARS == LLM_PROVIDER_ENV_VARS | {"AGENT_MODEL"}

    def test_agent_model_defaults_remain_unchanged(self):
        assert {name: config.agent_model for name, config in LLM_PROVIDERS.items()} == {
            "openai": "gpt-4o",
            "vercel": "google/gemini-3.8-flash",
            "anthropic": "claude-sonnet-5",
            "google": "gemini-3.8-flash",
            "aws": "anthropic.claude-sonnet-4-6",
            "cerebras": "zai-glm-4.7",
            "ollama": "qwen3:30b",
            "deepseek": "deepseek-v4-flash",
            "glm": "glm-4.7-flash",
            "kimi": "kimi-k2.6",
            "openrouter": "google/gemini-3.8-flash",
            "orcarouter": "openai/gpt-5.4-mini",
            "litellm": "gpt-4o",
        }

    @pytest.mark.parametrize(
        ("provider_name", "env_var", "default_url"),
        [
            ("openai", "OPENAI_BASE_URL", None),
            ("vercel", "VERCEL_BASE_URL", "https://ai-gateway.vercel.sh/v1"),
            ("anthropic", "ANTHROPIC_BASE_URL", None),
            ("ollama", "OLLAMA_BASE_URL", None),
            ("deepseek", "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            ("glm", "GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            ("kimi", "KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
            ("openrouter", "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            ("orcarouter", "ORCAROUTER_BASE_URL", "https://api.orcarouter.ai/v1"),
            ("litellm", "LITELLM_BASE_URL", None),
        ],
    )
    def test_base_url_metadata_resolves_provider_defaults(self, provider_name, env_var, default_url):
        config = LLM_PROVIDERS[provider_name]
        assert config.base_url_env == env_var

        with patch.dict(os.environ, {}, clear=True):
            resolved = config.get_resolved_extra_args()
            if default_url is None:
                assert "base_url" not in resolved
            else:
                assert resolved["base_url"] == default_url

        with patch.dict(os.environ, {env_var: "https://custom.example/v1"}, clear=True):
            assert config.get_resolved_extra_args()["base_url"] == "https://custom.example/v1"

    def test_every_provider_declaring_a_timeout_gives_its_client_a_real_one(self):
        """A ``None`` here disables the SDK default, so a stalled request hangs the whole run."""
        assert [
            name
            for name, config in LLM_PROVIDERS.items()
            if "timeout" in config.extra_args and not config.extra_args["timeout"]
        ] == []

    def test_anthropic_defaults_to_sonnet_5(self):
        anthropic = LLM_PROVIDERS["anthropic"]

        assert anthropic.agent_model == "claude-sonnet-5"

    def test_kimi_defaults_to_k2_6(self):
        kimi = LLM_PROVIDERS["kimi"]

        assert kimi.agent_model == "kimi-k2.6"

    def test_ollama_activates_via_ollama_host(self):
        ollama = LLM_PROVIDERS["ollama"]
        with patch.dict(os.environ, {"OLLAMA_HOST": "127.0.0.1:11434"}, clear=True):
            assert ollama.is_selected_by_env() is True
            assert ollama.has_real_api_key() is False

    def test_ollama_cloud_key_is_a_real_key(self):
        ollama = LLM_PROVIDERS["ollama"]
        env = {"OLLAMA_BASE_URL": "https://ollama.com", "OLLAMA_API_KEY": "ok-test"}
        with patch.dict(os.environ, env, clear=True):
            assert ollama.is_selected_by_env() is True
            assert ollama.has_real_api_key() is True

    def test_orcarouter_selected_via_api_key(self):
        orcarouter = LLM_PROVIDERS["orcarouter"]
        env = {"ORCAROUTER_API_KEY": "sk-orca-test"}
        with patch.dict(os.environ, env, clear=True):
            assert orcarouter.is_selected_by_env() is True
            assert orcarouter.has_real_api_key() is True
            assert orcarouter.get_resolved_extra_args()["base_url"] == "https://api.orcarouter.ai/v1"

    def test_aws_has_no_api_key_env(self):
        # botocore consumes AWS_BEARER_TOKEN_BEDROCK directly; it is never passed as a kwarg.
        aws = LLM_PROVIDERS["aws"]
        with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "bearer-test"}, clear=True):
            assert aws.is_selected_by_env() is True
            assert aws.get_api_key() is None
            assert aws.has_real_api_key() is False

    def test_anthropic_client_options_are_resolved(self):
        env = {
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "ANTHROPIC_BASE_URL": "https://resource.services.ai.azure.com/anthropic",
        }
        with patch.dict(os.environ, env, clear=True):
            anthropic = LLM_PROVIDERS["anthropic"]
            extra_args = anthropic.get_resolved_extra_args()
            assert extra_args["base_url"] == env["ANTHROPIC_BASE_URL"]
            assert extra_args["thinking"] == {"type": "disabled"}

    def test_anthropic_base_url_requires_key_without_selecting_provider(self):
        env = {"ANTHROPIC_BASE_URL": "https://resource.services.ai.azure.com/anthropic"}
        with patch.dict(os.environ, env, clear=True):
            assert LLM_PROVIDERS["anthropic"].is_selected_by_env() is False
            with pytest.raises(LLMConfigError, match="requires ANTHROPIC_API_KEY"):
                validate_api_key_provided()

    def test_anthropic_base_url_does_not_conflict_with_selected_provider(self):
        env = {
            "OPENAI_API_KEY": "sk-test",
            "ANTHROPIC_BASE_URL": "https://resource.services.ai.azure.com/anthropic",
        }
        with patch.dict(os.environ, env, clear=True):
            validate_api_key_provided()

    def test_native_provider_base_url_without_key_remains_valid(self):
        with patch.dict(os.environ, {"DEEPSEEK_BASE_URL": "http://localhost:8000/v1"}, clear=True):
            validate_api_key_provided()


class TestLLMConfigKeyless:
    def test_openai_is_keyless_capable(self):
        assert LLM_PROVIDERS["openai"].keyless_capable is True

    def test_empty_string_key_is_not_a_real_key(self):
        # Empty env values must keep meaning "unset", matching is_selected_by_env() and
        # the `api_key or "no-key-required"` fallback.
        openai = LLM_PROVIDERS["openai"]
        env = {"OPENAI_BASE_URL": "http://127.0.0.1:8000/v1", "OPENAI_API_KEY": ""}
        with patch.dict(os.environ, env, clear=True):
            assert openai.has_real_api_key() is False

    def test_has_real_api_key_distinguishes_from_is_selected_by_env(self):
        openai = LLM_PROVIDERS["openai"]
        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://127.0.0.1:8000/v1"}, clear=True):
            assert openai.is_selected_by_env() is True
            assert openai.has_real_api_key() is False
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            assert openai.is_selected_by_env() is True
            assert openai.has_real_api_key() is True


class TestAgentContextWindow:
    def test_openrouter_fallback_uses_large_default(self):
        with (
            patch(
                "agents.llm_config._resolve_selected_provider",
                return_value=("openrouter", MagicMock(), "@preset/production"),
            ),
            patch(
                "agents.llm_config.get_context_window",
                return_value=ContextWindow(256_000, 64_000, is_fallback=True),
            ),
        ):
            ctx = get_current_agent_context_window()

        assert ctx == ContextWindow(1_048_576, 65_536, is_fallback=True)

    def test_non_openrouter_fallback_stays_generic(self):
        with (
            patch("agents.llm_config._resolve_selected_provider", return_value=("openai", MagicMock(), "private")),
            patch(
                "agents.llm_config.get_context_window",
                return_value=ContextWindow(256_000, 64_000, is_fallback=True),
            ),
        ):
            ctx = get_current_agent_context_window()

        assert ctx == ContextWindow(256_000, 64_000, is_fallback=True)


class TestLiteLLMProvider:
    """The litellm provider proxies an OpenAI-compatible server via base_url."""

    def test_uses_proxy_base_url_and_key(self):
        env = {
            "LITELLM_API_KEY": "sk-litellm-test",
            "LITELLM_BASE_URL": "http://localhost:4000",
            "AGENT_MODEL": "my-proxy-model",
        }
        with patch.dict(os.environ, env, clear=True):
            litellm_config = LLM_PROVIDERS["litellm"]
            mock_llm = MagicMock()
            with patch.object(litellm_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                initialize_agent_llm()

                agent_kwargs = mock_chat_class.call_args.kwargs
                assert agent_kwargs["model"] == "my-proxy-model"
                assert agent_kwargs["base_url"] == "http://localhost:4000"
                assert agent_kwargs["api_key"] == "sk-litellm-test"

    def test_keyless_proxy_uses_placeholder_key(self):
        # Base URL alone activates the proxy; a placeholder key is sent when none is set.
        env = {"LITELLM_BASE_URL": "http://localhost:4000", "AGENT_MODEL": "my-proxy-model"}
        with patch.dict(os.environ, env, clear=True):
            litellm_config = LLM_PROVIDERS["litellm"]
            mock_llm = MagicMock()
            with patch.object(litellm_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                initialize_agent_llm()

                agent_kwargs = mock_chat_class.call_args.kwargs
                assert agent_kwargs["base_url"] == "http://localhost:4000"
                assert agent_kwargs["api_key"] == "no-key-required"

    def test_key_without_base_url_raises(self):
        # A key alone must not select litellm and fall through to the default OpenAI endpoint.
        env = {"LITELLM_API_KEY": "sk-litellm-test", "AGENT_MODEL": "my-proxy-model"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="is selected by LITELLM_BASE_URL"):
                initialize_agent_llm()


class TestEnvironmentVariables:
    """Test that agent model overrides are respected."""

    def test_agent_model_env_var_respected(self):
        with patch.dict(os.environ, {"AGENT_MODEL": "gpt-4-turbo", "OPENAI_API_KEY": "test-key"}):
            original_openai_config = LLM_PROVIDERS["openai"]
            mock_llm = MagicMock()
            with patch.object(original_openai_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                initialize_agent_llm()

        assert mock_chat_class.call_count == 1
        assert mock_chat_class.call_args.kwargs["model"] == "gpt-4-turbo"

    @patch("agents.llm_config.LLM_PROVIDERS")
    def test_agent_model_override_takes_precedence(self, mock_providers):
        """Test that model_override parameter takes precedence over default in initialize_agent_llm()."""
        mock_config = MagicMock()
        mock_config.is_selected_by_env.return_value = True
        mock_config.agent_model = "gpt-4o"
        mock_config.agent_temperature = 0.1
        mock_config.get_api_key.return_value = "test-key"
        mock_config.get_resolved_extra_args.return_value = {}
        mock_config.chat_class = MagicMock(return_value=MagicMock())
        mock_providers.__getitem__.return_value = mock_config
        mock_providers.items.return_value = [("openai", mock_config)]

        initialize_agent_llm(model_override="gpt-4o-mini")

        assert mock_config.chat_class.call_args[1]["model"] == "gpt-4o-mini"

    def test_agent_model_defaults_when_no_env_var(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            original_openai_config = LLM_PROVIDERS["openai"]
            mock_llm = MagicMock()
            with patch.object(original_openai_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                initialize_agent_llm()

        assert mock_chat_class.call_args.kwargs["model"] == "gpt-4o"

    def test_configured_model_is_used_when_environment_has_no_override(self):
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True),
            patch("agents.llm_config._agent_model_override", "configured-model"),
        ):
            original_openai_config = LLM_PROVIDERS["openai"]
            mock_llm = MagicMock()
            with patch.object(original_openai_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                initialize_agent_llm()

        assert mock_chat_class.call_args.kwargs["model"] == "configured-model"

    def test_shell_model_wins_over_configured_model(self):
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "AGENT_MODEL": "shell-model"}, clear=True),
            patch("agents.llm_config._agent_model_override", "configured-model"),
        ):
            original_openai_config = LLM_PROVIDERS["openai"]
            mock_llm = MagicMock()
            with patch.object(original_openai_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                initialize_agent_llm()

        assert mock_chat_class.call_args.kwargs["model"] == "shell-model"


class TestPromptProfiles:
    def test_qwen_automatically_uses_strict_prompts(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "AGENT_MODEL": "qwen3-coder"}, clear=True):
            assert get_current_prompt_profile() is PromptProfile.STRICT

    def test_actual_model_override_controls_prompt_profile(self):
        model = MagicMock()
        model.model_name = "qwen3-coder"

        assert get_current_prompt_profile(model) is PromptProfile.STRICT

    def test_strong_model_uses_standard_prompts(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "AGENT_MODEL": "gpt-5"}, clear=True):
            assert get_current_prompt_profile() is PromptProfile.STANDARD


class TestTemperatureGating:
    """Models requiring provider sampling defaults must omit temperature."""

    @pytest.mark.parametrize(
        "model_name",
        [
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-fable-5",
            "claude-mythos-5",
            "anthropic.claude-opus-4-8",  # Bedrock prefix
            "us.anthropic.claude-opus-4-8-v1:0",  # Bedrock with region
            "CLAUDE-OPUS-4-8",  # case-insensitive
            "gemini-3.8-flash",
            "google/gemini-3.8-flash",
            "google/gemini-3.5-flash-lite",
        ],
    )
    def test_sampling_param_free_models_omit_temperature(self, model_name):
        assert _model_accepts_temperature(model_name) is False

    @pytest.mark.parametrize(
        "model_name",
        ["claude-sonnet-4-6", "claude-haiku-4-5", "anthropic.claude-sonnet-4-6", "gpt-4o", "gemini-2.5-flash"],
    )
    def test_other_models_accept_temperature(self, model_name):
        assert _model_accepts_temperature(model_name) is True

    def test_gemini_3_request_omits_temperature(self):
        google = LLM_PROVIDERS["google"]
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=True):
            with patch.object(google, "chat_class", return_value=MagicMock()) as mock_chat_class:
                initialize_agent_llm()

        assert "temperature" not in mock_chat_class.call_args.kwargs

    def test_opus_built_without_temperature_attr(self) -> None:
        # Offline: ChatAnthropic built without temperature exposes .temperature == None.
        env = {"ANTHROPIC_API_KEY": "sk-ant-test", "AGENT_MODEL": "claude-opus-4-8"}
        with patch.dict(os.environ, env, clear=True):
            agent_llm = initialize_agent_llm("claude-opus-4-8")
            assert getattr(agent_llm, "temperature") is None

    def test_sonnet_built_with_zero_temperature(self) -> None:
        # Offline: a sampling-capable model keeps the deterministic temperature=0.
        env = {"ANTHROPIC_API_KEY": "sk-ant-test", "AGENT_MODEL": "claude-sonnet-4-6"}
        with patch.dict(os.environ, env, clear=True):
            agent_llm = initialize_agent_llm("claude-sonnet-4-6")
            assert getattr(agent_llm, "temperature") == 0.0


class TestMonitoringIntegration:
    """Test that model names are properly passed to monitoring callbacks."""

    @patch("agents.llm_config.LLM_PROVIDERS")
    def test_global_monitoring_callback_gets_model_name(self, mock_providers):
        mock_config = MagicMock()
        mock_config.is_selected_by_env.return_value = True
        mock_config.agent_model = "gpt-4o"
        mock_config.agent_temperature = 0.1
        mock_config.get_api_key.return_value = "test-key"
        mock_config.get_resolved_extra_args.return_value = {}
        mock_llm_instance = MagicMock()
        mock_config.chat_class = MagicMock(return_value=mock_llm_instance)
        mock_providers.__getitem__.return_value = mock_config
        mock_providers.items.return_value = [("openai", mock_config)]

        with patch.dict(os.environ, {"AGENT_MODEL": "gpt-4-turbo"}, clear=False):
            initialize_agent_llm()

        assert MONITORING_CALLBACK.model_name == "gpt-4-turbo"
