"""
LLM-based diagram similarity scoring.

Uses a Model-as-Judge approach to compare generated diagrams against ground truth.
Leverages LangChain's structured output for guaranteed valid responses.
"""

import json
import logging
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from agents.agent import CodeBoardingAgent
from evals.tasks.accuracy.models import SimilarityScore, SimilarityScoreOutput

logger = logging.getLogger(__name__)

# Retry configuration for LLM calls
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0


SYSTEM_MESSAGE = """You are an expert at comparing software architecture diagrams.
You analyze structural similarity between diagram JSON objects and provide detailed scoring."""

SCORING_PROMPT_TEMPLATE = """Compare the following two diagram JSON objects and score their similarity.

## Scoring Criteria

1. **Node Coverage** (components/nodes overlap)
   - Compare the components/nodes present in both diagrams
   - Consider names, labels, and purposes of nodes

2. **Relationship Fidelity** (edges/relations correctness)
   - Compare the connections between nodes
   - Consider source, target, and relationship types

3. **Structural Coherence** (overall topology/flow alignment)
   - Compare the overall architecture patterns
   - Consider key paths, hubs, and data flow

## Scoring Scale
- **10**: Identical structure and semantics
- **7-9**: Minor differences in naming or structure
- **4-6**: Similar high-level structure with notable differences
- **2-3**: Some overlapping concepts but different architecture
- **1**: Completely unrelated diagrams

{examples}

## Diagrams to Compare

### Dataset Diagram (Ground Truth):
```json
{dataset_json}
```

### Generated Diagram:
```json
{generated_json}
```

Provide your score and concise 1-sentence reasoning for each criterion."""


class DiagramSimilarityJudge:
    """
    Scores similarity between generated diagrams and ground truth using an LLM judge.

    Uses LangChain's structured output to guarantee valid responses with proper typing.

    Example:
        judge = DiagramSimilarityJudge()
        score = judge.score(generated_diagram, expected_diagram)
        print(f"Score: {score.score}/10")
        print(f"Node coverage: {score.node_coverage_reasoning}")
    """

    def __init__(self, model_override: str | None = None):
        """
        Initialize the judge with an optional model override.

        Args:
            model_override: Specific model to use (e.g., "gpt-4"). Uses default if None.
        """
        self.model_override = model_override
        self._llm = None
        self._structured_llm = None

    @property
    def llm(self):
        """Lazy initialization of base LLM."""
        if self._llm is None:
            self._llm, _ = CodeBoardingAgent._static_initialize_llm(
                model_override=self.model_override,
                is_parsing=True,
            )
        return self._llm

    @property
    def structured_llm(self):
        """Lazy initialization of structured output LLM."""
        if self._structured_llm is None:
            self._structured_llm = self.llm.with_structured_output(
                SimilarityScoreOutput,
                include_raw=True,
            )
        return self._structured_llm

    def score(
        self,
        actual: dict[str, Any],
        expected: dict[str, Any],
        examples: str = "",
    ) -> SimilarityScore:
        """
        Score the similarity between actual (generated) and expected (ground truth) diagrams.

        Uses structured output to guarantee valid responses with proper typing.
        Falls back to an empty score on parsing errors.

        Args:
            actual: The generated diagram JSON
            expected: The ground truth diagram JSON
            examples: Optional few-shot examples to include in prompt

        Returns:
            SimilarityScore with score (1-10) and reasoning for each criterion
        """
        prompt = self._build_prompt(actual, expected, examples)
        return self._invoke_structured(prompt)

    def _build_prompt(
        self,
        actual: dict[str, Any],
        expected: dict[str, Any],
        examples: str = "",
    ) -> str:
        """Build the scoring prompt with diagram JSON."""
        return SCORING_PROMPT_TEMPLATE.format(
            examples=examples,
            dataset_json=json.dumps(expected, indent=2),
            generated_json=json.dumps(actual, indent=2),
        )

    def _invoke_structured(self, prompt: str) -> SimilarityScore:
        """
        Invoke LLM with structured output and return SimilarityScore.

        The structured output guarantees:
        - score is an int between 1-10
        - All reasoning fields are present and non-empty

        Uses exponential backoff retry for transient failures.
        Falls back to an invalid SimilarityScore on persistent errors.
        """
        messages = [
            SystemMessage(content=SYSTEM_MESSAGE),
            HumanMessage(content=prompt),
        ]

        last_exception: Exception | None = None
        backoff = INITIAL_BACKOFF_SECONDS

        for attempt in range(MAX_RETRIES):
            try:
                result = self.structured_llm.invoke(messages)

                # with include_raw=True, result is a dict with 'parsed' and 'raw' keys
                if isinstance(result, dict) and "parsed" in result:
                    parsed = result["parsed"]
                    if parsed is not None:
                        return SimilarityScore.from_output(parsed)

                    # Structured parsing failed, log the raw response
                    raw = result.get("raw")
                    logger.warning(
                        "Structured output parsing failed. Raw response: %s",
                        raw.content if hasattr(raw, "content") else raw,
                    )
                    # Parsing failure is not retryable
                    break
                elif isinstance(result, SimilarityScoreOutput):
                    # Direct result without include_raw
                    return SimilarityScore.from_output(result)

            except ValidationError as e:
                # Validation errors are not retryable
                logger.warning("Validation error in structured output: %s", e)
                break
            except Exception as e:
                last_exception = e
                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        MAX_RETRIES,
                        backoff,
                        e,
                    )
                    time.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER
                else:
                    logger.exception(
                        "LLM call failed after %d attempts: %s",
                        MAX_RETRIES,
                        last_exception,
                    )

        # Return invalid score on any failure
        return SimilarityScore()
