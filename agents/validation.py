"""Shared scoring for structured LLM output validation."""

from collections.abc import Callable
from dataclasses import dataclass, field

VALIDATOR_WEIGHTS: dict[str, float] = {}
DEFAULT_VALIDATOR_WEIGHT = 5.0


@dataclass
class ValidationResult:
    """Result of a validation check."""

    is_valid: bool
    feedback_messages: list[str] = field(default_factory=list)
    score: float = 0.0


def score_validation_results(validator_results: list[tuple[Callable, ValidationResult]]) -> float:
    """Compute the weighted score for a set of validation results."""
    return sum(
        VALIDATOR_WEIGHTS.get(validator.__name__, DEFAULT_VALIDATOR_WEIGHT)
        * (1.0 if result.is_valid else max(0.0, min(1.0, result.score)))
        for validator, result in validator_results
    )
