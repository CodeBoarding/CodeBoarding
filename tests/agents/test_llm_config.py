"""Tests for LLM configuration and model detection."""

import pytest
from agents.llm_config import detect_llm_type_from_model
from agents.prompts.prompt_factory import LLMType


class TestDetectLLMTypeFromModel:
    """Test the detect_llm_type_from_model function with various model names."""

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
        assert detect_llm_type_from_model(model_name) == LLMType.GPT4

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
        assert detect_llm_type_from_model(model_name) == LLMType.CLAUDE

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
        assert detect_llm_type_from_model(model_name) == LLMType.GEMINI_FLASH

    # Edge Cases
    def test_unknown_model_defaults_to_gemini(self):
        """Test that unknown models default to GEMINI_FLASH."""
        assert detect_llm_type_from_model("unknown-model-xyz") == LLMType.GEMINI_FLASH
        assert detect_llm_type_from_model("custom-finetuned-model") == LLMType.GEMINI_FLASH
        assert detect_llm_type_from_model("llama-3-70b") == LLMType.GEMINI_FLASH

    def test_mixed_case_detection(self):
        """Test that detection is case-insensitive."""
        assert detect_llm_type_from_model("GPT-4O") == LLMType.GPT4
        assert detect_llm_type_from_model("Claude-3-Opus") == LLMType.CLAUDE
        assert detect_llm_type_from_model("GEMINI-2.5-FLASH") == LLMType.GEMINI_FLASH

    def test_models_with_provider_prefix(self):
        """Test models that include provider prefixes (e.g., from Bedrock)."""
        assert detect_llm_type_from_model("anthropic.claude-3-sonnet-20240229-v1:0") == LLMType.CLAUDE
        assert detect_llm_type_from_model("us.anthropic.claude-3-haiku-20240307-v1:0") == LLMType.CLAUDE

    # Typo resistance (common typos should still match)
    def test_typo_claude(self):
        """Test that 'cladude' (common typo) still matches Claude."""
        # Note: Our current implementation doesn't handle typos, but we can add this if needed
        # For now, this test documents the behavior
        assert detect_llm_type_from_model("cladude") == LLMType.GEMINI_FLASH  # Falls back to default

    # Vercel Gateway Models
    def test_vercel_gateway_models(self):
        """
        Test that models accessed through Vercel gateway are correctly detected
        based on their actual model name, not the provider.
        """
        # Vercel can proxy any model, detection should work based on model name
        assert detect_llm_type_from_model("gpt-4o") == LLMType.GPT4
        assert detect_llm_type_from_model("claude-3-7-sonnet-20250219") == LLMType.CLAUDE
        assert detect_llm_type_from_model("gemini-2.5-flash") == LLMType.GEMINI_FLASH

    # Future-proofing
    def test_future_gpt_versions(self):
        """Test that future GPT versions are detected correctly."""
        assert detect_llm_type_from_model("gpt-6-nano") == LLMType.GPT4
        assert detect_llm_type_from_model("gpt-10-ultra") == LLMType.GPT4

    def test_future_claude_versions(self):
        """Test that future Claude versions are detected correctly."""
        assert detect_llm_type_from_model("claude-4-opus") == LLMType.CLAUDE
        assert detect_llm_type_from_model("claude-5-mega") == LLMType.CLAUDE

    def test_future_gemini_versions(self):
        """Test that future Gemini versions are detected correctly."""
        assert detect_llm_type_from_model("gemini-4.0-ultra") == LLMType.GEMINI_FLASH
        assert detect_llm_type_from_model("gemini-10-pro-max") == LLMType.GEMINI_FLASH

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
        assert detect_llm_type_from_model(model_name) == LLMType.DEEPSEEK

    def test_deepseek_via_vercel_gateway(self):
        """Test that DeepSeek models accessed through Vercel gateway are correctly detected."""
        assert detect_llm_type_from_model("deepseek-chat") == LLMType.DEEPSEEK
        assert detect_llm_type_from_model("deepseek-v3.2") == LLMType.DEEPSEEK

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
        assert detect_llm_type_from_model(model_name) == LLMType.GLM

    def test_glm_via_vercel_gateway(self):
        """Test that GLM models accessed through Vercel gateway are correctly detected."""
        assert detect_llm_type_from_model("glm-4-flash") == LLMType.GLM
        assert detect_llm_type_from_model("glm-4") == LLMType.GLM

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
        assert detect_llm_type_from_model(model_name) == LLMType.KIMI

    def test_kimi_via_vercel_gateway(self):
        """Test that Kimi models accessed through Vercel gateway are correctly detected."""
        assert detect_llm_type_from_model("kimi-k2.5") == LLMType.KIMI
        assert detect_llm_type_from_model("moonshot-v1-128k") == LLMType.KIMI
