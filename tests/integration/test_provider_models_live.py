"""Live, keyless check that every model in LLM_PROVIDERS exists in a public catalog.

Most providers are validated against ``models.dev`` (the same keyless aggregator this repo
already uses in ``_resolve_modelsdev``); the two gateways serve their own OpenAI-style
``/models`` endpoint publicly. Nothing here needs an API key, so it runs in any environment
including fork CI. Expected model IDs are read straight from ``LLM_PROVIDERS``, so the test
follows config changes without edits.

A models.dev MISSING means the configured ID is absent from the aggregator — usually a stale
or renamed model, occasionally just aggregator lag; either way it's worth investigating.

Skipped by the default ``pytest --ignore=tests/integration`` run; invoke explicitly with
``pytest tests/integration/test_provider_models_live.py`` to catch model-name drift.
"""

import json
import urllib.error
import urllib.request
from functools import lru_cache

import pytest

from agents.llm_config import LLM_PROVIDERS

_UA = {"User-Agent": "codeboarding-tests"}  # models.dev 403s without a User-Agent.

# LLM_PROVIDERS provider name -> models.dev provider key(s) whose model sets are unioned.
_MODELSDEV_KEYS = {
    "openai": ["openai"],
    "anthropic": ["anthropic"],
    "google": ["google"],
    "cerebras": ["cerebras"],
    "deepseek": ["deepseek"],
    "glm": ["zhipuai"],
    "kimi": ["moonshotai", "moonshotai-cn"],
}

# Gateways carry provider-prefixed slugs (e.g. google/gemini-3-flash) that models.dev does not
# catalog, so they are checked against their own public, keyless /models endpoint instead.
_GATEWAY_CATALOGS = {
    "vercel": "https://ai-gateway.vercel.sh/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
}

# Ollama is local-only (no public catalog) and is intentionally excluded.


def _fetch_json(url: str) -> object | None:
    """GET and parse JSON, or None when the endpoint is unreachable."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=15) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


@lru_cache(maxsize=1)
def _modelsdev_catalog() -> dict | None:
    payload = _fetch_json("https://models.dev/api.json")
    return payload if isinstance(payload, dict) else None


def _modelsdev_ids(provider: str) -> frozenset[str] | None:
    catalog = _modelsdev_catalog()
    if catalog is None:
        return None
    ids: set[str] = set()
    for key in _MODELSDEV_KEYS[provider]:
        ids |= set(catalog.get(key, {}).get("models", {}))
    return frozenset(ids)


@lru_cache(maxsize=len(_GATEWAY_CATALOGS))
def _gateway_ids(provider: str) -> frozenset[str] | None:
    payload = _fetch_json(_GATEWAY_CATALOGS[provider])
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return frozenset(m["id"] for m in data) if data else None


def _catalog_ids(provider: str) -> frozenset[str] | None:
    return _gateway_ids(provider) if provider in _GATEWAY_CATALOGS else _modelsdev_ids(provider)


def _expected_pairs() -> list[tuple[str, str, str]]:
    """(provider, role, model) for every agent/parsing model with a keyless catalog."""
    covered = set(_MODELSDEV_KEYS) | set(_GATEWAY_CATALOGS)
    pairs = []
    for name, config in LLM_PROVIDERS.items():
        if name not in covered:
            continue
        pairs.append((name, "agent", config.agent_model))
        pairs.append((name, "parsing", config.parsing_model))
    return pairs


@pytest.mark.parametrize("provider,role,model", _expected_pairs(), ids=lambda v: v)
def test_configured_model_exists_in_catalog(provider, role, model):
    ids = _catalog_ids(provider)
    if ids is None:
        pytest.skip(f"{provider} catalog unreachable (offline)")
    assert model in ids, (
        f"{provider} {role}_model '{model}' not found in live catalog ({len(ids)} models). "
        f"Check agents/llm_config.py against the provider's current model list."
    )


def test_catalogs_are_reachable():
    """Guard: if every catalog is offline the suite silently no-ops, so require at least one."""
    providers = set(_MODELSDEV_KEYS) | set(_GATEWAY_CATALOGS)
    reachable = [p for p in providers if _catalog_ids(p) is not None]
    assert reachable, "No public catalog (models.dev, OpenRouter, Vercel) was reachable; cannot validate any model."
