import json
import logging
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agents.constants import ModelCapabilities

logger = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".codeboarding" / "cache"

# Escape hatch for catalog bugs or private IDs no catalog covers. Empty by design —
# models.dev resolves the previously-suspected overrides (gpt-5, kimi-k2.5, glm-4.6,
# gpt-oss-120b) correctly when we prefer limit.input over limit.context.
_OVERRIDES: dict[tuple[str, str], tuple[int, int]] = {}

_BEDROCK_REGION = re.compile(r"^(us|eu|apac|global|au|ca|us-gov)\.")


@dataclass(frozen=True)
class ContextWindow:
    input: int
    output: int


def get_context_window(provider: str, model_name: str) -> ContextWindow:
    resolvers = (
        _resolve_env,
        _resolve_override,
        _resolve_ollama,
        _resolve_modelsdev,
        _resolve_litellm,
        _resolve_openrouter,
    )
    for resolver in resolvers:
        hit = resolver(provider, model_name)
        if hit is not None:
            return ContextWindow(*hit)
    logger.warning(f"No context window for {provider}/{model_name}; using fallback {ModelCapabilities.FALLBACK_INPUT}")
    return ContextWindow(ModelCapabilities.FALLBACK_INPUT, ModelCapabilities.FALLBACK_OUTPUT)


def _resolve_env(provider: str, model_name: str) -> tuple[int, int] | None:
    key = f"CB_CTX_{provider.upper()}_{re.sub(r'[^A-Z0-9]', '_', model_name.upper())}"
    val = os.getenv(key)
    if not val:
        return None
    parts = val.split(",")
    inp = int(parts[0])
    out = int(parts[1]) if len(parts) > 1 else ModelCapabilities.FALLBACK_OUTPUT
    return inp, out


def _resolve_override(provider: str, model_name: str) -> tuple[int, int] | None:
    return _OVERRIDES.get((provider, model_name))


def _resolve_ollama(provider: str, model_name: str) -> tuple[int, int] | None:
    if provider != "ollama":
        return None
    base = os.getenv("OLLAMA_BASE_URL")
    if not base:
        return None
    return _ollama_show(model_name, base.rstrip("/"))


@lru_cache(maxsize=64)
def _ollama_show(model_name: str, base_url: str) -> tuple[int, int] | None:
    try:
        req = urllib.request.Request(
            f"{base_url}/api/show",
            data=json.dumps({"model": model_name}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            info = json.load(r)
    except Exception as e:
        logger.warning(f"Ollama /api/show failed for {model_name} ({e})")
        return None
    num_ctx = _parse_num_ctx(info.get("parameters") or "")
    arch_max = next(
        (int(v) for k, v in (info.get("model_info") or {}).items() if k.endswith(".context_length")),
        None,
    )
    ctx = num_ctx or arch_max
    if not ctx:
        return None
    # Why: num_ctx < arch_max means Ollama silently truncates beyond num_ctx tokens.
    # Tell the user so they can bump PARAMETER num_ctx in their Modelfile.
    if num_ctx and arch_max and num_ctx < arch_max:
        logger.info(f"{model_name}: num_ctx={num_ctx} < arch_max={arch_max}; Ollama will truncate")
    return ctx, ModelCapabilities.FALLBACK_OUTPUT


def _parse_num_ctx(params: str) -> int | None:
    # Extracts the `num_ctx N` runtime cap from an Ollama Modelfile parameters blob.
    m = re.search(r"^num_ctx\s+(\d+)", params, re.MULTILINE)
    return int(m.group(1)) if m else None


def _resolve_modelsdev(provider: str, model_name: str) -> tuple[int, int] | None:
    data = _load("modelsdev")
    slug = ModelCapabilities.MODELSDEV_SLUG.get(provider, provider)
    entry = data.get(slug, {}).get("models", {}).get(model_name)
    if not entry:
        return None
    limit = entry.get("limit") or {}
    # Why: models.dev splits total context from real input cap for models like GPT-5
    # (context=400K, input=272K). Prefer `input` so we never over-promise the window.
    inp = limit.get("input") or limit.get("context")
    if not inp:
        return None
    return int(inp), int(limit.get("output") or ModelCapabilities.FALLBACK_OUTPUT)


def _resolve_litellm(provider: str, model_name: str) -> tuple[int, int] | None:
    data = _load("litellm")
    base = _BEDROCK_REGION.sub("", model_name) if provider == "aws" else model_name
    for key in (base, f"{provider}/{base}", f"bedrock/{base}"):
        entry = data.get(key)
        if not entry:
            continue
        inp = entry.get("max_input_tokens") or entry.get("max_tokens")
        out = entry.get("max_output_tokens") or entry.get("max_tokens")
        if inp:
            return int(inp), int(out or ModelCapabilities.FALLBACK_OUTPUT)
    return None


def _resolve_openrouter(provider: str, model_name: str) -> tuple[int, int] | None:
    data = _load("openrouter")
    entry = data.get(_openrouter_id(provider, model_name))
    if not entry:
        return None
    ctx = entry.get("context_length")
    if not ctx:
        return None
    out = (entry.get("top_provider") or {}).get("max_completion_tokens")
    return int(ctx), int(out or ModelCapabilities.FALLBACK_OUTPUT)


def _openrouter_id(provider: str, model_name: str) -> str:
    if provider == "aws" and "anthropic." in model_name:
        stripped = _BEDROCK_REGION.sub("", model_name).removeprefix("anthropic.").removesuffix("-v1:0")
        return f"anthropic/{stripped}"
    if "/" in model_name:
        return model_name
    return f"{ModelCapabilities.OPENROUTER_PREFIX.get(provider, provider)}/{model_name}"


def _load(source: str) -> dict:
    path = _CACHE_DIR / f"{source}.json"
    if path.exists() and time.time() - path.stat().st_mtime < ModelCapabilities.CACHE_TTL_SECONDS:
        return json.loads(path.read_text())
    try:
        # Why: models.dev rejects urllib's default UA with 403.
        req = urllib.request.Request(ModelCapabilities.SOURCES[source], headers={"User-Agent": "codeboarding/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = json.load(r)
        data = _normalize(source, raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data
    except Exception as e:
        logger.warning(f"{source} fetch failed ({e}); using stale cache or skipping")
        return json.loads(path.read_text()) if path.exists() else {}


def _normalize(source: str, raw: dict) -> dict:
    if source == "openrouter":
        return {m["id"]: m for m in raw.get("data", [])}
    if source == "litellm":
        return {k: v for k, v in raw.items() if isinstance(v, dict)}
    return raw
