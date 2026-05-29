import pytest

from agents import llm_config
from agents.llm_config import configure_models, get_agent_timeout, get_parsing_timeout


@pytest.fixture(autouse=True)
def reset_overrides():
    """Restore module-level timeout overrides after each test."""
    yield
    configure_models()


class TestAgentTimeout:
    def test_defaults_to_300_first_600_retry_when_unset(self):
        configure_models()

        assert get_agent_timeout(0) == 300
        assert get_agent_timeout(1) == 600
        assert get_agent_timeout(2) == 600

    def test_flat_override_applies_to_every_attempt(self):
        configure_models(agent_timeout_s=1200)

        assert get_agent_timeout(0) == 1200
        assert get_agent_timeout(1) == 1200
        assert get_agent_timeout(2) == 1200


class TestParsingTimeout:
    def test_none_when_unset(self):
        configure_models()

        assert get_parsing_timeout() is None

    def test_returns_configured_value(self):
        configure_models(parsing_timeout_s=900)

        assert get_parsing_timeout() == 900

    def test_injected_into_parsing_client_kwargs(self, monkeypatch):
        """parsing_timeout_s must reach the chat client as timeout kwarg."""
        captured = {}

        class FakeChat:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        cfg = llm_config.LLM_PROVIDERS["openai"]
        monkeypatch.setattr(cfg, "chat_class", FakeChat)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        configure_models(parsing_timeout_s=900)

        llm_config.initialize_parsing_llm("some-model")

        assert captured.get("timeout") == 900

    def test_no_timeout_kwarg_override_when_unset(self, monkeypatch):
        captured = {}

        class FakeChat:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        cfg = llm_config.LLM_PROVIDERS["openai"]
        monkeypatch.setattr(cfg, "chat_class", FakeChat)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        configure_models()

        llm_config.initialize_parsing_llm("some-model")

        # extra_args carries timeout=None for openai; unset override must not change it.
        assert captured.get("timeout") is None
