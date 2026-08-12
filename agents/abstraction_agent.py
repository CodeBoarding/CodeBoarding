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
from agents.prompts import (
    get_final_analysis_message,
    get_api_surfaces_message,
    get_relation_analysis_message,
    get_system_message,
    format_project_system_message,
)
from agents.relation_edges import index_relation_endpoints
from agents.repair import ComponentRepairContext, repair_component_group_names, repair_key_entities
from agents.validation import (
    ValidationContext,
    validate_group_name_coverage,
    validate_key_entities,
    validate_relations,
)
from clustering import ClusteringResults
from clustering.assignment import (
    assemble_one_component_per_group,
    build_scope_cfg_string,
    build_static_relations,
    ensure_unique_key_entities,
    populate_file_methods,
    resolve_cluster_ids_from_groups,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)


class AbstractionAgent(CodeBoardingAgent):
    """Names and relates the top-level components fixed by the clustering stage."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ):
        system_message = format_project_system_message(get_system_message(), project_name, meta_context)
        super().__init__(repo_dir, static_analysis, system_message, agent_llm, parsing_llm)

        self.project_name = project_name
        self.meta_context = meta_context

        self.prompts = {
            "final_analysis": PromptTemplate(
                template=get_final_analysis_message(),
                input_variables=["cluster_analysis"],
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
    def step_final_analysis(
        self, llm_cluster_analysis: ClusterAnalysis, cluster_results: dict[str, ClusterResult]
    ) -> AnalysisInsights:
        logger.info(f"[AbstractionAgent] Generating final component analysis for: {self.project_name}")

        cluster_str = llm_cluster_analysis.llm_str() if llm_cluster_analysis else "No cluster analysis available."

        group_names = [cc.name for cc in llm_cluster_analysis.cluster_components] if llm_cluster_analysis else []

        prompt = self.prompts["final_analysis"].format(
            cluster_analysis=cluster_str,
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
        assemble_one_component_per_group(architecture, llm_cluster_analysis, cluster_results)
        return AnalysisInsights(
            description=architecture.description,
            components=architecture.components,
            components_relations=[],
        )

    @trace
    def step_api_surfaces(self, analysis: AnalysisInsights) -> ComponentApiSurfaces:
        logger.info(f"[AbstractionAgent] Analyzing component API surfaces for: {self.project_name}")
        static_call_evidence = build_scope_cfg_string(analysis, self.static_analysis)
        prompt = self.prompts["api_surfaces"].format(
            component_summaries=analysis.llm_str(),
            static_call_evidence=static_call_evidence,
        )
        return self._parse_invoke(prompt, ComponentApiSurfaces)

    @trace
    def step_relation_analysis(
        self,
        analysis: AnalysisInsights,
        api_surfaces: ComponentApiSurfaces,
        cluster_analysis: ClusterAnalysis,
        cluster_results: dict[str, ClusterResult],
    ) -> None:
        logger.info(f"[AbstractionAgent] Discovering component relations for: {self.project_name}")
        static_call_evidence = build_scope_cfg_string(analysis, self.static_analysis)
        cfg_graphs = self.static_analysis.available_cfgs()
        self.toolkit.context.cluster_analysis = cluster_analysis
        self.toolkit.context.cluster_results = cluster_results
        self.toolkit.context.cfg_graphs = cfg_graphs
        prompt = self.prompts["relation_analysis"].format(
            component_summaries=analysis.llm_str(),
            api_surfaces=api_surfaces.llm_str(),
            static_call_evidence=static_call_evidence,
        )
        relation_result = self._invoke_validate(
            prompt,
            ComponentRelations,
            validators=[validate_relations],
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
        build_static_relations(analysis, self.static_analysis)

    def run(self, clustering: ClusteringResults) -> AnalysisInsights:
        """Turn the clustering stage's fixed top-level groups into a named architecture."""
        cluster_analysis = clustering.cluster_analysis
        cluster_results = clustering.cluster_results

        # Step 1: Name and describe each fixed group into a component (LLM, one component per group)
        analysis = self.step_final_analysis(cluster_analysis, cluster_results)
        # Step 2: Assign hierarchical component IDs ("1", "2", "3", ...)
        assign_component_ids(analysis)
        # Step 3: Resolve cluster IDs deterministically from group names
        resolve_cluster_ids_from_groups(analysis, cluster_analysis)
        # Step 4: Populate file_methods deterministically from cluster results + orphan assignment
        populate_file_methods(analysis, cluster_results, self.repo_dir, self.static_analysis)

        # Step 5: Analyze component API surfaces
        api_surfaces = self.step_api_surfaces(analysis)

        # Step 6: Discover relations from API surfaces and attach deterministic all_edges
        self.step_relation_analysis(analysis, api_surfaces, cluster_analysis, cluster_results)

        # Step 7: Fix source code reference lines (resolves reference_file paths for key_entities and key_edges)
        analysis = self.reference_resolver.fix_source_code_reference_lines(analysis)
        # Step 8: Index relation endpoints after reference resolution
        index_relation_endpoints(analysis, self.repo_dir)
        # Step 9: Ensure unique key entities across components
        ensure_unique_key_entities(analysis)

        return analysis
