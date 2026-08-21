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
    MetaAnalysisInsights,
    assign_relation_ids,
)
from agents.cluster_methods_mixin import (
    ClusterMethodsMixin,
    cluster_group_descriptions,
    cluster_group_ids,
    render_cluster_groups,
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
from monitoring import trace
from static_analyzer import StaticAnalysisFatalError
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult, ClusterScopeResult

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
    def step_llm_analysis(self, scope: ClusterScopeResult) -> AnalysisInsights:
        logger.info(f"[AbstractionAgent] Generating final component analysis for: {self.project_name}")
        cluster_results = scope.leaf_clusters_by_language
        group_ids = cluster_group_ids(scope.groups)
        group_names = list(group_ids)

        prompt = self.prompts["final_analysis"].format(
            cluster_analysis=render_cluster_groups(
                group_ids,
                cluster_group_descriptions(scope.groups, cluster_results),
            ),
        )

        if group_names:
            prompt += (
                f"\n\n## All Group Names ({len(group_names)} total)\n"
                f"Every one of these names must appear in exactly one component's source_group_names: {group_names}\n"
            )

        context = ValidationContext(
            cluster_results=cluster_results,
            static_analysis=self.static_analysis,
            clustering=scope,
            group_ids=group_ids,
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
                clustering=scope,
                group_ids=group_ids,
            ),
            validation_context=context,
            max_validation_attempts=3,
        )
        self.assemble_one_component_per_group(architecture, scope)
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
        return self._parse_invoke(prompt, ComponentApiSurfaces)

    @trace
    def step_relation_analysis(
        self,
        analysis: AnalysisInsights,
        api_surfaces: ComponentApiSurfaces,
        scope: ClusterScopeResult,
    ) -> None:
        logger.info(f"[AbstractionAgent] Discovering component relations for: {self.project_name}")
        cluster_results = scope.leaf_clusters_by_language
        static_call_evidence = self.build_scope_cfg_string(analysis)
        cfg_graphs = scope.graphs_by_language
        self.toolkit.context.clustering = scope
        self.toolkit.context.cluster_group_ids = cluster_group_ids(scope.groups)
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
                clustering=scope,
                components=analysis.components,
            ),
            max_validation_attempts=3,
        )
        analysis.components_relations = relation_result.components_relations
        assign_relation_ids(analysis)
        self.build_static_relations(analysis)

    def run(self, scope: ClusterScopeResult) -> tuple[AnalysisInsights, dict[str, ClusterResult]]:
        """Name and analyze a precomputed root clustering scope."""
        cluster_results = scope.leaf_clusters_by_language
        if not scope.groups:
            # No leaf clusters means the repo has no callable structure to abstract
            # (unsupported/empty/no-code). Fail loudly instead of emptying the
            # architecture and crashing downstream in populate_file_methods.
            raise StaticAnalysisFatalError(
                f"No component groups found for {self.project_name}: the static analysis produced "
                "no callable structure to build an architecture from."
            )

        # Name and describe each fixed group into a component (LLM, one component per group)
        analysis = self.step_llm_analysis(scope)
        # Resolve cluster IDs deterministically from group names
        self._resolve_cluster_ids_from_groups(analysis, scope)
        # Populate file_methods deterministically from cluster results + orphan assignment
        self.populate_file_methods(analysis, cluster_results)

        api_surfaces = self.step_api_surfaces(analysis)
        self.step_relation_analysis(analysis, api_surfaces, scope)

        analysis = self.reference_resolver.fix_source_code_reference_lines(analysis)
        index_relation_endpoints(analysis, self.repo_dir)
        self._ensure_unique_key_entities(analysis)

        return analysis, cluster_results
