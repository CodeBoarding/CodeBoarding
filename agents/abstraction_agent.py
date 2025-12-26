import logging
import os.path
from collections import defaultdict
from pathlib import Path
from typing import Dict, Set, Optional

from langchain_core.prompts import PromptTemplate

from agents.agent import LargeModelAgent
from agents.agent_responses import (
    LLMBaseModel,
    AnalysisInsights,
    CFGAnalysisInsights,
    ClusterAnalysis,
    ValidationInsights,
    MetaAnalysisInsights,
    ComponentFiles,
    Component,
)
from agents.prompts import (
    get_cfg_message,
    get_source_message,
    get_system_message,
    get_cluster_analysis_message,
    get_final_analysis_message,
    get_conclusive_analysis_message,
    get_feedback_message,
    get_classification_message,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class AbstractionAgent(LargeModelAgent):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights,
    ):
        super().__init__(repo_dir, static_analysis, get_system_message())

        self.project_name = project_name
        self.meta_context = meta_context

        self.context: dict[str, LLMBaseModel] = {}

        self.prompts = {
            # NEW: Cluster-aware prompts
            "analyze_clusters": PromptTemplate(
                template=get_cluster_analysis_message(),
                input_variables=["project_name", "cfg_clusters", "meta_context", "project_type"]
            ),
            "final_analysis": PromptTemplate(
                template=get_final_analysis_message(),
                input_variables=["project_name", "cluster_analysis", "meta_context", "project_type"],
            ),
            # OLD: Keep for backward compatibility (will be removed later)
            "cfg": PromptTemplate(
                template=get_cfg_message(), input_variables=["project_name", "cfg_str", "meta_context", "project_type"]
            ),
            "source": PromptTemplate(
                template=get_source_message(), input_variables=["insight_so_far", "meta_context", "project_type"]
            ),
            "conclusive_analysis": PromptTemplate(
                template=get_conclusive_analysis_message(),
                input_variables=["project_name", "cfg_insight", "source_insight", "meta_context", "project_type"],
            ),
            "classification": PromptTemplate(
                template=get_classification_message(), input_variables=["project_name", "components", "files"]
            ),
            "feedback": PromptTemplate(template=get_feedback_message(), input_variables=["analysis", "feedback"]),
        }

    def _build_cluster_string(self, programming_langs: list[str]) -> str:
        """
        Build a cluster string that explicitly shows cluster IDs and their nodes.
        This makes it easy for the LLM to reference clusters.
        """
        cluster_lines = []

        for lang in programming_langs:
            cfg = self.static_analysis.get_cfg(lang)
            cluster_str = cfg.to_cluster_string()

            # The cluster string already has format: "Cluster 1 (5 nodes): [...]"
            # This is perfect - LLM can see cluster IDs explicitly
            cluster_lines.append(f"\n## {lang.capitalize()} Clusters\n")
            cluster_lines.append(cluster_str)

        return "".join(cluster_lines)

    @trace
    def analyze_clusters(self):
        """
        Step 1: Analyze CFG clusters and give them semantic meaning.
        LLM sees cluster definitions and assigns names/descriptions.
        """
        logger.info(f"[AbstractionAgent] Analyzing CFG clusters for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        programming_langs = self.static_analysis.get_languages()

        # Build cluster string that explicitly shows cluster IDs
        cluster_str = self._build_cluster_string(programming_langs)

        prompt = self.prompts["analyze_clusters"].format(
            project_name=self.project_name,
            cfg_clusters=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        cluster_analysis = self._parse_invoke(prompt, ClusterAnalysis)
        self.context["cluster_analysis"] = cluster_analysis
        return cluster_analysis

    @trace
    def generate_analysis(self):
        """
        Step 2: Synthesize final components from cluster analysis.
        This is where clusters get merged/refined into final components.
        """
        logger.info(f"[AbstractionAgent] Generating final analysis for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        cluster_analysis = self.context.get("cluster_analysis")
        cluster_str = cluster_analysis.llm_str() if cluster_analysis else "No cluster analysis available."

        prompt = self.prompts["final_analysis"].format(
            project_name=self.project_name,
            cluster_analysis=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        return self._parse_invoke(prompt, AnalysisInsights)

    # OLD METHODS - Keep for backward compatibility (will be removed later)
    @trace
    def step_cfg(self):
        logger.info(f"[AbstractionAgent] Analyzing CFG for project: {self.project_name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        programming_langs = self.static_analysis.get_languages()
        community_strs = ""
        if len(programming_langs) > 1:
            community_strs += f"This project contains multiple programming languages: {', '.join(programming_langs)}.\n"
        elif len(programming_langs) == 0:
            logger.warning(f"[AbstractionAgent] No programming languages detected for project: {self.project_name}")
            community_strs += "No programming languages detected.\n"

        # Generate visualizations for each language
        for pl in programming_langs:
            cfg = self.static_analysis.get_cfg(pl)
            community_strs += cfg.to_cluster_string()

        prompt = self.prompts["cfg"].format(
            project_name=self.project_name,
            cfg_str=community_strs,
            meta_context=meta_context_str,
            project_type=project_type,
        )
        parsed_response = self._parse_invoke(prompt, CFGAnalysisInsights)
        self.context["cfg_insight"] = parsed_response
        return parsed_response

    @trace
    def step_source(self):
        logger.info(f"[AbstractionAgent] Analyzing Source for project: {self.project_name}")
        insight_str = ""
        for insight_type, analysis_insight in self.context.items():
            insight_str += f"## {insight_type.capitalize()} Insight\n"
            insight_str += analysis_insight.llm_str() + "\n\n"

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["source"].format(
            insight_so_far=insight_str, meta_context=meta_context_str, project_type=project_type
        )
        parsed_response = self._parse_invoke(prompt, AnalysisInsights)
        self.context["source"] = parsed_response
        return parsed_response

    @trace
    def apply_feedback(self, analysis: AnalysisInsights, feedback: ValidationInsights):
        """
        Apply feedback to the analysis and return the updated analysis.
        This method should modify the analysis based on the feedback provided.
        """
        logger.info(f"[AbstractionAgent] Applying feedback to analysis for project: {self.project_name}")
        prompt = self.prompts["feedback"].format(analysis=analysis.llm_str(), feedback=feedback.llm_str())
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    def _build_file_cluster_mapping(self) -> Dict[str, Set[int]]:
        """
        Build mapping of file_path -> set of cluster_ids that have nodes in that file.
        This is purely deterministic - no LLM needed.
        """
        file_to_clusters: Dict[str, Set[int]] = defaultdict(set)

        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)
            nx_graph = cfg.to_networkx()

            if nx_graph.number_of_nodes() == 0:
                continue

            # Get cluster mapping
            communities, _ = cfg._adaptive_clustering(
                nx_graph,
                target_clusters=20,  # Use same default as to_cluster_string()
                min_cluster_size=2,
            )

            for cluster_id, nodes in enumerate(communities, start=1):  # Start from 1 to match display
                if len(nodes) < 2:  # Skip singletons
                    continue
                for node_name in nodes:
                    # Get file path for this node
                    if node_name in nx_graph.nodes:
                        node_data = nx_graph.nodes[node_name]
                        file_path = node_data.get('file_path')
                        if file_path:
                            file_to_clusters[file_path].add(cluster_id)

        return file_to_clusters

    def _match_file_to_component(
        self,
        file_path: str,
        components: list[Component],
        file_to_clusters: Dict[str, Set[int]]
    ) -> Optional[Component]:
        """
        Match a file to a component deterministically.

        Matching logic:
        1. If file contains a key_entity from component -> match
        2. If file's clusters overlap with component.source_cluster_ids -> match
        3. Otherwise -> no match (goes to Unclassified)
        """
        file_clusters = file_to_clusters.get(file_path, set())

        for component in components:
            if component.name == "Unclassified":
                continue

            # Check 1: Does file contain any key entities?
            for key_entity in component.key_entities:
                if key_entity.reference_file:
                    # Normalize paths for comparison
                    ref_file_norm = os.path.normpath(key_entity.reference_file)
                    file_path_norm = os.path.normpath(file_path)
                    if file_path_norm.endswith(ref_file_norm) or ref_file_norm in file_path_norm:
                        return component

            # Check 2: Do file's clusters overlap with component's clusters?
            if file_clusters and component.source_cluster_ids:
                if any(cluster_id in component.source_cluster_ids for cluster_id in file_clusters):
                    return component

        return None

    @trace
    def classify_files(self, analysis: AnalysisInsights):
        """
        Classify files into components based on the analysis (DETERMINISTIC).
        It will modify directly the analysis object.

        This method assigns files to components based on:
        1. key_entities references (if file contains those classes/methods)
        2. source_cluster_ids (if file contains nodes from those clusters)
        """
        logger.info(f"[AbstractionAgent] Classifying files deterministically for: {self.project_name}")
        all_files = self.static_analysis.get_all_source_files()

        # Add "Unclassified" component for files that don't fit
        analysis.components.append(
            Component(
                name="Unclassified",
                description="Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)",
                key_entities=[],
                source_cluster_ids=[],
            )
        )

        # Reset assigned_files for all components
        for comp in analysis.components:
            comp.assigned_files = []

        # Build file-to-cluster mapping for deterministic classification
        file_to_clusters = self._build_file_cluster_mapping()

        # Classify each file
        for file_path in all_files:
            matched_component = self._match_file_to_component(
                file_path,
                analysis.components,
                file_to_clusters
            )

            if matched_component:
                matched_component.assigned_files.append(file_path)
            else:
                # Add to Unclassified
                unclassified = next(c for c in analysis.components if c.name == "Unclassified")
                unclassified.assigned_files.append(file_path)

        # Convert to relative paths
        for comp in analysis.components:
            files = []
            for file in comp.assigned_files:
                if os.path.exists(file):
                    # relative path from the repo root
                    rel_file = os.path.relpath(file, self.repo_dir)
                    files.append(rel_file)
                else:
                    files.append(file)
            comp.assigned_files = files

        logger.info(f"[AbstractionAgent] Classified {len(all_files)} files into {len(analysis.components)} components")

    def run(self):
        """New simplified two-step flow"""
        self.analyze_clusters()  # Step 1: Understand clusters
        analysis = self.generate_analysis()  # Step 2: Create final components
        analysis = self.fix_source_code_reference_lines(analysis)
        return analysis
