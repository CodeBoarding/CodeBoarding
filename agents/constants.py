"""Constants for the agents module."""


class LLMDefaults:
    DEFAULT_AGENT_TEMPERATURE = 0
    DEFAULT_PARSING_TEMPERATURE = 0
    AWS_MAX_TOKENS = 4096


class FileStructureConfig:
    MAX_LINES = 500
    DEFAULT_MAX_DEPTH = 10
    FALLBACK_MAX_LINES = 50000


class ModelCapabilities:
    FALLBACK_INPUT = 256_000
    FALLBACK_OUTPUT = 64_000
    CACHE_TTL_SECONDS = 24 * 3600
    CHARS_PER_TOKEN = 3.5  # community consensus conversion is around 3 or 4 chars/token.

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
