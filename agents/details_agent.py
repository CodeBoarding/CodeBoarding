import logging
import os
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent import LargeModelAgent
from agents.agent_responses import (
    AnalysisInsights,
    CFGAnalysisInsights,
    LLMBaseModel,
    ValidationInsights,
    Component,
    MetaAnalysisInsights,
    ComponentFiles,
)
from agents.prompts import (
    get_system_details_message,
    get_cfg_details_message,
    get_details_message,
    get_subcfg_details_message,
    get_enhance_structure_message,
    get_feedback_message,
    get_classification_message,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class DetailsAgent(ClusterMethodsMixin, LargeModelAgent):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights,
    ):
        super().__init__(repo_dir, static_analysis, get_system_details_message())
        self.project_name = project_name
        self.meta_context = meta_context

        self.prompts = {
            "subcfg": PromptTemplate(
                template=get_subcfg_details_message(), input_variables=["project_name", "cfg_str", "component"]
            ),
            "cfg": PromptTemplate(
                template=get_cfg_details_message(),
                input_variables=["cfg_str", "project_name", "meta_context", "project_type"],
            ),
            "structure": PromptTemplate(
                template=get_enhance_structure_message(),
                input_variables=["insight_so_far", "component", "project_name", "meta_context", "project_type"],
            ),
            "final_analysis": PromptTemplate(
                template=get_details_message(),
                input_variables=["insight_so_far", "component", "meta_context", "project_type"],
            ),
            "feedback": PromptTemplate(template=get_feedback_message(), input_variables=["analysis", "feedback"]),
            "classification": PromptTemplate(
                template=get_classification_message(), input_variables=["project_name", "components", "files"]
            ),
        }

        self.context: dict[str, LLMBaseModel | str] = {}

    def _extract_relevant_cfg(self, component: Component) -> str:
        """
        Extract CFG clusters relevant to this component.
        Uses component.source_cluster_ids for deterministic filtering.
        """
        if not component.source_cluster_ids:
            logger.warning(f"[DetailsAgent] Component {component.name} has no source_cluster_ids, using fallback")
            # Fallback to old method if no cluster IDs
            return self.read_cfg_tool.component_cfg(component)  # type: ignore[return-value]

        cluster_ids = set(component.source_cluster_ids)
        cfg_lines = []

        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)
            cluster_str = cfg.to_cluster_string()

            # Extract only requested clusters
            filtered_clusters = self._extract_clusters_from_string(cluster_str, cluster_ids)

            if filtered_clusters:
                cfg_lines.append(f"\n## {lang.capitalize()} - Component CFG\n")
                cfg_lines.append(filtered_clusters)
                cfg_lines.append("\n")

        result = "".join(cfg_lines)
        if not result.strip():
            logger.warning(f"[DetailsAgent] No CFG found for component {component.name}, cluster IDs: {cluster_ids}")
            return "No relevant CFG clusters found for this component."

        return result

    def step_subcfg(self, component: Component):
        logger.info(
            f"[DetailsAgent] Filtering CFG for {component.name} using cluster IDs: {component.source_cluster_ids}"
        )
        filtered_cfg = self._extract_relevant_cfg(component)
        self.context["subcfg_insight"] = filtered_cfg

    @trace
    def step_cfg(self, component: Component) -> CFGAnalysisInsights:
        logger.info(f"[DetailsAgent] Analyzing details on cfg for {component.name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["cfg"].format(
            project_name=self.project_name,
            cfg_str=self.context["subcfg_insight"],
            component=component.llm_str(),
            meta_context=meta_context_str,
            project_type=project_type,
        )
        parsed = self._parse_invoke(prompt, CFGAnalysisInsights)
        self.context["cfg_insight"] = parsed  # Store for next step
        return parsed

    @trace
    def step_enhance_structure(self, component: Component) -> AnalysisInsights:
        logger.info(f"[DetailsAgent] Analyzing details on structure for {component.name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        cfg_insight = self.context.get("cfg_insight")
        cfg_insight_str = (
            cfg_insight.llm_str()
            if cfg_insight and isinstance(cfg_insight, LLMBaseModel)
            else "No CFG insight available."
        )
        prompt = self.prompts["structure"].format(
            project_name=self.project_name,
            insight_so_far=cfg_insight_str,
            component=component.llm_str(),
            meta_context=meta_context_str,
            project_type=project_type,
        )
        parsed = self._parse_invoke(prompt, AnalysisInsights)
        self.context["structure_insight"] = parsed
        return parsed

    @trace
    def step_analysis(self, component: Component) -> AnalysisInsights:
        logger.info("[DetailsAgent] Generating details documentation")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        structure_insight = self.context["structure_insight"]
        insight_str = (
            structure_insight.llm_str() if isinstance(structure_insight, LLMBaseModel) else str(structure_insight)
        )
        prompt = self.prompts["final_analysis"].format(
            insight_so_far=insight_str,
            component=component.llm_str(),
            meta_context=meta_context_str,
            project_type=project_type,
        )
        return self._parse_invoke(prompt, AnalysisInsights)

    @trace
    def apply_feedback(self, analysis: AnalysisInsights, feedback: ValidationInsights) -> AnalysisInsights:
        logger.info(f"[DetailsAgent] Applying feedback to analysis for project: {self.project_name}")
        prompt = self.prompts["feedback"].format(analysis=analysis.llm_str(), feedback=feedback.llm_str())
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    def run(self, component: Component):
        logger.info(f"Processing component: {component.name}")
        self.step_subcfg(component)
        self.step_cfg(component)
        self.step_enhance_structure(component)
        analysis = self.step_analysis(component)
        return self.fix_source_code_reference_lines(analysis)

    @trace
    def classify_files(self, component: Component, analysis: AnalysisInsights):
        logger.info(f"[DetailsAgent] Classifying component {component.name} based on assigned files")
        all_files = component.assigned_files
        analysis.components.append(
            Component(
                name="Unclassified",
                description="Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)",
                key_entities=[],
                source_cluster_ids=[],
            )
        )
        component_str = "\n".join([component.llm_str() for component in analysis.components])

        for comp in analysis.components:
            comp.assigned_files = []

        files = []
        for i in range(0, len(all_files), 100):
            file_block = [str(f) for f in all_files[i : i + 100]]
            prompt = self.prompts["classification"].format(
                project_name=self.project_name, components=component_str, files="\n".join(file_block)
            )
            classification = self._parse_invoke(prompt, ComponentFiles)
            files.extend(classification.file_paths)
        for file in files:
            matched_comp: Component | None = next(
                (c for c in analysis.components if c.name == file.component_name), None
            )
            if matched_comp is None:
                logger.warning(f"[DetailsAgent] File {file.component_name} not found in analysis")
                continue
            matched_comp.assigned_files.append(file.file_path)

        for comp in analysis.components:
            normalized_files: list[str] = []
            for file_path in comp.assigned_files:
                if os.path.exists(file_path):
                    rel_file = os.path.relpath(file_path, self.repo_dir)
                    normalized_files.append(rel_file)
                else:
                    normalized_files.append(file_path)
            comp.assigned_files = normalized_files
