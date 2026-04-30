"""Runner for the semantic-incremental analysis path.

Why a separate class: ``DiagramGenerator.generate_analysis_incremental`` was a
parallel pipeline (IncrementalUpdater + run_trace + derive_patch_scopes +
apply_patch_scopes) that happened to share ``pre_analysis()`` and a few helpers
with ``generate_analysis``. Two algorithms hiding inside one class. This module
extracts the incremental algorithm; ``DiagramGenerator`` keeps the helpers
and a thin delegate so the call site stays the same.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agents.agent_responses import AnalysisInsights
from agents.llm_config import MONITORING_CALLBACK, initialize_llms
from diagram_analysis.incremental_tracer import run_trace
from diagram_analysis.incremental_updater import (
    IncrementalUpdater,
    apply_method_delta,
    drop_deltas_for_pruned_components,
    prune_empty_components,
)
from diagram_analysis.io_utils import save_analysis
from diagram_analysis.scope_planner import (
    apply_patch_scopes,
    build_ownership_index,
    derive_patch_scopes,
    normalize_changes_for_delta,
    pick_component_for_file,
)
from repo_utils import get_git_commit_hash

if TYPE_CHECKING:
    from diagram_analysis.diagram_generator import DiagramGenerator


class IncrementalRunner:
    """Owns the semantic-incremental pipeline.

    Takes the generator as a collaborator — uses it for static analysis,
    repo location, file-coverage summary, and the symbol/component
    resolvers it has already built up via ``pre_analysis``.
    """

    def __init__(self, generator: DiagramGenerator) -> None:
        self._gen = generator

    def run(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        base_ref: str,
        changes,
    ) -> Path:
        gen = self._gen
        if gen.static_analysis is None:
            gen.pre_analysis()
        assert gen.static_analysis is not None

        ownership_index = build_ownership_index(root_analysis, sub_analyses)
        added_files, modified_files, deleted_files, rename_map = normalize_changes_for_delta(changes)
        methods_by_file = gen._collect_method_entries_from_static_analysis()

        updater = IncrementalUpdater(
            analysis=root_analysis,
            symbol_resolver=lambda file_path: methods_by_file.get(gen._normalize_repo_path(file_path), []),
            repo_dir=gen.repo_location,
            component_resolver=lambda file_path: pick_component_for_file(file_path, ownership_index, rename_map),
        )
        delta = updater.compute_delta(
            added_files=added_files,
            modified_files=modified_files,
            deleted_files=deleted_files,
            changes=changes,
        )

        apply_method_delta(root_analysis, sub_analyses, delta)
        removed_component_ids = prune_empty_components(root_analysis, sub_analyses)
        drop_deltas_for_pruned_components(delta, removed_component_ids)
        post_delta_ownership_index = build_ownership_index(root_analysis, sub_analyses)

        agent_llm, parsing_llm = initialize_llms()
        callbacks = [MONITORING_CALLBACK]
        cfgs = {
            language: gen.static_analysis.get_cfg(language)
            for language in gen.static_analysis.get_languages()
            if gen.static_analysis.get_source_files(language)
        }
        trace_result = run_trace(
            delta,
            cfgs,
            gen.static_analysis,
            gen.repo_location,
            base_ref,
            parsing_llm,
            parsed_diff=getattr(changes, "parsed_diff", None),
            callbacks=callbacks,
        )

        patch_scopes = derive_patch_scopes(
            trace_result,
            root_analysis,
            sub_analyses,
            post_delta_ownership_index,
            rename_map,
        )
        if patch_scopes:
            root_analysis, sub_analyses = apply_patch_scopes(
                root_analysis, sub_analyses, patch_scopes, agent_llm, callbacks
            )

        analysis_path = save_analysis(
            analysis=root_analysis,
            output_dir=Path(gen.output_dir),
            sub_analyses=sub_analyses,
            repo_name=gen.repo_name,
            file_coverage_summary=gen._build_file_coverage_summary(),
            commit_hash=get_git_commit_hash(gen.repo_location),
        ).resolve()
        gen._write_file_coverage()
        return analysis_path
