import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    MetaAnalysisInsights,
    assign_component_ids,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.prompts import (
    get_cluster_grouping_message,
    get_final_analysis_message,
    get_system_message,
)
from agents.validation import (
    ValidationContext,
    validate_cluster_coverage,
    validate_group_name_coverage,
    validate_key_entities,
    validate_relation_component_names,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import (
    build_all_cluster_results,
    get_all_cluster_ids,
)
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)


class AbstractionAgent(ClusterMethodsMixin, CodeBoardingAgent):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ):
        super().__init__(repo_dir, static_analysis, get_system_message(), agent_llm, parsing_llm)

        self.project_name = project_name
        self.meta_context = meta_context

        self.prompts = {
            "group_clusters": PromptTemplate(
                template=get_cluster_grouping_message(),
                input_variables=["project_name", "cfg_clusters", "meta_context", "project_type"],
            ),
            "final_analysis": PromptTemplate(
                template=get_final_analysis_message(),
                input_variables=["project_name", "cluster_analysis", "meta_context", "project_type"],
            ),
        }

    @trace
    def step_clusters_grouping(self, cluster_results: dict[str, ClusterResult]) -> ClusterAnalysis:
        logger.info(f"[AbstractionAgent] Grouping CFG clusters for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        programming_langs = self.static_analysis.get_languages()

        # Measure everything that wraps cfg_clusters (system message + rendered
        # template with an empty slot) so the skip planner can back it out of
        # the input window before budgeting the cluster string.
        overhead_chars = len(str(self.system_message.content)) + len(
            self.prompts["group_clusters"].format(
                project_name=self.project_name,
                cfg_clusters="",
                meta_context=meta_context_str,
                project_type=project_type,
            )
        )
        cluster_str = self._build_cluster_string(
            programming_langs, cluster_results, prompt_overhead_chars=overhead_chars
        )

        prompt = self.prompts["group_clusters"].format(
            project_name=self.project_name,
            cfg_clusters=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        cluster_analysis = self._validation_invoke(
            prompt,
            ClusterAnalysis,
            validators=[validate_cluster_coverage],
            context=ValidationContext(
                cluster_results=cluster_results,
                expected_cluster_ids=get_all_cluster_ids(cluster_results),
            ),
            max_validation_attempts=3,
        )
        return cluster_analysis

    @trace
    def step_final_analysis(
        self, llm_cluster_analysis: ClusterAnalysis, cluster_results: dict[str, ClusterResult]
    ) -> AnalysisInsights:
        logger.info(f"[AbstractionAgent] Generating final analysis for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        cluster_str = llm_cluster_analysis.llm_str() if llm_cluster_analysis else "No cluster analysis available."

        group_names = [cc.name for cc in llm_cluster_analysis.cluster_components] if llm_cluster_analysis else []

        prompt = self.prompts["final_analysis"].format(
            project_name=self.project_name,
            cluster_analysis=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        if group_names:
            prompt += (
                f"\n\n## All Group Names ({len(group_names)} total)\n"
                f"Every one of these names must appear in exactly one component's source_group_names: {group_names}\n"
            )

        # Build validation context with CFG graphs for edge checking
        context = ValidationContext(
            cluster_results=cluster_results,
            cfg_graphs={lang: self.static_analysis.get_cfg(lang) for lang in self.static_analysis.get_languages()},
            static_analysis=self.static_analysis,
            llm_cluster_analysis=llm_cluster_analysis,
        )

        return self._validation_invoke(
            prompt,
            AnalysisInsights,
            validators=[
                validate_relation_component_names,
                validate_group_name_coverage,
                validate_key_entities,
            ],
            context=context,
            max_validation_attempts=3,
        )

    def run(self):
        # Build full cluster results dict for all languages ONCE
        cluster_results = build_all_cluster_results(self.static_analysis)

        # Step 1: Group related clusters together into logical components
        cluster_analysis = self.step_clusters_grouping(cluster_results)

        # Step 2: Generate abstract components from grouped clusters
        analysis = self.step_final_analysis(cluster_analysis, cluster_results)
        # Step 3: Assign hierarchical component IDs ("1", "2", "3", ...)
        assign_component_ids(analysis)
        # Step 4: Resolve cluster IDs deterministically from group names
        self._resolve_cluster_ids_from_groups(analysis, cluster_analysis)
        # Step 5: Populate file_methods deterministically from cluster results + orphan assignment
        self.populate_file_methods(analysis, cluster_results)

        # Step 6: Build static inter-component relations from CFG edges
        self.build_static_relations(analysis)

        # Step 7: Fix source code reference lines (resolves reference_file paths for key_entities)
        analysis = self.fix_source_code_reference_lines(analysis)
        # Step 8: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)

        return analysis, cluster_results
