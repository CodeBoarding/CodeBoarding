import logging
from pathlib import Path

from langchain_core.prompts import PromptTemplate
from langchain_core.language_models import BaseChatModel

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ComponentApiSurfaces,
    ComponentArchitecture,
    ComponentRelations,
    Component,
    MetaAnalysisInsights,
    assign_component_ids,
    assign_relation_ids,
)
from agents.prompts import (
    get_system_details_message,
    get_details_message,
    get_api_surfaces_message,
    get_relation_analysis_message,
    format_project_system_message,
)
from agents.relation_edges import index_relation_endpoints
from agents.repair import ComponentRepairContext, repair_component_group_names, repair_key_entities
from caching.cache import ModelSettings
from caching.details_cache import FinalAnalysisCache
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
from static_analyzer.graph import CallGraph, ClusterResult

logger = logging.getLogger(__name__)


class DetailsAgent(CodeBoardingAgent):
    """Names and relates one component's sub-components fixed by the clustering stage."""

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
            "final_analysis": PromptTemplate(
                template=get_details_message(),
                input_variables=["cluster_analysis", "component"],
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
        self,
        component: Component,
        cluster_analysis: ClusterAnalysis,
        subgraph_cluster_results: dict[str, ClusterResult],
        subgraph_cfgs: dict[str, CallGraph],
    ) -> AnalysisInsights:
        """
        Generate detailed final analysis from grouped clusters.

        Args:
            component: The component being analyzed
            cluster_analysis: The clustered structure from the clustering stage
            subgraph_cluster_results: Cluster results for the subgraph (for validation)

        Returns:
            AnalysisInsights with detailed component information
        """
        logger.info(f"[DetailsAgent] Generating final detailed analysis for: {component.name}")
        cluster_str = cluster_analysis.llm_str() if cluster_analysis else "No cluster analysis available."

        group_names = [cc.name for cc in cluster_analysis.cluster_components] if cluster_analysis else []

        prompt = self.prompts["final_analysis"].format(
            cluster_analysis=cluster_str,
            component=component.llm_str(),
        )

        if group_names:
            prompt += (
                f"\n\n## All Group Names ({len(group_names)} total)\n"
                f"Every one of these names: {group_names} must appear in exactly one component's source_group_names\n"
            )

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
                validate_group_name_coverage,
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
        assemble_one_component_per_group(architecture, cluster_analysis, subgraph_cluster_results)
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
        cfg_graphs: dict[str, CallGraph],
        source_cluster_id_prefix: str,
    ) -> None:
        logger.info(f"[DetailsAgent] Discovering component relations for: {self.project_name}")
        static_call_evidence = build_scope_cfg_string(analysis, self.static_analysis)
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
        build_static_relations(analysis, self.static_analysis, cfg_graphs, source_cluster_id_prefix)

    def run(self, component: Component, clustering: ClusteringResults) -> AnalysisInsights:
        """
        Analyze a component in detail from its pre-clustered subgraph.

        This follows the same pattern as AbstractionAgent but operates on the
        component-level clustering produced by ``ClusteringService.cluster_component``.

        Pipeline:
        1. LLM creates components from the fixed sub-component groups
           (validated: key_entities must be in cluster scope)
        2. Deterministically assign methods via cluster -> component mapping
        3. Discover relations, resolve references

        Args:
            component: Component to analyze in detail
            clustering: The component's subgraph clustering results

        Returns:
            AnalysisInsights with detailed component information
        """
        logger.info(f"[DetailsAgent] Processing component: {component.name}")
        cluster_analysis = clustering.cluster_analysis
        subgraph_cluster_results = clustering.cluster_results
        subgraph_cfgs = clustering.cfg_graphs

        # Step 1: Generate detailed analysis from grouped clusters
        # Validation ensures key_entities are within cluster scope (no rescue needed)
        analysis = self.step_final_analysis(component, cluster_analysis, subgraph_cluster_results, subgraph_cfgs)

        # Step 2: Assign hierarchical component IDs (e.g., "1.1", "1.2" under parent "1")
        assign_component_ids(analysis, parent_id=component.component_id)

        # Step 3: Resolve cluster IDs deterministically from group names
        resolve_cluster_ids_from_groups(analysis, cluster_analysis)

        # Step 4: Populate file_methods deterministically from cluster results + orphan assignment
        # Pass subgraph_cfgs to scope node collection to the component's filtered graph
        # With method-level expansion, each method has its own cluster -> deterministic assignment
        populate_file_methods(analysis, subgraph_cluster_results, self.repo_dir, self.static_analysis, subgraph_cfgs)

        # Step 5: Analyze component API surfaces
        api_surfaces = self.step_api_surfaces(analysis)

        # Step 6: Discover relations from API surfaces and attach deterministic all_edges
        self.step_relation_analysis(
            analysis,
            api_surfaces,
            cluster_analysis,
            subgraph_cluster_results,
            subgraph_cfgs,
            component.component_id,
        )

        # Step 7: Fix source code reference lines (resolves reference_file paths)
        analysis = self.reference_resolver.fix_source_code_reference_lines(analysis)

        # Step 8: Index relation endpoints after reference resolution
        index_relation_endpoints(analysis, self.repo_dir)

        # Step 9: Ensure unique key entities across components
        ensure_unique_key_entities(analysis)

        return analysis
