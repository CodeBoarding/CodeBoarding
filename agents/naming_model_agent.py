"""Read a repo's components out of its own identifiers, once per full analysis."""

import logging
from collections import Counter
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import NamingModelInsights
from agents.prompts import get_naming_model_message, get_system_message
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering.naming import ComponentVocabulary, NamingModel, scope_of, tokenize

logger = logging.getLogger(__name__)

VOCABULARY_SAMPLE = 120
IDENTIFIER_SAMPLE = 120


class NamingModelAgent(CodeBoardingAgent):
    """Decides which components a repo's vocabulary names, and which words are machinery.

    Run on a full analysis only. An incremental run reuses the stored answer, so the
    partition cannot move underneath unchanged code.
    """

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ):
        super().__init__(repo_dir, static_analysis, get_system_message(), agent_llm, parsing_llm)
        self.prompt = PromptTemplate(
            template=get_naming_model_message(),
            input_variables=["scopes", "vocabulary", "identifiers"],
        )

    @trace
    def read_naming_model(self) -> NamingModel:
        scopes, vocabulary, identifiers = self._evidence()
        if not identifiers:
            logger.warning("[NamingModelAgent] No identifiers to read; returning an empty model")
            return NamingModel(components=(), machinery=frozenset())

        insights = self._parse_invoke(
            self.prompt.format(
                scopes="\n".join(scopes),
                vocabulary=", ".join(f"{word}({count})" for word, count in vocabulary),
                identifiers="\n".join(identifiers),
            ),
            NamingModelInsights,
        )
        model = NamingModel(
            components=tuple(
                ComponentVocabulary(component.name, tuple(component.owns)) for component in insights.components
            ),
            machinery=frozenset(insights.machinery),
        )
        logger.info(
            "[NamingModelAgent] %d components, %d machinery words, scopes lead: %s",
            len(model.components),
            len(model.machinery),
            model.scopes_are_components(set(scopes)),
        )
        return model

    def _evidence(self) -> tuple[list[str], list[tuple[str, int]], list[str]]:
        """The scopes, the commonest words, and a sample of identifiers. Never the answer."""
        scopes: set[str] = set()
        words: Counter[str] = Counter()
        identifiers: set[str] = set()
        for node in self.static_analysis.iter_reference_nodes():
            scope = scope_of(node.file_path)
            if scope:
                scopes.add(scope)
            name = node.fully_qualified_name.rsplit(".", 1)[-1]
            identifiers.add(name)
            words.update(tokenize(name))
        return (
            sorted(scopes),
            words.most_common(VOCABULARY_SAMPLE),
            sorted(identifiers)[:IDENTIFIER_SAMPLE],
        )
