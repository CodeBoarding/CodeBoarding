from dataclasses import dataclass

from agents.constants import ModelCapabilities

OUTPUT_HEADROOM_TOKENS = 8_000
CONTEXT_MARGIN = 0.9


@dataclass(frozen=True)
class ClusterPromptBudget:
    """Character budget for the full rendered ``cfg_clusters`` prompt slot."""

    input_tokens: int
    output_headroom_tokens: int = OUTPUT_HEADROOM_TOKENS
    chars_per_token: float = ModelCapabilities.CHARS_PER_TOKEN
    margin: float = CONTEXT_MARGIN

    def available_chars(self, prompt_overhead_chars: int) -> int:
        prompt_overhead_tokens = prompt_overhead_chars / self.chars_per_token
        available_tokens = (self.input_tokens - self.output_headroom_tokens - prompt_overhead_tokens) * self.margin
        return int(available_tokens * self.chars_per_token)
