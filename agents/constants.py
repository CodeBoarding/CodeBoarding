"""Constants for the agents module."""


class LLMDefaults:
    DEFAULT_AGENT_TEMPERATURE = 0.1
    DEFAULT_PARSING_TEMPERATURE = 0
    AWS_MAX_TOKENS = 4096


class FileStructureConfig:
    MAX_LINES = 500
    DEFAULT_MAX_DEPTH = 10
    FALLBACK_MAX_LINES = 50000
