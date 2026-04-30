"""Runner for the full (LLM-driven) analysis path.

Why a separate class: ``DiagramGenerator.generate_analysis`` plus its frontier-queue
scheduler (``_generate_subcomponents``) made up roughly half of the runtime
class. Splitting them out keeps the runtime class focused on shared resources
(agents, static analysis, file coverage) while the runner owns the algorithm.

The runner takes the generator as a collaborator so it can reach the helpers
(``process_component``, ``_build_file_coverage_summary``, ``_write_file_coverage``)
without duplicating them.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from agents.agent_responses import AnalysisInsights, Component
from agents.planner_agent import get_expandable_components
from diagram_analysis.io_utils import save_analysis
from repo_utils import get_git_commit_hash

if TYPE_CHECKING:
    from diagram_analysis.diagram_generator import DiagramGenerator

logger = logging.getLogger(__name__)


class FullAnalysisRunner:
    """Owns the full analysis pipeline.

    Builds the root analysis via the abstraction agent, then uses a
    frontier-queue scheduler to expand components in parallel up to
    ``generator.depth_level`` levels deep.
    """

    def __init__(self, generator: DiagramGenerator) -> None:
        self._gen = generator

    def run(self) -> Path:
        """Generate the graph analysis for the repository.

        Components are analyzed in parallel as soon as their parents complete.
        Output is stored as a single ``analysis.json`` in ``output_dir``.
        """
        gen = self._gen
        if gen.details_agent is None or gen.abstraction_agent is None:
            gen.pre_analysis()

        monitor = gen.stats_writer if gen.stats_writer else nullcontext()
        with monitor:
            logger.info("Generating initial analysis")
            assert gen.abstraction_agent is not None

            analysis, _cluster_results = gen.abstraction_agent.run()

            root_components = get_expandable_components(analysis)
            logger.info(f"Found {len(root_components)} components to analyze at level 1")

            _expanded, sub_analyses = self._expand_subcomponents(analysis, root_components)

            analysis_path = save_analysis(
                analysis=analysis,
                output_dir=Path(gen.output_dir),
                sub_analyses=sub_analyses,
                repo_name=gen.repo_name,
                file_coverage_summary=gen._build_file_coverage_summary(),
                commit_hash=get_git_commit_hash(gen.repo_location),
            ).resolve()

            logger.info(f"Analysis complete. Written unified analysis to {analysis_path}")

            gen._write_file_coverage()
            return analysis_path

    def _expand_subcomponents(
        self,
        analysis: AnalysisInsights,
        root_components: list[Component],
    ) -> tuple[list[Component], dict[str, AnalysisInsights]]:
        """Frontier-queue expansion: submit children as soon as parent finishes."""
        gen = self._gen
        max_workers = min(os.cpu_count() or 4, 8)

        expanded_components: list[Component] = []
        sub_analyses: dict[str, AnalysisInsights] = {}
        commit_hash = get_git_commit_hash(gen.repo_location)

        stats = {"submitted": 0, "completed": 0, "saves": 0, "errors": 0}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task: dict[Future, tuple[Component, int]] = {}

            def submit_component(comp: Component, lvl: int):
                future = executor.submit(gen.process_component, comp)
                future_to_task[future] = (comp, lvl)
                stats["submitted"] += 1
                logger.debug("Submitted component='%s' at level=%d", comp.name, lvl)

            if gen.depth_level > 1:
                for component in root_components:
                    submit_component(component, 1)

            logger.info(
                "Subcomponent generation started with %d workers. Initial tasks: %d",
                max_workers,
                stats["submitted"],
            )

            while future_to_task:
                completed_futures, _ = wait(future_to_task.keys(), return_when=FIRST_COMPLETED)

                for future in completed_futures:
                    component, level = future_to_task.pop(future)
                    stats["completed"] += 1

                    try:
                        comp_name, sub_analysis, new_components = future.result()

                        if comp_name and sub_analysis:
                            sub_analyses[comp_name] = sub_analysis
                            expanded_components.append(component)
                            stats["saves"] += 1

                            logger.debug("Saving intermediate analysis for '%s'", comp_name)
                            save_analysis(
                                analysis=analysis,
                                output_dir=Path(gen.output_dir),
                                sub_analyses=sub_analyses,
                                repo_name=gen.repo_name,
                                commit_hash=commit_hash,
                            )

                        if new_components and level + 1 < gen.depth_level:
                            for child in new_components:
                                submit_component(child, level + 1)

                            logger.info("Expanded '%s' with %d new children.", comp_name, len(new_components))

                    except Exception:
                        stats["errors"] += 1
                        logger.exception("Component '%s' generated an exception", component.name)

                logger.info(
                    "Progress: %d completed, %d in flight, %d errors",
                    stats["completed"],
                    len(future_to_task),
                    stats["errors"],
                )

            logger.info("Subcomponent generation complete: %s", stats)

        return expanded_components, sub_analyses
