import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.analysis_result_responses import AnalysisInsights, assign_component_ids, assign_relation_ids
from agents.full_analysis_responses import (
    ComponentApiSurfaces,
    ComponentArchitecture,
    ComponentRelations,
    ClusterAnalysis,
    MetaAnalysisInsights,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.module_architecture import module_architecture_prompt, order_components_by_module
from agents.prompts import (
    get_api_surfaces_message,
    get_relation_analysis_message,
    get_system_message,
    format_project_system_message,
)
from agents.relation_edges import index_relation_endpoints
from agents.repair import ComponentRepairContext, repair_component_group_names, repair_key_entities
from agents.validation import (
    ValidationContext,
    validate_key_entities,
    validate_module_component_mapping,
    validate_relations,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results
from static_analyzer.clustering import ClusterResult

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

        cluster_evidence = self._build_cluster_string(
            self.static_analysis.get_languages(),
            cluster_results,
            prompt_overhead_chars=len(str(self.system_message.content)),
        )
        prompt = module_architecture_prompt(llm_cluster_analysis, cluster_evidence)

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
                validate_module_component_mapping,
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
        module_validation = validate_module_component_mapping(architecture, context)
        if not module_validation.is_valid:
            raise ValueError(" ".join(module_validation.feedback_messages))
        architecture.components = order_components_by_module(architecture.components, llm_cluster_analysis)
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
        cluster_analysis: ClusterAnalysis,
        cluster_results: dict[str, ClusterResult],
    ) -> None:
        logger.info(f"[AbstractionAgent] Discovering component relations for: {self.project_name}")
        static_call_evidence = self.build_scope_cfg_string(analysis)
        cfg_graphs = self.static_analysis.available_program_graphs()
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
        self.build_static_relations(analysis)

    def run(self):
        # Build full cluster results dict for all languages ONCE
        cluster_results = build_all_cluster_results(self.static_analysis)

        cluster_analysis = self._module_analysis(cluster_results)

        # Generate components from the deterministic top-level Infomap modules.
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
