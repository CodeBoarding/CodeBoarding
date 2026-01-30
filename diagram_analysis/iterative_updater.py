"""
Minimal iterative update system for diagram analysis.

Routes updates based on change magnitude:
- SMALL: Update file assignments only (0 LLM calls, instant)
- MEDIUM: Update affected components (partial LLM calls, seconds)
- BIG: Full re-analysis (full LLM calls, minutes)
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from copy import deepcopy

from agents.abstraction_agent import AbstractionAgent
from agents.details_agent import DetailsAgent
from agents.planner_agent import PlannerAgent
from agents.agent_responses import AnalysisInsights, Component
from agents.cluster_methods_mixin import ClusterMethodsMixin
from static_analyzer.cluster_change_analyzer import ChangeClassification, ClusterChangeResult
from static_analyzer.graph import ClusterResult
from diagram_analysis.analysis_json import from_analysis_to_json
from output_generators.markdown import sanitize

logger = logging.getLogger(__name__)


@dataclass
class CachedAnalysis:
    """Simple cache entry for component analysis."""

    component_name: str
    level: int
    analysis: AnalysisInsights
    cluster_ids: set[int]
    commit_hash: str


class AnalysisCache:
    """File-based cache for agent outputs."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, level: int, analysis: AnalysisInsights, cluster_ids: set[int], commit: str) -> None:
        path = self.cache_dir / f"{name}_L{level}.json"
        data = {
            "name": name,
            "level": level,
            "commit": commit,
            "cluster_ids": list(cluster_ids),
            "analysis": analysis.model_dump(),
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self, name: str, level: int) -> Optional[CachedAnalysis]:
        path = self.cache_dir / f"{name}_L{level}.json"
        if not path.exists():
            return None
        with open(path) as f:
            data = json.load(f)
        return CachedAnalysis(
            data["name"],
            data["level"],
            AnalysisInsights(**data["analysis"]),
            set(data["cluster_ids"]),
            data["commit"],
        )

    def get_affected(self, change: ClusterChangeResult, level: Optional[int] = None) -> list[str]:
        """Find components affected by cluster changes."""
        affected = []
        for path in self.cache_dir.glob("*_L*.json"):
            with open(path) as f:
                data = json.load(f)
            if level is not None and data.get("level") != level:
                continue
            clusters = set(data.get("cluster_ids", []))
            if self._is_affected(clusters, change):
                affected.append(data["name"])
        return affected

    def _is_affected(self, clusters: set[int], change: ClusterChangeResult) -> bool:
        """Check if component clusters are in changed set."""
        if clusters & set(change.new_clusters) or clusters & set(change.removed_clusters):
            return True
        for match in change.matched_clusters:
            if match.old_cluster_id in clusters and match.similarity < 0.8:
                return True
        return False


class IterativeUpdater(ClusterMethodsMixin):
    """
    Updates diagram analysis based on change classification.
    Inherits ClusterMethodsMixin for file assignment utilities.
    """

    def __init__(
        self,
        repo_location: Path,
        output_dir: Path,
        abstraction_agent: AbstractionAgent,
        details_agent: DetailsAgent,
        planner_agent: PlannerAgent,
        current_commit: str,
    ):
        self.repo_location = repo_location
        self.output_dir = output_dir
        self.abstraction_agent = abstraction_agent
        self.details_agent = details_agent
        self.planner_agent = planner_agent
        self.current_commit = current_commit
        self.cache = AnalysisCache(output_dir / ".analysis_cache")
        # For ClusterMethodsMixin
        self.repo_dir = repo_location
        self.static_analysis = details_agent.static_analysis

    def update(
        self,
        classification: ChangeClassification,
        cluster_change: Optional[ClusterChangeResult],
        cluster_results: dict[str, ClusterResult],
        old_analysis: Optional[AnalysisInsights] = None,
    ) -> list[str]:
        """Route to appropriate update strategy."""
        # Handle missing data by falling back to full analysis
        if cluster_change is None or old_analysis is None:
            logger.warning("Missing cluster_change or old_analysis, falling back to full analysis")
            return self._update_big(cluster_results)

        if classification == ChangeClassification.SMALL:
            return self._update_small(cluster_change, cluster_results, old_analysis)
        elif classification == ChangeClassification.MEDIUM:
            return self._update_medium(cluster_change, cluster_results, old_analysis)
        else:
            return self._update_big(cluster_results)

    def _update_small(
        self,
        change: ClusterChangeResult,
        cluster_results: dict[str, ClusterResult],
        old_analysis: AnalysisInsights,
    ) -> list[str]:
        """SMALL: Just reassign files, no LLM calls."""
        logger.info("SMALL change: updating file assignments only")

        updated = deepcopy(old_analysis)

        # Update cluster IDs and reassign files for affected components
        for component in updated.components:
            current_clusters = set(component.source_cluster_ids)
            affected = False

            for match in change.matched_clusters:
                if match.old_cluster_id in current_clusters:
                    current_clusters.discard(match.old_cluster_id)
                    current_clusters.add(match.new_cluster_id)
                    affected = True

            if affected:
                component.source_cluster_ids = list(current_clusters)
                self._assign_files_to_component(component, cluster_results)
                logger.info(f"Updated files for {component.name}")

        return self._write_outputs(updated, [], is_update=True)

    def _update_medium(
        self,
        change: ClusterChangeResult,
        cluster_results: dict[str, ClusterResult],
        old_analysis: Optional[AnalysisInsights],
    ) -> list[str]:
        """MEDIUM: Update affected components only."""
        logger.info("MEDIUM change: updating affected components")

        # Check if Level 0 needs update
        level0_affected = bool(change.new_clusters or change.removed_clusters)

        if level0_affected or not old_analysis:
            logger.info("Re-running AbstractionAgent")
            new_analysis, _ = self.abstraction_agent.run()
            root_clusters = self._all_cluster_ids(cluster_results)
            self.cache.save("root", 0, new_analysis, root_clusters, self.current_commit)
        else:
            new_analysis = old_analysis
            logger.info("Using cached Level 0")

        # Get components and identify affected ones
        components = self.planner_agent.plan_analysis(new_analysis)
        affected = set(self.cache.get_affected(change, level=1))

        # Process each component
        component_analyses = []
        for component in components:
            if component.name in affected:
                logger.info(f"Re-analyzing: {component.name}")
                comp_analysis, _ = self.details_agent.run(component)
                clusters = set(component.source_cluster_ids)
                self.cache.save(component.name, 1, comp_analysis, clusters, self.current_commit)
                component_analyses.append(comp_analysis)
            else:
                cached = self.cache.load(component.name, 1)
                if cached:
                    logger.info(f"Cached: {component.name}")
                    component_analyses.append(cached.analysis)
                else:
                    comp_analysis, _ = self.details_agent.run(component)
                    clusters = set(component.source_cluster_ids)
                    self.cache.save(component.name, 1, comp_analysis, clusters, self.current_commit)
                    component_analyses.append(comp_analysis)

        return self._write_outputs(new_analysis, component_analyses, is_update=True)

    def _update_big(self, cluster_results: dict[str, ClusterResult]) -> list[str]:
        """BIG: Full re-analysis (current behavior)."""
        logger.info("BIG change: full re-analysis")

        analysis, _ = self.abstraction_agent.run()
        root_clusters = self._all_cluster_ids(cluster_results)
        self.cache.save("root", 0, analysis, root_clusters, self.current_commit)

        components = self.planner_agent.plan_analysis(analysis)
        component_analyses = []

        for component in components:
            comp_analysis, _ = self.details_agent.run(component)
            clusters = set(component.source_cluster_ids)
            self.cache.save(component.name, 1, comp_analysis, clusters, self.current_commit)
            component_analyses.append(comp_analysis)

        return self._write_outputs(analysis, component_analyses, is_update=False)

    def _all_cluster_ids(self, results: dict[str, ClusterResult]) -> set[int]:
        """Get all cluster IDs."""
        ids = set()
        for result in results.values():
            ids.update(result.get_cluster_ids())
        return ids

    def _write_outputs(self, root: AnalysisInsights, components: list[AnalysisInsights], is_update: bool) -> list[str]:
        """Write JSON output files."""
        files = []

        # Root analysis
        root_path = self.output_dir / "analysis.json"
        expandable = []
        for comp in components:
            expandable.extend(comp.components)

        with open(root_path, "w") as f:
            f.write(from_analysis_to_json(root, expandable))
        files.append(str(root_path))

        # Component files
        for comp in components:
            for component in comp.components:
                safe_name = sanitize(component.name)
                comp_path = self.output_dir / f"{safe_name}.json"
                with open(comp_path, "w") as f:
                    f.write(from_analysis_to_json(comp, []))
                files.append(str(comp_path))

        action = "Updated" if is_update else "Generated"
        logger.info(f"{action} {len(files)} files")
        return files
