import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    ComponentApiSurfaces,
    ComponentArchitecture,
    ComponentRelations,
    ClusterAnalysis,
    MetaAnalysisInsights,
    assign_component_ids,
    assign_relation_ids,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.prompts import (
    get_cluster_grouping_message,
    get_final_analysis_message,
    get_api_surfaces_message,
    get_relation_analysis_message,
    get_system_message,
)
from agents.relation_edges import index_relation_endpoints
from agents.repair import ComponentRepairContext, repair_component_group_names, repair_key_entities
from agents.validation import (
    ValidationContext,
    validate_cluster_coverage,
    validate_group_name_coverage,
    validate_key_entities,
    validate_relations,
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
        meta_context_str = meta_context.llm_str() if meta_context else "No project context available."
        project_type = meta_context.project_type if meta_context else "unknown"
        system_message = get_system_message().format(
            project_name=project_name,
            project_type=project_type,
            meta_context=meta_context_str,
        )
        super().__init__(repo_dir, static_analysis, system_message, agent_llm, parsing_llm)

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
            "api_surfaces": PromptTemplate(
                template=get_api_surfaces_message(),
                input_variables=[
                    "component_summaries",
                    "static_call_evidence",
                ],
            ),
            "relation_analysis": PromptTemplate(
                template=get_relation_analysis_message(),
                input_variables=[
                    "component_summaries",
                    "api_surfaces",
                    "static_call_evidence",
                ],
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

        cluster_analysis = self._invoke_repair_validate(
            prompt,
            ClusterAnalysis,
            repairs=[],
            validators=[validate_cluster_coverage],
            repair_context=None,
            validation_context=ValidationContext(
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
        logger.info(f"[AbstractionAgent] Generating final component analysis for: {self.project_name}")

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

        context = ValidationContext(
            cluster_results=cluster_results,
            static_analysis=self.static_analysis,
            llm_cluster_analysis=llm_cluster_analysis,
        )

        architecture = self._invoke_repair_validate(
            prompt,
            ComponentArchitecture,
            repairs=[repair_component_group_names, repair_key_entities],
            validators=[
                validate_group_name_coverage,
                validate_key_entities,
            ],
            repair_context=ComponentRepairContext(
                reference_resolver=self.reference_resolver,
                cluster_results=cluster_results,
                llm_cluster_analysis=llm_cluster_analysis,
            ),
            validation_context=context,
            max_validation_attempts=3,
        )
        return AnalysisInsights(
            description=architecture.description,
            components=architecture.components,
            components_relations=[],
        )

    @trace
    def step_api_surfaces(self, analysis: AnalysisInsights) -> ComponentApiSurfaces:
        logger.info(f"[AbstractionAgent] Analyzing component API surfaces for: {self.project_name}")
        static_call_evidence = self.build_scope_cfg_string(analysis)
        prompt = self.prompts["api_surfaces"].format(
            component_summaries=analysis.llm_str(),
            static_call_evidence=static_call_evidence,
        )
        return self._invoke_repair_validate(
            prompt,
            ComponentApiSurfaces,
            repairs=[],
            validators=[],
            repair_context=None,
            validation_context=None,
            max_validation_attempts=1,
        )

    @trace
    def step_relation_analysis(
        self,
        analysis: AnalysisInsights,
        api_surfaces: ComponentApiSurfaces,
        cluster_analysis: ClusterAnalysis,
        cluster_results: dict[str, ClusterResult],
    ) -> None:
        logger.info(f"[AbstractionAgent] Discovering component relations for: {self.project_name}")
        static_call_evidence = self.build_scope_cfg_string(analysis)
        cfg_graphs = self.static_analysis.available_cfgs()
        self.toolkit.context.cluster_analysis = cluster_analysis
        self.toolkit.context.cluster_results = cluster_results
        self.toolkit.context.cfg_graphs = cfg_graphs
        prompt = self.prompts["relation_analysis"].format(
            component_summaries=analysis.llm_str(),
            api_surfaces=api_surfaces.llm_str(),
            static_call_evidence=static_call_evidence,
        )
        relation_result = self._invoke_repair_validate(
            prompt,
            ComponentRelations,
            repairs=[],
            validators=[validate_relations],
            repair_context=None,
            validation_context=ValidationContext(
                cluster_results=cluster_results,
                cfg_graphs=cfg_graphs,
                repo_dir=str(self.repo_dir),
                static_analysis=self.static_analysis,
                llm_cluster_analysis=cluster_analysis,
                components=analysis.components,
            ),
            max_validation_attempts=3,
        )
        analysis.components_relations = relation_result.components_relations
        assign_relation_ids(analysis)
        self.build_static_relations(analysis)

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

        # Step 6: Analyze component API surfaces
        api_surfaces = self.step_api_surfaces(analysis)

        # Step 7: Discover relations from API surfaces and attach deterministic all_edges
        self.step_relation_analysis(analysis, api_surfaces, cluster_analysis, cluster_results)

        # Step 8: Fix source code reference lines (resolves reference_file paths for key_entities and key_edges)
        analysis = self.reference_resolver.fix_source_code_reference_lines(analysis)
        # Step 9: Index relation endpoints after reference resolution
        index_relation_endpoints(analysis, self.repo_dir)
        # Step 10: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)

        return analysis, cluster_results
