import logging
from pathlib import Path

from langchain_core.prompts import PromptTemplate
from langchain_core.language_models import BaseChatModel

from agents.agent import CodeBoardingAgent
from agents.analysis_result_responses import AnalysisInsights, Component, assign_component_ids, assign_relation_ids
from agents.full_analysis_responses import (
    ClusterAnalysis,
    ComponentApiSurfaces,
    ComponentArchitecture,
    ComponentRelations,
    MetaAnalysisInsights,
)
from agents.module_architecture import module_architecture_prompt, order_components_by_module
from agents.prompts import (
    get_system_details_message,
    get_api_surfaces_message,
    get_relation_analysis_message,
    format_project_system_message,
)
from agents.relation_edges import index_relation_endpoints
from agents.repair import ComponentRepairContext, repair_component_group_names, repair_key_entities
from agents.cluster_methods_mixin import ClusterMethodsMixin
from caching.cache import ModelSettings
from caching.details_cache import FinalAnalysisCache
from agents.validation import (
    ValidationContext,
    validate_key_entities,
    validate_module_component_mapping,
    validate_relations,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult
from static_analyzer.program_graph import ProgramGraph

logger = logging.getLogger(__name__)


class DetailsAgent(ClusterMethodsMixin, CodeBoardingAgent):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
        run_id: str,
    ):
        system_message = format_project_system_message(get_system_details_message(), project_name, meta_context)
        super().__init__(repo_dir, static_analysis, system_message, agent_llm, parsing_llm)
        self.project_name = project_name
        self.meta_context = meta_context
        self.run_id = run_id
        self._cache_model_settings = ModelSettings.from_chat_model(provider="unknown", llm=agent_llm)
        self._analysis_cache = FinalAnalysisCache(repo_dir=repo_dir)

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
        self,
        component: Component,
        cluster_analysis: ClusterAnalysis,
        subgraph_cluster_results: dict[str, ClusterResult],
        subgraph_cfgs: dict[str, ProgramGraph],
    ) -> AnalysisInsights:
        """
        Generate detailed final analysis from grouped clusters.

        Args:
            component: The component being analyzed
            cluster_analysis: Deterministic modules projected from the Infomap hierarchy
            subgraph_cluster_results: Cluster results for the subgraph (for validation)

        Returns:
            AnalysisInsights with detailed component information
        """
        logger.info(f"[DetailsAgent] Generating final detailed analysis for: {component.name}")
        cluster_evidence = self._build_cluster_string(
            self.static_analysis.get_languages(),
            subgraph_cluster_results,
            prompt_overhead_chars=len(str(self.system_message.content)) + len(component.llm_str()),
        )
        prompt = module_architecture_prompt(cluster_analysis, cluster_evidence, component.llm_str())

        self.toolkit.context.cluster_analysis = cluster_analysis
        self.toolkit.context.cluster_results = subgraph_cluster_results
        self.toolkit.context.cfg_graphs = subgraph_cfgs

        context = ValidationContext(
            cluster_results=subgraph_cluster_results,
            static_analysis=self.static_analysis,
            llm_cluster_analysis=cluster_analysis,
        )

        cache_key = self._analysis_cache.build_key(prompt, self._cache_model_settings)

        if (cached := self._analysis_cache.load(cache_key)) is not None:
            return cached
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
                cluster_results=subgraph_cluster_results,
                llm_cluster_analysis=cluster_analysis,
            ),
            validation_context=context,
            max_validation_attempts=3,
        )
        module_validation = validate_module_component_mapping(architecture, context)
        if not module_validation.is_valid:
            raise ValueError(" ".join(module_validation.feedback_messages))
        architecture.components = order_components_by_module(architecture.components, cluster_analysis)
        result = AnalysisInsights(
            description=architecture.description,
            components=architecture.components,
            components_relations=[],
        )
        self._analysis_cache.store(
            cache_key,
            result,
            run_id=self.run_id,
        )
        return result

    @trace
    def step_api_surfaces(self, analysis: AnalysisInsights) -> ComponentApiSurfaces:
        logger.info(f"[DetailsAgent] Analyzing component API surfaces for: {self.project_name}")
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
        cfg_graphs: dict[str, ProgramGraph],
        source_cluster_id_prefix: str,
    ) -> None:
        logger.info(f"[DetailsAgent] Discovering component relations for: {self.project_name}")
        static_call_evidence = self.build_scope_cfg_string(analysis)
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
        self.build_static_relations(analysis, cfg_graphs, source_cluster_id_prefix=source_cluster_id_prefix)

    def run(self, component: Component):
        """
        Analyze a component in detail by creating a subgraph and analyzing its structure.

        This follows the same pattern as AbstractionAgent but operates on a component-level
        subgraph instead of the full codebase.

        Args:
            component: Component to analyze in detail

        Returns:
            Tuple of (AnalysisInsights, cluster_results dict) with detailed component information
        """
        logger.info(f"[DetailsAgent] Processing component: {component.name}")

        _subgraph_str, subgraph_cluster_results, subgraph_cfgs = self._create_strict_component_subgraph(
            component, source_cluster_id_prefix=component.component_id
        )
        if sum(len(result.clusters) for result in subgraph_cluster_results.values()) <= 1:
            logger.info("[DetailsAgent] %s has no deeper Infomap module partition", component.name)
            return AnalysisInsights(description=component.description, components=[], components_relations=[]), {}

        cluster_analysis = self._module_analysis(subgraph_cluster_results)
        analysis = self.step_final_analysis(component, cluster_analysis, subgraph_cluster_results, subgraph_cfgs)

        # Step 4: Assign hierarchical component IDs (e.g., "1.1", "1.2" under parent "1")
        assign_component_ids(analysis, parent_id=component.component_id)

        # Step 5: Resolve cluster IDs deterministically from group names
        self._resolve_cluster_ids_from_groups(analysis, cluster_analysis)

        self.populate_file_methods(analysis, subgraph_cluster_results, subgraph_cfgs)

        # Step 7: Analyze component API surfaces
        api_surfaces = self.step_api_surfaces(analysis)

        # Step 8: Discover relations from API surfaces and attach deterministic all_edges
        self.step_relation_analysis(
            analysis,
            api_surfaces,
            cluster_analysis,
            subgraph_cluster_results,
            subgraph_cfgs,
            component.component_id,
        )

        # Step 9: Fix source code reference lines (resolves reference_file paths)
        analysis = self.reference_resolver.fix_source_code_reference_lines(analysis)

        # Step 10: Index relation endpoints after reference resolution
        index_relation_endpoints(analysis, self.repo_dir)

        # Step 11: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)

        return analysis, subgraph_cluster_results
