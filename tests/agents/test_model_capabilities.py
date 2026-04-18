"""Hermetic unit tests for the context-window resolver. Catalogs are mocked — no network."""

import io
import json

import pytest

from agents.model_capabilities import (
    _OLLAMA_CACHE,
    ContextWindow,
    _parse_num_ctx,
    _resolve_ollama,
    get_context_window,
)

_FAKE_MODELSDEV = {
    "openai": {
        "models": {
            "gpt-5": {"limit": {"context": 400_000, "input": 272_000, "output": 128_000}},
            "gpt-4o": {"limit": {"context": 128_000, "output": 16_384}},
        }
    },
    "anthropic": {
        "models": {
            "claude-sonnet-4-5-20250929": {"limit": {"context": 200_000, "output": 64_000}},
        }
    },
    "amazon-bedrock": {
        "models": {
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0": {"limit": {"context": 200_000, "output": 64_000}},
        }
    },
    "zai": {"models": {"glm-4.6": {"limit": {"context": 204_800, "output": 131_072}}}},
    "moonshotai": {"models": {"kimi-k2.5": {"limit": {"context": 262_144, "output": 262_144}}}},
}

_FAKE_LITELLM = {
    "anthropic.claude-3-haiku-20240307-v1:0": {"max_input_tokens": 200_000, "max_output_tokens": 4_096},
}

_FAKE_OPENROUTER = {
    "anthropic/claude-opus-4-7": {
        "context_length": 1_000_000,
        "top_provider": {"max_completion_tokens": 128_000},
    },
}


@pytest.fixture
def fake_catalogs(monkeypatch):
    catalogs: dict[str, dict] = {
        "modelsdev": _FAKE_MODELSDEV,
        "litellm": _FAKE_LITELLM,
        "openrouter": _FAKE_OPENROUTER,
    }

    def fake_load(source: str) -> dict:
        return catalogs.get(source, {})

    monkeypatch.setattr("agents.model_capabilities._load", fake_load)
    # Why: isolate from ~/.codeboarding/config.toml so a developer's local override
    # doesn't shadow the catalog under test.
    monkeypatch.setattr("agents.model_capabilities._user_context_window_override", lambda: None)
    _OLLAMA_CACHE.clear()


class TestResolverPriority:
    def test_env_override_wins_over_catalog(self, fake_catalogs, monkeypatch):
        monkeypatch.setenv("CB_CTX_OPENAI_GPT_5", "500000,200000")
        assert get_context_window("openai", "gpt-5") == ContextWindow(500_000, 200_000)

    def test_env_override_single_value_uses_fallback_output(self, fake_catalogs, monkeypatch):
        monkeypatch.setenv("CB_CTX_OPENAI_GPT_5", "500000")
        cw = get_context_window("openai", "gpt-5")
        assert cw.input_tokens == 500_000
        assert cw.output_tokens == 64_000

    def test_malformed_env_override_falls_through_to_catalog(self, fake_catalogs, monkeypatch):
        # Regression: a typo like `500k` used to raise ValueError out of get_context_window.
        monkeypatch.setenv("CB_CTX_OPENAI_GPT_5", "500k")
        assert get_context_window("openai", "gpt-5").input_tokens == 272_000

    def test_fallback_when_nothing_matches(self, fake_catalogs):
        assert get_context_window("mystery", "nonexistent") == ContextWindow(256_000, 64_000)


class TestModelsdevResolution:
    def test_prefers_input_over_context(self, fake_catalogs):
        # Why: GPT-5 ships with context=400K but input=272K; we must honor the tighter cap.
        assert get_context_window("openai", "gpt-5").input_tokens == 272_000

    def test_falls_back_to_context_when_input_absent(self, fake_catalogs):
        assert get_context_window("openai", "gpt-4o").input_tokens == 128_000

    def test_bedrock_regional_prefix_matched_directly(self, fake_catalogs):
        cw = get_context_window("aws", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        assert cw.input_tokens == 200_000

    def test_slug_mapping_glm_zai(self, fake_catalogs):
        assert get_context_window("glm", "glm-4.6").input_tokens == 204_800

    def test_slug_mapping_kimi_moonshotai(self, fake_catalogs):
        assert get_context_window("kimi", "kimi-k2.5").input_tokens == 262_144


class TestLitellmResolution:
    def test_bedrock_region_stripped_for_litellm_key(self, fake_catalogs):
        # LiteLLM holds the stripped key `anthropic.claude-3-haiku-…-v1:0`;
        # resolver must strip `us.` before looking up.
        cw = get_context_window("aws", "us.anthropic.claude-3-haiku-20240307-v1:0")
        assert cw.input_tokens == 200_000


class TestOpenrouterResolution:
    def test_resolves_via_aggregator_id(self, fake_catalogs):
        cw = get_context_window("anthropic", "claude-opus-4-7")
        assert cw.input_tokens == 1_000_000
        assert cw.output_tokens == 128_000


class TestOllamaResolver:
    def test_short_circuits_without_base_url(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        _OLLAMA_CACHE.clear()
        assert _resolve_ollama("ollama", "qwen3:30b") is None

    def test_short_circuits_for_non_ollama_provider(self):
        assert _resolve_ollama("openai", "gpt-4o") is None

    def test_num_ctx_wins_over_arch_max(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://fake:11434")
        _OLLAMA_CACHE.clear()
        payload = {"parameters": 'stop "<|eot|>"\nnum_ctx 4096', "model_info": {"qwen3.context_length": 131072}}

        def fake_urlopen(req, timeout=None):
            return io.BytesIO(json.dumps(payload).encode())

        monkeypatch.setattr("agents.model_capabilities.urllib.request.urlopen", fake_urlopen)
        result = _resolve_ollama("ollama", "qwen3:30b")
        assert result == (4096, 64_000)

    def test_arch_max_used_when_num_ctx_absent(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://fake:11434")
        _OLLAMA_CACHE.clear()
        payload = {"parameters": 'stop "<|eot|>"', "model_info": {"llama.context_length": 131072}}

        def fake_urlopen(req, timeout=None):
            return io.BytesIO(json.dumps(payload).encode())

        monkeypatch.setattr("agents.model_capabilities.urllib.request.urlopen", fake_urlopen)
        assert _resolve_ollama("ollama", "llama3:8b") == (131072, 64_000)

    def test_transient_failure_is_retried_not_cached(self, monkeypatch):
        # Why: @lru_cache used to memoize None here, so a brief Ollama outage
        # silently pinned the resolver to the generic fallback for the process lifetime.
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://fake:11434")
        _OLLAMA_CACHE.clear()
        calls = {"n": 0}
        payload = {"parameters": "", "model_info": {"llama.context_length": 8192}}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionRefusedError("ollama not up yet")
            return io.BytesIO(json.dumps(payload).encode())

        monkeypatch.setattr("agents.model_capabilities.urllib.request.urlopen", fake_urlopen)
        assert _resolve_ollama("ollama", "llama3:8b") is None
        assert _resolve_ollama("ollama", "llama3:8b") == (8192, 64_000)


class TestCorruptCache:
    def test_corrupt_cache_file_triggers_refetch_instead_of_crashing(self, tmp_path, monkeypatch):
        # Regression: a half-written cache file used to crash the first resolver call
        # with a JSONDecodeError, turning a transient disk issue into a startup failure.
        from agents import model_capabilities

        # Why: clear before AND after so we don't poison the lru_cache for sibling tests.
        model_capabilities._load.cache_clear()
        try:
            cache_dir = tmp_path / ".codeboarding" / "cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "openrouter.json").write_text("{ not json ")
            monkeypatch.setattr(model_capabilities, "get_cache_dir", lambda _repo: cache_dir)

            valid_body = json.dumps({"data": [{"id": "openai/gpt-4o", "context_length": 128_000}]}).encode()

            def fake_urlopen(req, timeout=None):
                return io.BytesIO(valid_body)

            monkeypatch.setattr("agents.model_capabilities.urllib.request.urlopen", fake_urlopen)
            data = model_capabilities._load("openrouter")
            assert data["openai/gpt-4o"]["context_length"] == 128_000
        finally:
            model_capabilities._load.cache_clear()


class TestParseNumCtx:
    def test_extracts_num_ctx(self):
        assert _parse_num_ctx('stop "x"\nnum_ctx 8192\ntemperature 0.7') == 8192

    def test_returns_none_when_absent(self):
        assert _parse_num_ctx('stop "x"\ntemperature 0.7') is None

    def test_handles_empty_string(self):
        assert _parse_num_ctx("") is None

    def test_not_matched_inside_other_key(self):
        # `some_num_ctx 2048` must not match.
        assert _parse_num_ctx("some_num_ctx 2048") is None
