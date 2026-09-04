import unittest

from agents.validation import (
    DEFAULT_VALIDATOR_WEIGHT,
    VALIDATOR_WEIGHTS,
    ValidationResult,
    score_validation_results,
)


def important_validator() -> None:
    pass


class TestValidationResult(unittest.TestCase):
    def test_defaults_to_no_feedback_and_zero_score(self) -> None:
        result = ValidationResult(is_valid=True)

        self.assertEqual(result.feedback_messages, [])
        self.assertEqual(result.score, 0.0)


class TestValidationScoring(unittest.TestCase):
    def tearDown(self) -> None:
        VALIDATOR_WEIGHTS.clear()

    def test_valid_result_receives_full_default_weight(self) -> None:
        score = score_validation_results([(important_validator, ValidationResult(True))])

        self.assertEqual(score, DEFAULT_VALIDATOR_WEIGHT)

    def test_invalid_result_receives_partial_weight(self) -> None:
        VALIDATOR_WEIGHTS[important_validator.__name__] = 8.0

        score = score_validation_results([(important_validator, ValidationResult(False, score=0.75))])

        self.assertEqual(score, 6.0)

    def test_partial_score_is_clamped(self) -> None:
        above_one = score_validation_results([(important_validator, ValidationResult(False, score=2.0))])
        below_zero = score_validation_results([(important_validator, ValidationResult(False, score=-1.0))])

        self.assertEqual(above_one, DEFAULT_VALIDATOR_WEIGHT)
        self.assertEqual(below_zero, 0.0)


if __name__ == "__main__":
    unittest.main()
