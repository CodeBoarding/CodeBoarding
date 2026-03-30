import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    AnalysisStructure,
    ClusterAnalysis,
    MetaAnalysisInsights,
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
                input_variables=[
                    "project_name",
                    "cfg_clusters",
                    "meta_context",
                    "project_type",
                    "sorted_cluster_ids",
                    "cluster_count",
                ],
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
        expected_cluster_ids = get_all_cluster_ids(cluster_results)

        # Build cluster string using the pre-computed cluster results
        cluster_str = self._build_cluster_string(programming_langs, cluster_results)

        prompt = self.prompts["group_clusters"].format(
            project_name=self.project_name,
            cfg_clusters=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
            sorted_cluster_ids=", ".join(str(cluster_id) for cluster_id in sorted(expected_cluster_ids)),
            cluster_count=len(expected_cluster_ids),
        )

        cluster_analysis = self._validation_invoke(
            prompt,
            ClusterAnalysis,
            validators=[validate_cluster_coverage],
            context=ValidationContext(
                cluster_results=cluster_results,
                expected_cluster_ids=expected_cluster_ids,
            ),
            max_validation_retries=3,
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

        prompt = self.prompts["final_analysis"].format(
            project_name=self.project_name,
            cluster_analysis=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        structure = self._validation_invoke(
            prompt,
            AnalysisStructure,
            validators=[validate_group_name_coverage],
            context=ValidationContext(
                cluster_results=cluster_results,
                llm_cluster_analysis=llm_cluster_analysis,
            ),
            max_validation_retries=3,
        )
        return self._materialize_analysis(structure, llm_cluster_analysis, cluster_results)

    def run(self):
        # Build full cluster results dict for all languages ONCE
        cluster_results = build_all_cluster_results(self.static_analysis)

        # Step 1: Group related clusters together into logical components
        cluster_analysis = self.step_clusters_grouping(cluster_results)

        # Step 2: Generate abstract components from grouped clusters
        analysis = self.step_final_analysis(cluster_analysis, cluster_results)

        return analysis, cluster_results
