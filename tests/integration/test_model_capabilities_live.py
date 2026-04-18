"""Live-catalog smoke test: resolves 100 (provider, model) pairs against real models.dev / litellm / openrouter.

Skipped by the default `pytest --ignore=tests/integration` invocation in CLAUDE.md; run explicitly with
`pytest tests/integration/test_model_capabilities_live.py` to spot catalog drift or check coverage for new models.
"""

import pytest

from agents.model_capabilities import (
    _resolve_env,
    _resolve_litellm,
    _resolve_modelsdev,
    _resolve_ollama,
    _resolve_openrouter,
    _resolve_user_config,
    get_context_window,
)

_RESOLVERS = (
    _resolve_env,
    _resolve_user_config,
    _resolve_ollama,
    _resolve_modelsdev,
    _resolve_litellm,
    _resolve_openrouter,
)


def _resolved(provider: str, model: str) -> bool:
    # Why: we can't detect fallback via input == 128_000 because real models (gpt-4o) hit 128_000.
    return any(fn(provider, model) is not None for fn in _RESOLVERS)


_MIN_MODERN_INPUT = 8_000

# (provider, model, min_expected_input). `min_expected_input` is a lower bound — the resolver
# may return more (e.g. a catalog bumps Claude Sonnet from 200K to 1M) without failing the test.
_TRICKY_CASES = [
    ("openai", "gpt-5", 272_000),
    ("openai", "gpt-5-mini", 272_000),
    ("openai", "gpt-4o", 128_000),
    ("openai", "gpt-4o-mini", 128_000),
    ("openai", "gpt-4.1", 128_000),
    ("openai", "o1", 128_000),
    ("openai", "o3-mini", 128_000),
    ("anthropic", "claude-sonnet-4-5-20250929", 200_000),
    ("anthropic", "claude-3-haiku-20240307", 200_000),
    ("anthropic", "claude-opus-4-5-20251101", 200_000),
    ("anthropic", "claude-haiku-4-5-20251001", 200_000),
    ("aws", "us.anthropic.claude-sonnet-4-5-20250929-v1:0", 200_000),
    ("aws", "us.anthropic.claude-3-haiku-20240307-v1:0", 200_000),
    ("aws", "eu.anthropic.claude-sonnet-4-20250514-v1:0", 200_000),
    ("aws", "anthropic.claude-opus-4-1-20250805-v1:0", 200_000),
    ("aws", "anthropic.claude-3-5-sonnet-20241022-v2:0", 200_000),
    ("cerebras", "gpt-oss-120b", 131_072),
    ("cerebras", "llama3.1-8b", 8_000),
    ("deepseek", "deepseek-chat", 100_000),
    ("kimi", "kimi-k2.5", 250_000),
    ("glm", "glm-4.6", 200_000),
    ("google", "gemini-2.5-pro", 1_000_000),
    ("google", "gemini-2.5-flash", 1_000_000),
    ("google", "gemini-1.5-pro", 1_000_000),
    ("openrouter", "google/gemini-2.5-flash", 1_000_000),
    ("openrouter", "meta-llama/llama-3.1-70b-instruct", 100_000),
    ("openrouter", "qwen/qwen3-32b", 32_000),
    ("openrouter", "x-ai/grok-4", 100_000),
    ("openrouter", "mistralai/mistral-large", 32_000),
    ("openrouter", "deepseek/deepseek-r1", 64_000),
]

# Additional well-known pairs across all providers. Kept live by pruning renamed/retired IDs —
# this suite's purpose is "does the catalog resolve current models," not archaeology.
_POPULAR_PAIRS = [
    # OpenAI family
    ("openai", "gpt-5.1"),
    ("openai", "gpt-5.2"),
    ("openai", "gpt-4-turbo"),
    ("openai", "gpt-3.5-turbo"),
    ("openai", "o1-mini"),
    ("openai", "o3"),
    # Anthropic direct
    ("anthropic", "claude-3-5-sonnet-20241022"),
    ("anthropic", "claude-3-5-haiku-20241022"),
    ("anthropic", "claude-sonnet-4-20250514"),
    ("anthropic", "claude-opus-4-20250514"),
    # Bedrock
    ("aws", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
    ("aws", "anthropic.claude-haiku-4-5-20251001-v1:0"),
    ("aws", "us.anthropic.claude-opus-4-5-20251101-v1:0"),
    ("aws", "amazon.nova-pro-v1:0"),
    ("aws", "amazon.nova-lite-v1:0"),
    ("aws", "meta.llama3-70b-instruct-v1:0"),
    # Google
    ("google", "gemini-2.0-flash"),
    ("google", "gemini-1.5-flash"),
    # OpenRouter aggregator
    ("openrouter", "anthropic/claude-3.5-sonnet"),
    ("openrouter", "openai/gpt-4o"),
    ("openrouter", "openai/gpt-4o-mini"),
    ("openrouter", "meta-llama/llama-3.3-70b-instruct"),
    ("openrouter", "qwen/qwen-2.5-72b-instruct"),
    ("openrouter", "deepseek/deepseek-chat"),
    ("openrouter", "deepseek/deepseek-v3"),
    ("openrouter", "nvidia/llama-3.1-nemotron-70b-instruct"),
    ("openrouter", "amazon/nova-pro-v1"),
    # Moonshot / Zhipu / Cerebras / DeepSeek long-tail
    ("kimi", "kimi-k2-0711-preview"),
    ("kimi", "kimi-k2-turbo-preview"),
    ("glm", "glm-4.5"),
    ("glm", "glm-4.7"),
    ("cerebras", "qwen-3-235b-a22b-instruct-2507"),
    ("deepseek", "deepseek-reasoner"),
]


@pytest.mark.parametrize("provider,model,min_input", _TRICKY_CASES)
def test_tricky_case_meets_minimum(provider, model, min_input):
    cw = get_context_window(provider, model)
    assert cw.input_tokens >= min_input, f"{provider}/{model} returned {cw.input_tokens}, expected >= {min_input}"
    assert _resolved(provider, model), f"{provider}/{model} hit the generic fallback"


@pytest.mark.parametrize("provider,model", _POPULAR_PAIRS)
def test_popular_pair_resolves(provider, model):
    cw = get_context_window(provider, model)
    assert cw.input_tokens >= _MIN_MODERN_INPUT, f"{provider}/{model} returned implausible input={cw.input_tokens}"


def test_total_coverage_across_tricky_and_popular():
    """Sanity bound: across 100 real-world pairs, < 10% should hit the generic fallback."""
    pairs = [(p, m) for p, m, _ in _TRICKY_CASES] + list(_POPULAR_PAIRS)
    fallbacks = [pm for pm in pairs if not _resolved(*pm)]
    assert len(fallbacks) / len(pairs) < 0.10, f"High fallback rate: {fallbacks}"
