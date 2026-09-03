"""One level-agnostic agent for naming components and describing their relations."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field, ValidationError

from agents.agent_responses import AnalysisInsights, LLMBaseModel, RelationEdge, SourceCodeReference
from agents.llm_config import MONITORING_CALLBACK
from agents.llm_renderers import render_scope_context, scope_file_paths, scope_method_names
from agents.prompts import SCOPE_ANALYSIS_MESSAGE, SCOPE_ANALYSIS_SYSTEM_MESSAGE
from agents.tools import MethodCallsTool, ReadFileTool
from agents.tools.base import RepoContext
from monitoring.mixin import MonitoringMixin
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterScopeResult

logger = logging.getLogger(__name__)

MAX_SCOPE_TOOL_CALLS = 6
MAX_SCOPE_MODEL_CALLS = 8
SCOPE_RECURSION_LIMIT = 40


class ScopeComponentSemantics(LLMBaseModel):
    """Semantic metadata for one fixed deterministic group."""

    group_id: str = Field(description="Exact deterministic group ID from the input.")
    name: str = Field(description="Architectural name for the group's one responsibility.")
    description: str = Field(description="Concise description of the group's responsibility.")
    key_entities: list[SourceCodeReference] = Field(
        default_factory=list,
        description="Important exact source symbols owned by this group.",
    )

    def llm_str(self) -> str:
        return f"{self.group_id}: {self.name} — {self.description}"


class ScopeRelationSemantics(LLMBaseModel):
    """Semantic label and evidence for one directed group relation."""

    source_group_id: str = Field(description="Exact source group ID from the input.")
    target_group_id: str = Field(description="Exact target group ID from the input.")
    relation: str = Field(description="Short directed architectural phrase.")
    evidence: str = Field(
        default="",
        description="Concrete runtime evidence; may be empty for a supplied known call connection.",
    )
    key_edges: list[RelationEdge] = Field(
        default_factory=list,
        description="Exact source-side to target-side symbol pairs supporting this relation.",
    )

    def llm_str(self) -> str:
        return f"{self.source_group_id} -{self.relation}-> {self.target_group_id}"


class ScopeAnalysisResult(LLMBaseModel):
    """Semantic enrichment for one deterministic scope."""

    description: str = Field(default="", description="Purpose and main flow of this scope.")
    components: list[ScopeComponentSemantics] = Field(default_factory=list)
    relations: list[ScopeRelationSemantics] = Field(default_factory=list)

    def llm_str(self) -> str:
        components = "\n".join(component.llm_str() for component in self.components)
        relations = "\n".join(relation.llm_str() for relation in self.relations)
        return "\n".join(part for part in (self.description, components, relations) if part)


class ScopeAnalysisAgent(MonitoringMixin):
    """Analyze any deterministic scope with two scope-restricted tools."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        agent_llm: BaseChatModel,
    ) -> None:
        super().__init__()
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis
        self.agent_llm = agent_llm
        self.ignore_manager = RepoIgnoreManager(repo_dir)

    def analyze(
        self,
        scope: ClusterScopeResult,
        analysis: AnalysisInsights,
        editable_group_ids: set[str],
        locked_name_ids: frozenset[str] = frozenset(),
        changed_files: frozenset[str] = frozenset(),
        incremental: bool = False,
    ) -> ScopeAnalysisResult | None:
        """Run one bounded semantic analysis, returning no result for malformed output."""
        context = RepoContext(
            repo_dir=self.repo_dir,
            ignore_manager=self.ignore_manager,
            static_analysis=self.static_analysis,
            scope_restricted=True,
            scope_files=scope_file_paths(scope, self.repo_dir),
            scope_methods=scope_method_names(scope),
            cfg_graphs=dict(scope.graphs_by_language),
        )
        tools = [ReadFileTool(context=context), MethodCallsTool(context=context)]
        middleware: list = [
            ToolCallLimitMiddleware(run_limit=MAX_SCOPE_TOOL_CALLS, exit_behavior="continue"),
            ModelCallLimitMiddleware(run_limit=MAX_SCOPE_MODEL_CALLS, exit_behavior="error"),
        ]
        agent: CompiledStateGraph = create_agent(
            model=self.agent_llm,
            tools=tools,
            system_prompt=SCOPE_ANALYSIS_SYSTEM_MESSAGE,
            middleware=middleware,
        )
        scope_context = render_scope_context(
            scope,
            analysis,
            self.repo_dir,
            editable_group_ids,
            locked_name_ids,
            changed_files,
            incremental,
        )
        response = agent.invoke(
            {"messages": [HumanMessage(content=SCOPE_ANALYSIS_MESSAGE.format(scope_context=scope_context))]},
            config={
                "callbacks": [MONITORING_CALLBACK, self.agent_monitoring_callback],
                "recursion_limit": SCOPE_RECURSION_LIMIT,
            },
        )
        text = self._last_response_text(response.get("messages", []))
        try:
            return ScopeAnalysisResult.model_validate_json(self._json_object(text))
        except (json.JSONDecodeError, ValidationError, ValueError) as error:
            logger.warning("Scope %s returned unusable semantic output: %s", scope.scope_id, error)
            return None

    @staticmethod
    def _last_response_text(messages: list) -> str:
        """Extract text from the final model response across provider content formats."""
        message = next((item for item in reversed(messages) if isinstance(item, AIMessage)), None)
        if message is None:
            return ""
        if isinstance(message.content, str):
            return message.content
        parts: list[str] = []
        for block in message.content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("output_text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    @staticmethod
    def _json_object(text: str) -> str:
        """Extract the outer JSON object without invoking a second model."""
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("response contains no JSON object")
        return text[start : end + 1]
