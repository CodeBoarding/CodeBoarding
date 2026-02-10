"""Tests for LLM configuration and model detection."""

import os
import pytest
from unittest.mock import patch, MagicMock
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agents.llm_config import initialize_agent_llm, initialize_parsing_llm
from agents.prompts.prompt_factory import LLMType


class DummyChatModel(BaseChatModel):
    """Minimal chat model used to validate round-robin behavior in tests."""

    key_id: str

    @property
    def _llm_type(self) -> str:
        return "dummy"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.key_id))])

    def bind_tools(self, tools, *, tool_choice: str | None = None, **kwargs):
        return self

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs):
        return self


class TestDetectLLMTypeFromModel:
    """Test the LLMType.from_model_name function with various model names."""

    # GPT Models
    @pytest.mark.parametrize(
        "model_name",
        [
            "gpt-4",
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-4-turbo-preview",
            "gpt-3.5-turbo",
            "gpt-5-mini",  # Future model
            "gpt-5-max",  # Future model with different suffix
            "gpt4",  # Without dash
            "GPT-4",  # Uppercase
            "o1-preview",
            "o1-mini",
            "o3-mini",  # Future O-series model
        ],
    )
    def test_gpt_models(self, model_name):
        """Test that GPT models are correctly detected."""
        assert LLMType.from_model_name(model_name) == LLMType.GPT4

    # Claude Models
    @pytest.mark.parametrize(
        "model_name",
        [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            "claude-3-7-sonnet-20250219",
            "claude-3.5-sonnet-20241022",
            "claude-2.1",
            "claude-instant-1.2",
            "CLAUDE-3-OPUS",  # Uppercase
            "anthropic.claude-3-sonnet-20240229-v1:0",  # Bedrock format
            "us.anthropic.claude-3-7-sonnet-20250219-v1:0",  # Bedrock with region
            "opus",  # Just model family name
            "sonnet",  # Just model family name
            "haiku",  # Just model family name
        ],
    )
    def test_claude_models(self, model_name):
        """Test that Claude/Anthropic models are correctly detected."""
        assert LLMType.from_model_name(model_name) == LLMType.CLAUDE

    # Gemini Models
    @pytest.mark.parametrize(
        "model_name",
        [
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-2.0-flash",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-3-pro-preview",  # Future model
            "gemini-flash",  # Simplified name
            "GEMINI-PRO",  # Uppercase
            "gemini-exp-1206",  # Experimental model
        ],
    )
    def test_gemini_models(self, model_name):
        """Test that Gemini models are correctly detected."""
        assert LLMType.from_model_name(model_name) == LLMType.GEMINI_FLASH

    # Edge Cases
    def test_unknown_model_defaults_to_gemini(self):
        """Test that unknown models default to GEMINI_FLASH."""
        assert LLMType.from_model_name("unknown-model-xyz") == LLMType.GEMINI_FLASH
        assert LLMType.from_model_name("custom-finetuned-model") == LLMType.GEMINI_FLASH
        assert LLMType.from_model_name("llama-3-70b") == LLMType.GEMINI_FLASH

    def test_mixed_case_detection(self):
        """Test that detection is case-insensitive."""
        assert LLMType.from_model_name("GPT-4O") == LLMType.GPT4
        assert LLMType.from_model_name("Claude-3-Opus") == LLMType.CLAUDE
        assert LLMType.from_model_name("GEMINI-2.5-FLASH") == LLMType.GEMINI_FLASH

    def test_models_with_provider_prefix(self):
        """Test models that include provider prefixes (e.g., from Bedrock)."""
        assert LLMType.from_model_name("anthropic.claude-3-sonnet-20240229-v1:0") == LLMType.CLAUDE
        assert LLMType.from_model_name("us.anthropic.claude-3-haiku-20240307-v1:0") == LLMType.CLAUDE

    # Typo resistance (common typos should still match)
    def test_typo_claude(self):
        """Test that 'cladude' (common typo) still matches Claude."""
        # Note: Our current implementation doesn't handle typos, but we can add this if needed
        # For now, this test documents the behavior
        assert LLMType.from_model_name("cladude") == LLMType.GEMINI_FLASH  # Falls back to default

    # Vercel Gateway Models
    def test_vercel_gateway_models(self):
        """
        Test that models accessed through Vercel gateway are correctly detected
        based on their actual model name, not the provider.
        """
        # Vercel can proxy any model, detection should work based on model name
        assert LLMType.from_model_name("gpt-4o") == LLMType.GPT4
        assert LLMType.from_model_name("claude-3-7-sonnet-20250219") == LLMType.CLAUDE
        assert LLMType.from_model_name("gemini-2.5-flash") == LLMType.GEMINI_FLASH

    # Future-proofing
    def test_future_gpt_versions(self):
        """Test that future GPT versions are detected correctly."""
        assert LLMType.from_model_name("gpt-6-nano") == LLMType.GPT4
        assert LLMType.from_model_name("gpt-10-ultra") == LLMType.GPT4

    def test_future_claude_versions(self):
        """Test that future Claude versions are detected correctly."""
        assert LLMType.from_model_name("claude-4-opus") == LLMType.CLAUDE
        assert LLMType.from_model_name("claude-5-mega") == LLMType.CLAUDE

    def test_future_gemini_versions(self):
        """Test that future Gemini versions are detected correctly."""
        assert LLMType.from_model_name("gemini-4.0-ultra") == LLMType.GEMINI_FLASH
        assert LLMType.from_model_name("gemini-10-pro-max") == LLMType.GEMINI_FLASH

    # DeepSeek Models
    @pytest.mark.parametrize(
        "model_name",
        [
            "deepseek-chat",
            "deepseek-coder",
            "deepseek-v3",
            "deepseek-v3.2",
            "deepseek-v3.2-lite",
            "deepseek-reasoner",
            "DEEPSEEK-CHAT",  # Uppercase
            "DeepSeek-Coder",  # Mixed case
            "deepseek-v4",  # Future version
            "deepseek-v3.2-turbo",  # Variant
        ],
    )
    def test_deepseek_models(self, model_name):
        """Test that DeepSeek models are correctly detected."""
        assert LLMType.from_model_name(model_name) == LLMType.DEEPSEEK

    def test_deepseek_via_vercel_gateway(self):
        """Test that DeepSeek models accessed through Vercel gateway are correctly detected."""
        assert LLMType.from_model_name("deepseek-chat") == LLMType.DEEPSEEK
        assert LLMType.from_model_name("deepseek-v3.2") == LLMType.DEEPSEEK

    # GLM Models
    @pytest.mark.parametrize(
        "model_name",
        [
            "glm-4",
            "glm-4-flash",
            "glm-4-air",
            "glm-4-airx",
            "glm-4-plus",
            "glm-4-long",
            "glm-4v",
            "glm-4v-plus",
            "GLM-4-FLASH",  # Uppercase
            "GLM-4",  # Uppercase
            "glm-5",  # Future version
            "glm-4.7",  # Specific version
        ],
    )
    def test_glm_models(self, model_name):
        """Test that GLM models are correctly detected."""
        assert LLMType.from_model_name(model_name) == LLMType.GLM

    def test_glm_via_vercel_gateway(self):
        """Test that GLM models accessed through Vercel gateway are correctly detected."""
        assert LLMType.from_model_name("glm-4-flash") == LLMType.GLM
        assert LLMType.from_model_name("glm-4") == LLMType.GLM

    # Kimi Models
    @pytest.mark.parametrize(
        "model_name",
        [
            "kimi-k2.5",
            "kimi-k2",
            "kimi-k1",
            "moonshot-v1",
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
            "KIMI-K2.5",  # Uppercase
            "Kimi-K2",  # Mixed case
            "kimi-k3",  # Future version
            "kimi-k2.5-vision",  # Variant
        ],
    )
    def test_kimi_models(self, model_name):
        """Test that Kimi/Moonshot models are correctly detected."""
        assert LLMType.from_model_name(model_name) == LLMType.KIMI

    def test_kimi_via_vercel_gateway(self):
        """Test that Kimi models accessed through Vercel gateway are correctly detected."""
        assert LLMType.from_model_name("kimi-k2.5") == LLMType.KIMI
        assert LLMType.from_model_name("moonshot-v1-128k") == LLMType.KIMI


class TestEnvironmentVariables:
    """Test that AGENT_MODEL and PARSING_MODEL environment variables are respected."""

    @patch("agents.prompts.prompt_factory.initialize_global_factory")
    @patch("agents.agent.MONITORING_CALLBACK")
    def test_agent_model_env_var_respected(self, mock_monitoring_callback, mock_init_factory):
        """Test that AGENT_MODEL environment variable is used by initialize_llms()."""
        from agents.llm_config import LLM_PROVIDERS, initialize_llms

        # Test with AGENT_MODEL env var set
        with patch.dict(os.environ, {"AGENT_MODEL": "gpt-4-turbo", "OPENAI_API_KEY": "test-key"}):
            with patch("agents.llm_config.LLMType.from_model_name", return_value=LLMType.GPT4):
                # Mock just the chat class creation
                original_openai_config = LLM_PROVIDERS["openai"]
                mock_llm = MagicMock()

                with patch.object(original_openai_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                    agent_llm, parsing_llm = initialize_llms()

                    # Verify initialize_llms passed env var to initialize_agent_llm
                    # First call should be for agent LLM with env var model
                    # Verify initialize_llms passed env var to initialize_agent_llm
                    # The chat class should be called twice (once for agent, once for parsing)
                    assert mock_chat_class.call_count == 2
                    # First call is for agent LLM with env var model
                    agent_call_kwargs = mock_chat_class.call_args_list[0][1]
                    assert agent_call_kwargs["model"] == "gpt-4-turbo"

    @patch("agents.llm_config.LLM_PROVIDERS")
    @patch("agents.prompts.prompt_factory.initialize_global_factory")
    def test_agent_model_override_takes_precedence(self, mock_init_factory, mock_providers):
        """Test that model_override parameter takes precedence over default in initialize_agent_llm()."""
        # Setup mock provider
        mock_config = MagicMock()
        mock_config.is_active.return_value = True
        mock_config.agent_model = "gpt-4o"  # Default model
        mock_config.agent_temperature = 0.1
        mock_config.get_api_key.return_value = "test-key"
        mock_config.get_resolved_extra_args.return_value = {}
        mock_config.chat_class = MagicMock(return_value=MagicMock())
        mock_providers.__getitem__.return_value = mock_config
        mock_providers.items.return_value = [("openai", mock_config)]

        # Test that override parameter works
        with patch("agents.llm_config.LLMType.from_model_name", return_value=LLMType.GPT4):
            llm = initialize_agent_llm(model_override="gpt-4o-mini")

            # Verify the override was used
            call_kwargs = mock_config.chat_class.call_args[1]
            assert call_kwargs["model"] == "gpt-4o-mini"

    @patch("agents.prompts.prompt_factory.initialize_global_factory")
    @patch("agents.agent.MONITORING_CALLBACK")
    def test_agent_model_defaults_when_no_env_var(self, mock_monitoring_callback, mock_init_factory):
        """Test that default model is used when AGENT_MODEL env var is not set in initialize_llms()."""
        from agents.llm_config import LLM_PROVIDERS, initialize_llms

        # Test without AGENT_MODEL env var
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with patch("agents.llm_config.LLMType.from_model_name", return_value=LLMType.GPT4):
                # Mock just the chat class creation
                original_openai_config = LLM_PROVIDERS["openai"]
                mock_llm = MagicMock()

                with patch.object(original_openai_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                    agent_llm, parsing_llm = initialize_llms()

                    # Verify the default was used (gpt-4o is the default for OpenAI)
                    # First call is for agent LLM
                    agent_call_kwargs = mock_chat_class.call_args_list[0][1]
                    assert agent_call_kwargs["model"] == "gpt-4o"

    @patch("agents.prompts.prompt_factory.initialize_global_factory")
    @patch("agents.agent.MONITORING_CALLBACK")
    def test_parsing_model_env_var_respected(self, mock_monitoring_callback, mock_init_factory):
        """Test that PARSING_MODEL environment variable is used by initialize_llms()."""
        from agents.llm_config import LLM_PROVIDERS, initialize_llms

        # Test with PARSING_MODEL env var set
        with patch.dict(os.environ, {"PARSING_MODEL": "gpt-3.5-turbo", "OPENAI_API_KEY": "test-key"}):
            with patch("agents.llm_config.LLMType.from_model_name", return_value=LLMType.GPT4):
                # Mock just the chat class creation
                original_openai_config = LLM_PROVIDERS["openai"]
                mock_llm = MagicMock()

                with patch.object(original_openai_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                    agent_llm, parsing_llm = initialize_llms()

                    # Verify the chat class was called twice (agent + parsing)
                    assert mock_chat_class.call_count == 2
                    # Second call is for parsing LLM with env var model
                    parsing_call_kwargs = mock_chat_class.call_args_list[1][1]
                    assert parsing_call_kwargs["model"] == "gpt-3.5-turbo"

    @patch("agents.llm_config.LLM_PROVIDERS")
    def test_parsing_model_override_takes_precedence(self, mock_providers):
        """Test that model_override parameter takes precedence over default in initialize_parsing_llm()."""
        # Setup mock provider
        mock_config = MagicMock()
        mock_config.is_active.return_value = True
        mock_config.parsing_model = "gpt-4o-mini"  # Default parsing model
        mock_config.parsing_temperature = 0
        mock_config.get_api_key.return_value = "test-key"
        mock_config.get_resolved_extra_args.return_value = {}
        mock_config.chat_class = MagicMock(return_value=MagicMock())
        mock_providers.__getitem__.return_value = mock_config
        mock_providers.items.return_value = [("openai", mock_config)]

        # Test that override parameter works
        llm = initialize_parsing_llm(model_override="gpt-4o")

        # Verify the override was used, not the default
        call_kwargs = mock_config.chat_class.call_args[1]
        assert call_kwargs["model"] == "gpt-4o"

    @patch("agents.prompts.prompt_factory.initialize_global_factory")
    @patch("agents.agent.MONITORING_CALLBACK")
    def test_parsing_model_defaults_when_no_env_var(self, mock_monitoring_callback, mock_init_factory):
        """Test that default parsing model is used when PARSING_MODEL env var is not set in initialize_llms()."""
        from agents.llm_config import LLM_PROVIDERS, initialize_llms

        # Test without PARSING_MODEL env var
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with patch("agents.llm_config.LLMType.from_model_name", return_value=LLMType.GPT4):
                # Mock just the chat class creation
                original_openai_config = LLM_PROVIDERS["openai"]
                mock_llm = MagicMock()

                with patch.object(original_openai_config, "chat_class", return_value=mock_llm) as mock_chat_class:
                    agent_llm, parsing_llm = initialize_llms()

                    # Verify the default was used (gpt-4o-mini is the default for parsing)
                    # Second call is for parsing LLM
                    parsing_call_kwargs = mock_chat_class.call_args_list[1][1]
                    assert parsing_call_kwargs["model"] == "gpt-4o-mini"


class TestMonitoringIntegration:
    """Test that model names are properly passed to monitoring callbacks."""

    @patch("agents.llm_config.LLM_PROVIDERS")
    @patch("agents.prompts.prompt_factory.initialize_global_factory")
    def test_agent_monitoring_callback_gets_model_name(self, mock_init_factory, mock_providers):
        """Test that agent's monitoring callback gets the correct model name."""
        from agents.agent import CodeBoardingAgent
        from unittest.mock import MagicMock
        from pathlib import Path
        import tempfile

        # Setup mock provider
        mock_config = MagicMock()
        mock_config.is_active.return_value = True
        mock_config.agent_model = "gpt-4o"
        mock_config.agent_temperature = 0.1
        mock_config.get_api_key.return_value = "test-key"
        mock_config.get_resolved_extra_args.return_value = {}
        mock_llm_instance = MagicMock()
        mock_config.chat_class = MagicMock(return_value=mock_llm_instance)
        mock_providers.__getitem__.return_value = mock_config
        mock_providers.items.return_value = [("openai", mock_config)]

        with patch.dict(os.environ, {"AGENT_MODEL": "gpt-4-turbo"}, clear=False):
            with patch("agents.llm_config.LLMType.from_model_name", return_value=LLMType.GPT4):
                from agents.llm_config import initialize_llms

                agent_llm, parsing_llm = initialize_llms()

                # Create an agent
                with tempfile.TemporaryDirectory() as tmpdir:
                    from static_analyzer.analysis_result import StaticAnalysisResults

                    mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
                    mock_static_analysis.call_graph = MagicMock()
                    mock_static_analysis.class_hierarchies = {}
                    mock_static_analysis.package_relations = {}
                    mock_static_analysis.references = []

                    with patch("agents.agent.create_agent"):
                        agent = CodeBoardingAgent(
                            repo_dir=Path(tmpdir),
                            static_analysis=mock_static_analysis,
                            system_message="Test",
                            agent_llm=agent_llm,
                            parsing_llm=parsing_llm,
                        )

                        # Simulate what DiagramGenerator does: set model name on agent's callback
                        agent.agent_monitoring_callback.model_name = "gpt-4-turbo"

                        # Verify the agent's monitoring callback has the correct model name
                        results = agent.get_monitoring_results()
                        assert results["model_name"] == "gpt-4-turbo"


class TestRoundRobinLLMKeys:
    def _create_dummy_model(self, **kwargs):
        return DummyChatModel(key_id=kwargs["api_key"])

    @patch("agents.prompts.prompt_factory.initialize_global_factory")
    def test_comma_separated_keys_create_model_pool(self, mock_init_factory):
        from agents.llm_config import LLM_PROVIDERS

        with patch.dict(os.environ, {"OPENAI_API_KEY": "key-a, key-b"}, clear=True):
            with patch("agents.llm_config.LLMType.from_model_name", return_value=LLMType.GPT4):
                openai_config = LLM_PROVIDERS["openai"]
                with patch.object(openai_config, "chat_class", side_effect=self._create_dummy_model):
                    llm = initialize_agent_llm()

                    assert isinstance(llm, list)
                    assert [model.invoke("ping").content for model in llm] == ["key-a", "key-b"]

    @patch("agents.prompts.prompt_factory.initialize_global_factory")
    def test_indexed_keys_without_base_env_create_model_pool(self, mock_init_factory):
        from agents.llm_config import LLM_PROVIDERS

        with patch.dict(os.environ, {"OPENAI_API_KEY_2": "key-b", "OPENAI_API_KEY_1": "key-a"}, clear=True):
            with patch("agents.llm_config.LLMType.from_model_name", return_value=LLMType.GPT4):
                openai_config = LLM_PROVIDERS["openai"]
                with patch.object(openai_config, "chat_class", side_effect=self._create_dummy_model):
                    llm = initialize_agent_llm()

                    assert isinstance(llm, list)
                    assert [model.invoke("ping").content for model in llm] == ["key-a", "key-b"]

    @patch("agents.prompts.prompt_factory.initialize_global_factory")
    def test_single_key_keeps_single_model_behavior(self, mock_init_factory):
        from agents.llm_config import LLM_PROVIDERS

        with patch.dict(os.environ, {"OPENAI_API_KEY": "only-key"}, clear=True):
            with patch("agents.llm_config.LLMType.from_model_name", return_value=LLMType.GPT4):
                openai_config = LLM_PROVIDERS["openai"]
                with patch.object(openai_config, "chat_class", side_effect=self._create_dummy_model):
                    llm = initialize_agent_llm()

                    assert not isinstance(llm, list)
                    assert llm.invoke("ping").content == "only-key"
