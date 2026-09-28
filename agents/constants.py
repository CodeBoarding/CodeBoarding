"""Constants for the agents module."""


class LLMDefaults:
    DEFAULT_AGENT_TEMPERATURE = 0
    AWS_MAX_TOKENS = 4096
    # Why: the provider configs below pass this straight to the client. ``None`` there disables the
    # SDK's own default, so a stalled socket hangs the whole analysis with nothing to interrupt it.
    REQUEST_TIMEOUT_SECONDS = 300.0


class ModelCapabilities:
    FALLBACK_INPUT = 256_000
    FALLBACK_OUTPUT = 64_000
    CACHE_TTL_SECONDS = 24 * 3600
    CHARS_PER_TOKEN = 3.5  # community consensus conversion is around 3 or 4 chars/token.

    # Input/output limits from models.dev, checked 2026-09-29; live catalogs take precedence.
    KNOWN_WINDOWS = {
        "anthropic/claude-opus-4-5": (200_000, 64_000),
        "anthropic/claude-opus-4-5-20251101": (200_000, 64_000),
        "anthropic/claude-opus-4-6": (1_000_000, 128_000),
        "anthropic/claude-opus-4-7": (1_000_000, 128_000),
        "anthropic/claude-opus-4-8": (1_000_000, 128_000),
        "anthropic/claude-opus-5": (1_000_000, 128_000),
        "anthropic/claude-opus-5-5": (1_000_000, 128_000),
        "openai/gpt-5": (272_000, 128_000),
        "openai/gpt-5-mini": (272_000, 128_000),
        "openai/gpt-5-nano": (272_000, 128_000),
        "openai/gpt-5-pro": (272_000, 272_000),
        "openai/gpt-5.1": (272_000, 128_000),
        "openai/gpt-5.2": (272_000, 128_000),
        "openai/gpt-5.2-pro": (272_000, 128_000),
        "openai/gpt-5.2-chat-latest": (128_000, 16_384),
        "openai/gpt-5.3-chat-latest": (128_000, 16_384),
        "openai/gpt-5.3-codex": (272_000, 128_000),
        "openai/gpt-5.3-codex-spark": (100_000, 32_000),
        "openai/gpt-5.4": (922_000, 128_000),
        "openai/gpt-5.4-pro": (922_000, 128_000),
        "openai/gpt-5.4-mini": (272_000, 128_000),
        "openai/gpt-5.4-nano": (272_000, 128_000),
        "openai/gpt-5.5": (922_000, 128_000),
        "openai/gpt-5.5-pro": (922_000, 128_000),
        "openai/gpt-5.6": (922_000, 128_000),
        "openai/gpt-5.6-luna": (922_000, 128_000),
        "openai/gpt-5.6-sol": (922_000, 128_000),
        "openai/gpt-5.6-terra": (922_000, 128_000),
        "openai/gpt-6-astra": (922_000, 128_000),
        "openai/gpt-6-luna": (922_000, 128_000),
        "openai/gpt-6-sol": (922_000, 128_000),
    }

    SOURCES = {
        "litellm": "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
        "modelsdev": "https://models.dev/api.json",
        "openrouter": "https://openrouter.ai/api/v1/models",
    }

    # models.dev uses slugs that diverge from our internal provider names.
    MODELSDEV_SLUG = {
        "aws": "amazon-bedrock",
        "kimi": "moonshotai",
        "glm": "zai",
    }

    OPENROUTER_PREFIX = {
        "kimi": "moonshotai",
        "glm": "z-ai",
    }
