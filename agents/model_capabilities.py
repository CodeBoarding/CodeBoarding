import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agents.constants import ModelCapabilities
from utils import get_cache_dir

logger = logging.getLogger(__name__)

_BEDROCK_REGION = re.compile(r"^(us|eu|apac|global|au|ca|us-gov)\.")

# Why: cached by hand (not via @lru_cache) so a transient Ollama outage -- user starts the
# app before `ollama serve` is up -- doesn't memoize None for the rest of the process.
_OLLAMA_CACHE: dict[tuple[str, str], tuple[int, int]] = {}

_PRESET_RE = re.compile(r"^@preset/([A-Za-z0-9_.-]+)$")

# Why: presets are mutable server-side, so cache per-process only. Network errors are
# not cached (transient); HTTP errors are (bad slug / key without preset read access).
_PRESET_CACHE: dict[str, list[str]] = {}


@dataclass(frozen=True)
class ContextWindow:
    input_tokens: int
    output_tokens: int
    is_fallback: bool = False


def get_context_window(provider: str, model_name: str) -> ContextWindow:
    hit = _run_resolvers(provider, model_name)
    if hit is not None:
        return ContextWindow(*hit)

    preset_window = _resolve_preset_window(provider, model_name)
    if preset_window is not None:
        return preset_window

    logger.warning(f"No context window for {provider}/{model_name}; using fallback {ModelCapabilities.FALLBACK_INPUT}")
    return ContextWindow(ModelCapabilities.FALLBACK_INPUT, ModelCapabilities.FALLBACK_OUTPUT, is_fallback=True)


def _run_resolvers(provider: str, model_name: str) -> tuple[int, int] | None:
    resolvers = (
        _resolve_env,
        _resolve_user_config,
        _resolve_ollama,
        _resolve_modelsdev,
        _resolve_litellm,
        _resolve_openrouter,
    )
    for resolver in resolvers:
        hit = resolver(provider, model_name)
        if hit is not None:
            return hit
    return None


def _resolve_preset_window(provider: str, model_name: str) -> ContextWindow | None:
    """Window for an OpenRouter ``@preset/...`` reference via its underlying models.

    Why min: a preset may route across models with different windows; promise only
    the smallest so no candidate can overflow.
    """
    hits: list[tuple[int, int]] = []
    unresolved: list[str] = []
    for underlying in _openrouter_preset_models(provider, model_name):
        hit = _run_resolvers(provider, underlying)
        if hit is not None:
            hits.append(hit)
        else:
            unresolved.append(underlying)
    if not hits:
        return None
    if unresolved:
        logger.warning(f"Preset {model_name}: no window for {unresolved}; using min over the resolved models")
    return ContextWindow(min(inp for inp, _ in hits), min(out for _, out in hits))


def _openrouter_preset_models(provider: str, model_name: str) -> list[str]:
    """Model ids configured in an OpenRouter preset, or [] when not a preset reference."""
    if provider != "openrouter":
        return []
    match = _PRESET_RE.match(model_name)
    if not match:
        return []
    slug = match.group(1)
    cached = _PRESET_CACHE.get(slug)
    if cached is not None:
        return cached

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return []
    base_url = (os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
    try:
        req = urllib.request.Request(f"{base_url}/presets/{slug}", headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=3) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        logger.warning(f"OpenRouter preset lookup failed for {model_name} (HTTP {e.code})")
        _PRESET_CACHE[slug] = []
        return []
    except Exception as e:
        logger.warning(f"OpenRouter preset lookup failed for {model_name} ({e})")
        return []

    models = _extract_preset_models(payload)
    if not models:
        logger.warning(f"OpenRouter preset {model_name} lists no models")
    _PRESET_CACHE[slug] = models
    return models


def _extract_preset_models(payload: dict) -> list[str]:
    # The active config lives under data.designated_version.config; `model` is a single
    # id, `models` a routing/fallback array. Either or both may be present.
    data = payload.get("data") or {}
    config = (data.get("designated_version") or {}).get("config") or data.get("config") or {}
    models: list[str] = []
    for candidate in [config.get("model"), *(config.get("models") or [])]:
        if isinstance(candidate, str) and candidate and candidate not in models:
            models.append(candidate)
    return models


def _resolve_env(provider: str, model_name: str) -> tuple[int, int] | None:
    key = f"CB_CTX_{provider.upper()}_{re.sub(r'[^A-Z0-9]', '_', model_name.upper())}"
    val = os.getenv(key)
    if not val:
        return None
    parts = val.split(",")
    try:
        inp = int(parts[0])
        out = int(parts[1]) if len(parts) > 1 else ModelCapabilities.FALLBACK_OUTPUT
    except ValueError as e:
        # Why: env layer is an escape hatch; a typo like `500k` must fall through
        # to the next resolver, not crash the whole call out of get_context_window.
        logger.warning(f"Ignoring malformed {key}={val!r} ({e})")
        return None
    return inp, out


def _resolve_user_config(provider: str, model_name: str) -> tuple[int, int] | None:
    # Why: lets users pin a window via `[llm] context_window = N` in ~/.codeboarding/config.toml
    # when catalogs are wrong or a model is private. Global scalar — applies to every provider/model.
    cw = _user_context_window_override()
    if cw is None:
        return None
    return cw, ModelCapabilities.FALLBACK_OUTPUT


@lru_cache(maxsize=1)
def _user_context_window_override() -> int | None:
    # Why: delayed import avoids a module-load cycle; lru_cache avoids re-parsing
    # config.toml on every get_context_window call.
    from user_config import load_user_config

    return load_user_config().llm.context_window


def _resolve_ollama(provider: str, model_name: str) -> tuple[int, int] | None:
    if provider != "ollama":
        return None
    base = os.getenv("OLLAMA_BASE_URL")
    if not base:
        return None
    return _ollama_show(model_name, base.rstrip("/"))


def _ollama_show(model_name: str, base_url: str) -> tuple[int, int] | None:
    key = (model_name, base_url)
    cached = _OLLAMA_CACHE.get(key)
    if cached is not None:
        return cached
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
    result = (ctx, ModelCapabilities.FALLBACK_OUTPUT)
    _OLLAMA_CACHE[key] = result
    return result


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


@lru_cache(maxsize=4)
def _load(source: str) -> dict:
    # Why: each resolver calls _load on every lookup; without memoization, 100 models = ~300
    # parses of the same multi-MB JSON. On-disk TTL still applies on first hit per process.
    path = get_cache_dir(Path.cwd()) / f"{source}.json"
    if path.exists() and time.time() - path.stat().st_mtime < ModelCapabilities.CACHE_TTL_SECONDS:
        cached = _read_cache(path)
        if cached is not None:
            return cached
    try:
        # Why: models.dev rejects urllib's default UA with 403.
        req = urllib.request.Request(ModelCapabilities.SOURCES[source], headers={"User-Agent": "codeboarding/1.0"})
        with urllib.request.urlopen(req, timeout=2) as r:
            raw = json.load(r)
        data = _normalize(source, raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data
    except Exception as e:
        logger.warning(f"{source} fetch failed ({e}); using stale cache or skipping")
        return _read_cache(path) or {}


def _read_cache(path: Path) -> dict | None:
    # Why: a half-written or disk-full cache file must not crash the resolver
    # on every startup. Treat unreadable cache as "no cache" -- next call refetches.
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Cache read failed for {path.name} ({e}); treating as missing")
        return None


def _normalize(source: str, raw: dict) -> dict:
    if source == "openrouter":
        return {m["id"]: m for m in raw.get("data", [])}
    if source == "litellm":
        return {k: v for k, v in raw.items() if isinstance(v, dict)}
    return raw
