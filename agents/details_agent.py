import logging
from pathlib import Path

from langchain_core.prompts import PromptTemplate
from langchain_core.language_models import BaseChatModel

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    ComponentApiSurfaces,
    ComponentArchitecture,
    ComponentRelations,
    Component,
    MetaAnalysisInsights,
    assign_component_ids,
    assign_relation_ids,
)
from agents.enrichment import StaticAnalysisEnricher
from agents.prompts import (
    get_system_details_message,
    get_details_message,
    get_api_surfaces_message,
    get_relation_analysis_message,
    format_project_system_message,
)
from agents.relation_edges import index_relation_endpoints
from agents.repair import (
    ComponentRepairContext,
    ensure_unique_key_entities,
    repair_component_group_names,
    repair_key_entities,
    repair_unique_key_entities,
)
from caching.cache import ModelSettings
from caching.details_cache import FinalAnalysisCache
from agents.validation import (
    ValidationContext,
    validate_group_name_coverage,
    validate_key_entities,
    validate_relations,
)
from monitoring import trace
from static_analyzer.clustering.service import ClusteringResults

logger = logging.getLogger(__name__)


class DetailsAgent(CodeBoardingAgent):

    def __init__(
        self,
        repo_dir: Path,
        clustering: ClusteringResults,
        project_name: str,
        meta_context: MetaAnalysisInsights,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
        run_id: str,
    ):
        system_message = format_project_system_message(get_system_details_message(), project_name, meta_context)
        super().__init__(repo_dir, clustering.static_analysis, system_message, agent_llm, parsing_llm)
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
    def step_analysis_shell(
        self,
        component: Component,
        clustering: ClusteringResults,
        enricher: StaticAnalysisEnricher,
    ) -> AnalysisInsights:
        """Name and describe the component's fixed sub-groups into the analysis shell (no relations yet)."""
        logger.info(f"[DetailsAgent] Generating detailed analysis shell for: {component.name}")
        cluster_analysis = clustering.cluster_analysis
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
        self.toolkit.context.cluster_results = clustering.cluster_results
        self.toolkit.context.cfg_graphs = clustering.cfg_graphs

        context = ValidationContext(
            cluster_results=clustering.cluster_results,
            static_analysis=clustering.static_analysis,
            llm_cluster_analysis=cluster_analysis,
        )

        cache_key = self._analysis_cache.build_key(prompt, self._cache_model_settings)

        if (cached := self._analysis_cache.load(cache_key)) is not None:
            return cached
        architecture = self._invoke_repair_validate(
            prompt,
            ComponentArchitecture,
            repairs=[repair_component_group_names, repair_key_entities, repair_unique_key_entities],
            validators=[
                validate_group_name_coverage,
                validate_key_entities,
            ],
            repair_context=ComponentRepairContext(
                reference_resolver=self.reference_resolver,
                cluster_results=clustering.cluster_results,
                llm_cluster_analysis=cluster_analysis,
            ),
            validation_context=context,
            max_validation_attempts=3,
        )
        enricher.pin_components_to_groups(architecture)
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
    def step_api_surfaces(self, analysis: AnalysisInsights, enricher: StaticAnalysisEnricher) -> ComponentApiSurfaces:
        logger.info(f"[DetailsAgent] Analyzing component API surfaces for: {self.project_name}")
        prompt = self.prompts["api_surfaces"].format(
            component_summaries=analysis.llm_str(),
            static_call_evidence=enricher.cfg_evidence(analysis),
        )
        return self._parse_invoke(prompt, ComponentApiSurfaces)

    @trace
    def step_relation_analysis(
        self,
        analysis: AnalysisInsights,
        api_surfaces: ComponentApiSurfaces,
        clustering: ClusteringResults,
        enricher: StaticAnalysisEnricher,
    ) -> None:
        logger.info(f"[DetailsAgent] Discovering component relations for: {self.project_name}")
        cluster_analysis = clustering.cluster_analysis
        self.toolkit.context.cluster_analysis = cluster_analysis
        self.toolkit.context.cluster_results = clustering.cluster_results
        self.toolkit.context.cfg_graphs = clustering.cfg_graphs
        prompt = self.prompts["relation_analysis"].format(
            component_summaries=analysis.llm_str(),
            api_surfaces=api_surfaces.llm_str(),
            static_call_evidence=enricher.cfg_evidence(analysis),
        )
        relation_result = self._invoke_validate(
            prompt,
            ComponentRelations,
            validators=[validate_relations],
            validation_context=ValidationContext(
                cluster_results=clustering.cluster_results,
                cfg_graphs=clustering.cfg_graphs,
                repo_dir=str(self.repo_dir),
                static_analysis=clustering.static_analysis,
                llm_cluster_analysis=cluster_analysis,
                components=analysis.components,
            ),
            max_validation_attempts=3,
        )
        analysis.components_relations = relation_result.components_relations
        assign_relation_ids(analysis)
        enricher.build_static_relations(analysis)

    def run(self, component: Component, clustering: ClusteringResults) -> AnalysisInsights:
        """Analyze a component in detail from its pre-clustered subgraph.

        ``clustering`` is the component-level scope produced by
        ``ClusteringService.cluster_component``: the LLM names the fixed
        sub-component groups, then the enricher infills the deterministic data.
        """
        logger.info(f"[DetailsAgent] Processing component: {component.name}")
        enricher = StaticAnalysisEnricher(clustering, self.repo_dir)

        analysis = self.step_analysis_shell(component, clustering, enricher)
        assign_component_ids(analysis, parent_id=component.component_id)
        enricher.resolve_cluster_ids(analysis)
        enricher.populate_file_methods(analysis)

        api_surfaces = self.step_api_surfaces(analysis, enricher)
        self.step_relation_analysis(analysis, api_surfaces, clustering, enricher)

        # Resolve source references, then finish the passes that depend on them.
        analysis = self.reference_resolver.fix_source_code_reference_lines(analysis)
        index_relation_endpoints(analysis, self.repo_dir)
        ensure_unique_key_entities(analysis)

        return analysis
