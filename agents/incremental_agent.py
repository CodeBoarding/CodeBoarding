"""Incremental grouping agent + deterministic stitching helpers.

One LLM call: route the cluster delta to existing components by name, or
create new ones with a ``parent_id``. Stitching back into the live tree
(``stitch_delta``) and downstream refresh/prune is deterministic.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersComponent,
    Component,
    FileMethodGroup,
    MetaAnalysisInsights,
    MethodEntry,
    Relation,
    ScopeRelations,
    assign_component_ids,
    index_components_by_id,
    iter_components,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.prompts import get_incremental_grouping_message, get_scope_relations_message, get_system_message
from agents.validation import (
    ValidationContext,
    validate_cluster_coverage,
    validate_existing_component_ids,
    validate_scope_relation_names,
)
from diagram_analysis.cluster_delta import ClusterDelta
from diagram_analysis.io_utils import normalize_repo_path
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_relations import merge_relations
from static_analyzer.constants import Language
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)


@dataclass
class IncrementalUpdatePlan:
    """Deterministic work plan produced by incremental stitching."""

    refresh_ids: set[str] = field(default_factory=set)
    detail_ids: set[str] = field(default_factory=set)


class IncrementalAgent(ClusterMethodsMixin, CodeBoardingAgent):
    """One LLM call: route delta clusters to existing or new components."""

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights | None,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ):
        super().__init__(
            repo_dir,
            static_analysis,
            get_system_message(),
            agent_llm,
            parsing_llm,
        )
        # Routing only needs source disambiguation, not the full code-reading
        # toolkit — narrow the ReAct loop by rebuilding the agent with one tool.
        self.agent = create_agent(
            model=agent_llm,
            tools=[self.toolkit.read_source_reference],
        )
        self.project_name = project_name
        self.meta_context = meta_context
        self.prompts = {
            "group_delta": PromptTemplate(
                template=get_incremental_grouping_message(),
                input_variables=["project_name", "meta_context", "project_type", "existing_components", "cfg_clusters"],
            ),
        }

    @trace
    def run(
        self,
        delta: ClusterDelta,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> ClusterAnalysis:
        affected_cluster_ids = delta.all_affected_cluster_ids()
        if not affected_cluster_ids:
            return ClusterAnalysis(cluster_components=[])

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"
        cluster_results = delta.cluster_results()
        affected_files: set[str] = set()
        for cr in cluster_results.values():
            for cid in affected_cluster_ids:
                affected_files |= cr.cluster_to_files.get(cid, set())
        existing_components_str = _format_existing_components(
            root_analysis, sub_analyses, affected_files=affected_files
        )

        programming_langs = self.static_analysis.get_languages()

        overhead_chars = len(str(self.system_message.content)) + len(
            self.prompts["group_delta"].format(
                project_name=self.project_name,
                meta_context=meta_context_str,
                project_type=project_type,
                existing_components=existing_components_str,
                cfg_clusters="",
            )
        )
        cluster_str = self._build_cluster_string(
            programming_langs,
            cluster_results,
            cluster_ids=affected_cluster_ids,
            prompt_overhead_chars=overhead_chars,
        )

        prompt = self.prompts["group_delta"].format(
            project_name=self.project_name,
            meta_context=meta_context_str,
            project_type=project_type,
            existing_components=existing_components_str,
            cfg_clusters=cluster_str,
        )

        existing_component_ids = {
            c.component_id for c in iter_components(root_analysis, sub_analyses) if c.component_id
        }
        result = self._validation_invoke(
            prompt,
            ClusterAnalysis,
            validators=[validate_cluster_coverage, validate_existing_component_ids],
            context=ValidationContext(
                cluster_results=cluster_results,
                expected_cluster_ids=affected_cluster_ids,
                existing_component_ids=existing_component_ids,
            ),
            max_validation_attempts=3,
            include_hidden=True,
        )
        _log_routing_summary(result)
        return result

    @trace
    def generate_scope_relations(self, scope: AnalysisInsights, scope_name: str) -> list[Relation]:
        """Generate LLM relations for a single scope and merge with existing static ones.

        Mutates ``scope.components_relations`` in place. Returns the LLM-generated relations.
        """
        if len(scope.components) < 2:
            return []

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        component_summaries = "\n".join(c.llm_str() for c in scope.components)
        cross_calls = self.build_scope_cfg_string(scope)

        template = get_scope_relations_message()
        prompt_template = PromptTemplate(
            template=template,
            input_variables=[
                "scope_name",
                "project_name",
                "meta_context",
                "project_type",
                "component_summaries",
                "cross_component_calls",
            ],
        )
        prompt = prompt_template.format(
            scope_name=scope_name,
            project_name=self.project_name,
            meta_context=meta_context_str,
            project_type=project_type,
            component_summaries=component_summaries,
            cross_component_calls=cross_calls,
        )

        valid_names = {c.name for c in scope.components}
        context = ValidationContext(valid_component_names=valid_names)

        result: ScopeRelations = self._validation_invoke(
            prompt,
            ScopeRelations,
            validators=[validate_scope_relation_names],
            context=context,
            max_validation_attempts=3,
        )

        existing_static = [r for r in scope.components_relations if r.is_static]
        merged = merge_relations(result.components_relations, [], scope)
        if existing_static:
            for r in existing_static:
                existing_pair = next(
                    (m for m in merged if m.src_id == r.src_id and m.dst_id == r.dst_id),
                    None,
                )
                if existing_pair is None:
                    merged.append(r)
                elif existing_pair.edge_count == 0 and r.edge_count > 0:
                    existing_pair.edge_count = r.edge_count
                    existing_pair.is_static = True

        scope.components_relations = merged
        return result.components_relations

    @trace
    def generate_all_scope_relations(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        touched_scopes: set[str],
    ) -> None:
        """Generate LLM relations for every touched scope with >= 2 components.

        The LLM infers semantic connections that CFG-only ``build_static_relations``
        misses (e.g. a component that registers callbacks siblings call, with no
        direct static edge). Called once after stitching, repopulation, and
        redetail are complete so the full component context is available.
        """
        all_llm_rels: list[tuple[str, list[Relation]]] = []
        if "" in touched_scopes:
            rels = self.generate_scope_relations(root_analysis, "root")
            if rels:
                all_llm_rels.append(("root", rels))
        for scope_id in sorted(touched_scopes - {""}):
            sub = sub_analyses.get(scope_id)
            if sub is not None:
                rels = self.generate_scope_relations(sub, scope_id)
                if rels:
                    all_llm_rels.append((scope_id, rels))

        if all_llm_rels:
            _log_scope_relations_summary(all_llm_rels)


def _format_existing_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    affected_files: set[str] | None = None,
) -> str:
    """Render existing components for the incremental grouping prompt.

    Components owning a file in *affected_files* get full info; others get a
    single ``id "name"`` line. ``affected_files=None`` renders everything full.
    """
    all_components: list[Component] = list(iter_components(root_analysis, sub_analyses))

    if not all_components:
        return "(no existing components -- incremental run on an empty baseline)"

    if affected_files is None:
        return "\n".join(_format_component_line(c, include_description=True) for c in all_components)

    affected: list[Component] = []
    other: list[Component] = []
    for component in all_components:
        owns_relevant = any(g.file_path in affected_files for g in component.file_methods)
        (affected if owns_relevant else other).append(component)

    sections: list[str] = []
    if affected:
        sections.append("### Affected components (full info)")
        sections.extend(_format_component_line(c, include_description=True) for c in affected)
    if other:
        sections.append("\n### Other components (routing targets, names only)")
        sections.extend(_format_component_line(c, include_description=False) for c in other)
    return "\n".join(sections)


def _format_component_line(component: Component, include_description: bool = True) -> str:
    cid = component.component_id or "?"
    if not include_description:
        return f'- {cid} "{component.name}"'
    desc = (component.description or "").strip().replace("\n", " ")
    return f'- {cid} "{component.name}" -- {desc}'


# ---------------------------------------------------------------------------
# Stitching (deterministic, no LLM)
# ---------------------------------------------------------------------------
class Verdict(StrEnum):
    """Derived stitching verdict — not part of the LLM schema.

    Why: the verdict is implicit in (existing_component_id, redetail_needed);
    surfacing it as a derived label keeps logs/stats legible without a
    second source of truth.
    """

    ADD = "ADD"
    UPDATE = "UPDATE"
    NOOP = "NOOP"


def _classify_verdict(cc: ClustersComponent, *, existing_found: bool) -> Verdict:
    if not existing_found:
        return Verdict.ADD
    return Verdict.UPDATE if cc.redetail_needed else Verdict.NOOP


def _log_routing_summary(result: ClusterAnalysis) -> None:
    if not result.cluster_components:
        return
    rows = []
    for cc in result.cluster_components:
        action = (
            f"UPDATE {cc.existing_component_id}" if cc.existing_component_id else f"ADD under {cc.parent_id or 'root'}"
        )
        rows.append(f"  {action:25s}  {cc.name:40s}  clusters={cc.cluster_ids}")
    header = f"[incremental] Routing decisions ({len(result.cluster_components)} groups):"
    logger.info("\n".join([header] + rows))


def _log_stitch_summary(
    routing_rows: list[dict],
    verdicts: dict[Verdict, int],
    refresh_ids: set[str],
    detail_ids: set[str],
) -> None:
    lines = [
        f"[stitch] ADD={verdicts[Verdict.ADD]} UPDATE={verdicts[Verdict.UPDATE]} "
        f"NOOP={verdicts[Verdict.NOOP]}  refresh={sorted(refresh_ids)} detail={sorted(detail_ids)}"
    ]
    for r in routing_rows:
        parent_str = f"under {r['parent']}" if r["parent"] else ""
        redetail_str = "redetail" if r["redetail"] else "keep"
        lines.append(
            f"  {r['verdict']:8s}  {r['id']:5s} {r['name']:40s}  "
            f"clusters={r['clusters']}  {redetail_str}  {parent_str}"
        )
    logger.info("\n".join(lines))


def _log_scope_relations_summary(all_rels: list[tuple[str, list[Relation]]]) -> None:
    lines = ["[scope_relations] LLM-generated inter-component relations:"]
    for scope_name, rels in all_rels:
        for r in rels:
            lines.append(f"  {scope_name:8s}  {r.src_name:40s} --{r.relation}--> {r.dst_name}")
    logger.info("\n".join(lines))


def _deduplicate_cluster_routes(delta_cluster_analysis: ClusterAnalysis) -> ClusterAnalysis:
    """Keep each changed cluster in exactly one routed component.

    A fresh cluster can overlap multiple previous components when old clusters
    merged. The route is still applied once; deterministic remapping handles the
    affected survivors.
    """
    seen: set[int] = set()
    kept_components: list[ClustersComponent] = []
    dropped: list[str] = []
    for cc in delta_cluster_analysis.cluster_components:
        kept_cluster_ids: list[int] = []
        for cluster_id in cc.cluster_ids:
            if cluster_id in seen:
                dropped.append(f"{cluster_id} from {cc.existing_component_id or cc.name}")
                continue
            seen.add(cluster_id)
            kept_cluster_ids.append(cluster_id)
        if kept_cluster_ids:
            kept_components.append(cc.model_copy(update={"cluster_ids": kept_cluster_ids}))

    if dropped:
        logger.warning("[incremental] dropped duplicate cluster route(s): %s", "; ".join(dropped))
    return ClusterAnalysis(cluster_components=kept_components)


def _sort_cluster_ids(cluster_ids) -> list:
    return sorted(
        cluster_ids,
        key=lambda cluster_id: (
            (0, cluster_id)
            if isinstance(cluster_id, int)
            else (1, [int(part) if part.isdigit() else part for part in cluster_id.split(".")])
        ),
    )


def _source_cluster_ids(cluster_ids: set[int]) -> list[str]:
    return [str(cluster_id) for cluster_id in _sort_cluster_ids(cluster_ids)]


def _stabilize_existing_file_routes(
    delta_cluster_analysis: ClusterAnalysis,
    component_index: dict[str, Component],
    cluster_results: dict[str, ClusterResult],
    repo_dir: Path,
) -> ClusterAnalysis:
    """Route clusters touching already-owned files back to their existing owner."""
    repo_root = repo_dir.resolve()

    file_owners: dict[str, list[Component]] = {}
    for component in component_index.values():
        for group in component.file_methods:
            file_path = normalize_repo_path(group.file_path, repo_root)
            file_owners.setdefault(file_path, []).append(component)

    cluster_files: dict[int, set[str]] = {}
    for cr in cluster_results.values():
        for cluster_id, files in cr.cluster_to_files.items():
            cluster_files.setdefault(cluster_id, set()).update(normalize_repo_path(fp, repo_root) for fp in files)

    stabilized: list[ClustersComponent] = []
    fallback_reroutes: list[ClustersComponent] = []
    rerouted: list[str] = []
    for cc in delta_cluster_analysis.cluster_components:
        buckets: dict[tuple[str, str | None], list[int]] = {}
        for cluster_id in cc.cluster_ids:
            owner = _owner_for_cluster_files(cluster_files.get(cluster_id, set()), file_owners)
            if cc.existing_component_id is None and owner is not None and owner.component_id:
                buckets.setdefault(("rerouted", owner.component_id), []).append(cluster_id)
                rerouted.append(f"{cluster_id} -> {owner.component_id}")
            else:
                key = ("original", cc.existing_component_id) if cc.existing_component_id else ("add", cc.parent_id)
                buckets.setdefault(key, []).append(cluster_id)

        for (kind, component_id), cluster_ids in buckets.items():
            if kind == "rerouted" and component_id in component_index:
                fallback_reroutes.append(
                    cc.model_copy(
                        update={
                            "name": "",
                            "description": "",
                            "cluster_ids": cluster_ids,
                            "existing_component_id": component_id,
                            "parent_id": None,
                            "redetail_needed": True,
                        }
                    )
                )
            else:
                stabilized.append(cc.model_copy(update={"cluster_ids": cluster_ids}))

    if rerouted:
        logger.warning("[incremental] rerouted existing-file cluster(s): %s", "; ".join(rerouted))
    return ClusterAnalysis(cluster_components=[*stabilized, *fallback_reroutes])


def _owner_for_cluster_files(files: set[str], file_owners: dict[str, list[Component]]) -> Component | None:
    candidates: dict[str, tuple[int, int, Component]] = {}
    for file_path in files:
        for component in file_owners.get(file_path, []):
            if not component.component_id:
                continue
            previous = candidates.get(component.component_id, (0, 0, component))
            depth = component.component_id.count(".") + 1
            candidates[component.component_id] = (previous[0] + 1, depth, component)
    if not candidates:
        return None
    return max(candidates.values(), key=lambda item: (item[0], item[1], item[2].component_id or ""))[2]


def _root_cluster_ids(source_cluster_ids: list[str]) -> set[int]:
    return {int(cluster_id) for cluster_id in source_cluster_ids if cluster_id.isdigit()}


def stitch_delta(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    delta_cluster_analysis: ClusterAnalysis,
    delta: ClusterDelta,
    repo_dir: Path = Path("."),
) -> IncrementalUpdatePlan:
    """Apply the delta ClusterAnalysis to the live tree.

    Routing is by ``existing_component_id`` (decision #4). The LLM either
    sets it to a live component_id (route into that component) or leaves it
    null (create a new component). Name matching is *not* used — that path
    silently forks a duplicate component on every rename.

    Steps:
    1. Index live components by component id.
    2. Stabilize ADD routes that touch already-owned files back to the owner.
    3. Deduplicate merged clusters so each routed cluster is applied once.
    4. Refresh changed component method lists and detail only newly-created
       components that can still have child scopes.
    """
    component_index = index_components_by_id(root_analysis, sub_analyses)
    delta_cluster_analysis = _stabilize_existing_file_routes(
        delta_cluster_analysis, component_index, delta.cluster_results(), repo_dir
    )
    delta_cluster_analysis = _deduplicate_cluster_routes(delta_cluster_analysis)

    cluster_id_remap = delta.merged_cluster_id_remap()
    dropped_cluster_ids = delta.all_dropped_cluster_ids()
    # Member-set churn (id unchanged) still requires redetail: file_methods
    # depends on cluster MEMBERS, not just IDs.
    changed_cluster_ids: set[int] = {
        cluster_id_remap.get(cid, cid) for ld in delta.by_language.values() for cid in ld.changed_cluster_ids
    }

    plan = IncrementalUpdatePlan()

    for component in component_index.values():
        before = set(component.source_cluster_ids)
        remapped = {str(cluster_id_remap.get(int(cid), int(cid))) if cid.isdigit() else cid for cid in before}
        remapped -= {str(cid) for cid in dropped_cluster_ids}
        if remapped != before:
            component.source_cluster_ids = _sort_cluster_ids(remapped)
            if component.component_id:
                plan.refresh_ids.add(component.component_id)
        if component.component_id and _root_cluster_ids(component.source_cluster_ids) & changed_cluster_ids:
            plan.refresh_ids.add(component.component_id)

    new_components: list[tuple[Component, str | None]] = []
    verdicts: dict[Verdict, int] = {Verdict.ADD: 0, Verdict.UPDATE: 0, Verdict.NOOP: 0}
    routing_rows: list[dict] = []
    for cc in delta_cluster_analysis.cluster_components:
        if cc.existing_component_id is not None:
            existing = component_index.get(cc.existing_component_id)
            if existing is None:
                logger.warning(
                    "[stitch] hallucinated existing_component_id=%r, treating %r as ADD",
                    cc.existing_component_id,
                    cc.name,
                )
            else:
                verdict = _classify_verdict(cc, existing_found=True)
                verdicts[verdict] += 1
                routing_rows.append(
                    {
                        "verdict": verdict,
                        "id": existing.component_id,
                        "name": existing.name,
                        "clusters": _sort_cluster_ids(set(cc.cluster_ids)),
                        "redetail": cc.redetail_needed,
                        "parent": None,
                    }
                )
                if cc.redetail_needed:
                    if cc.name and cc.name != existing.name:
                        existing.name = cc.name
                    if cc.description and cc.description != existing.description:
                        existing.description = cc.description
                    updated = _sort_cluster_ids(
                        set(existing.source_cluster_ids) | set(_source_cluster_ids(set(cc.cluster_ids)))
                    )
                    if updated != existing.source_cluster_ids:
                        existing.source_cluster_ids = updated
                        if existing.component_id:
                            plan.refresh_ids.add(existing.component_id)
                continue

        verdicts[Verdict.ADD] += 1
        routing_rows.append(
            {
                "verdict": Verdict.ADD,
                "id": "?",
                "name": cc.name,
                "clusters": _sort_cluster_ids(set(cc.cluster_ids)),
                "redetail": True,
                "parent": cc.parent_id,
            }
        )
        new_component = Component(
            name=cc.name,
            description=cc.description or "",
            key_entities=[],
            source_group_names=[cc.name],
            source_cluster_ids=_source_cluster_ids(set(cc.cluster_ids)),
        )
        new_components.append((new_component, cc.parent_id))

    if new_components:
        _attach_new_components(new_components, root_analysis, sub_analyses, component_index, plan)

    if delta_cluster_analysis.cluster_components:
        _log_stitch_summary(routing_rows, verdicts, plan.refresh_ids, plan.detail_ids)

    return plan


def _attach_new_components(
    new_components: list[tuple[Component, str | None]],
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    component_index: dict[str, Component],
    plan: IncrementalUpdatePlan,
) -> None:
    """Insert each new component under its requested parent, then assign IDs."""
    for component, parent_id in new_components:
        target_analysis = _scope_for_parent(parent_id, root_analysis, sub_analyses, component_index)
        target_analysis.components.append(component)

    # Assign hierarchical IDs only to brand-new components (preserves survivors).
    for analysis in [root_analysis, *sub_analyses.values()]:
        if any(not c.component_id for c in analysis.components):
            parent_id_for_scope = _parent_id_for_scope(analysis, root_analysis, sub_analyses)
            assign_component_ids(analysis, parent_id=parent_id_for_scope, only_new=True)

    for component, _ in new_components:
        if component.component_id:
            plan.refresh_ids.add(component.component_id)
            plan.detail_ids.add(component.component_id)


def _scope_for_parent(
    parent_id: str | None,
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    component_index: dict[str, Component],
) -> AnalysisInsights:
    """Pick the analysis scope (root or a sub-analysis) under which to insert a new component.

    When ``parent_id`` references a leaf with no child scope yet, create one
    on the fly — falling through to ``root_analysis`` would silently re-root
    the new component and break its hierarchical id assignment.
    """
    if not parent_id or parent_id not in component_index:
        return root_analysis
    return sub_analyses.setdefault(
        parent_id,
        AnalysisInsights(description="", components=[], components_relations=[]),
    )


def _parent_id_for_scope(
    analysis: AnalysisInsights,
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> str:
    """Recover the component_id that owns a sub-analysis (so child IDs nest under it)."""
    if analysis is root_analysis:
        return ""
    for component_id, sub in sub_analyses.items():
        if sub is analysis:
            return component_id
    return ""


# ---------------------------------------------------------------------------
# Re-resolution helpers (file_methods + relations on touched scopes only)
# ---------------------------------------------------------------------------
def repopulate_touched_scopes(
    redetail_ids: set[str],
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    cluster_results: dict[str, ClusterResult],
    helpers: ClusterMethodsMixin,
    refresh_files: set[str],
) -> set[str]:
    """Refresh ``file_methods`` for redetail components and rebuild static relations.

    Why per-component (not scope-wide ``populate_file_methods``): the latter
    re-routes every node and would dump global orphans into the scope's leaf
    fallback. Siblings whose clusters didn't change keep their old file_methods
    byte-for-byte. Returns the touched scope ids (``""`` for root).
    """
    touched_scopes: set[str] = set()
    if not redetail_ids:
        return touched_scopes

    node_lookup = _build_node_lookup(helpers.static_analysis, cluster_results)
    repo_dir = helpers.repo_dir

    if any(c.component_id in redetail_ids for c in root_analysis.components):
        touched_scopes.add("")
        for component in root_analysis.components:
            if component.component_id in redetail_ids:
                _refresh_component_file_methods(component, cluster_results, node_lookup, repo_dir, refresh_files)
        helpers.build_static_relations(root_analysis)

    for scope_id, sub in sub_analyses.items():
        if any(c.component_id in redetail_ids for c in sub.components):
            touched_scopes.add(scope_id)
            for component in sub.components:
                if component.component_id in redetail_ids:
                    _refresh_component_file_methods(component, cluster_results, node_lookup, repo_dir, refresh_files)
            helpers.build_static_relations(sub)

    return touched_scopes


def _build_node_lookup(static_analysis, cluster_results: dict[str, ClusterResult]) -> dict[str, MethodEntry]:
    """Map qname -> ``MethodEntry`` built from the live CFG (fresh file/line metadata)."""
    lookup: dict[str, MethodEntry] = {}
    for language in cluster_results:
        try:
            cfg = static_analysis.get_cfg(Language(language))
        except (ValueError, KeyError):
            continue
        for qname, node in cfg.nodes.items():
            if qname in lookup:
                continue
            lookup[qname] = MethodEntry(
                qualified_name=qname,
                start_line=node.line_start,
                end_line=node.line_end,
                node_type=node.type.name,
            )
    return lookup


def _refresh_component_file_methods(
    component: Component,
    cluster_results: dict[str, ClusterResult],
    node_lookup: dict[str, MethodEntry],
    repo_dir: Path | str | None,
    refresh_files: set[str],
) -> None:
    """Rebuild ``component.file_methods`` from live cluster_results, grouped by file.

    When ``refresh_files`` is set, only those files are replaced from live CFG
    data; untouched files keep their previous owner even if broad clusters drift.
    """
    owned_cids = _root_cluster_ids(component.source_cluster_ids)
    if not owned_cids:
        if not refresh_files:
            component.file_methods = []
        return

    repo_root: Path | None
    try:
        repo_root = Path(repo_dir).resolve() if repo_dir else None
    except (TypeError, OSError):
        repo_root = None

    # Normalize paths once so the substring match in _pick_file_for_qname
    # isn't poisoned by absolute snapshot-worktree prefixes.
    qname_to_files: dict[str, set[str]] = {}
    for cr in cluster_results.values():
        for cid, members in cr.clusters.items():
            files = {normalize_repo_path(fp, repo_root) for fp in cr.cluster_to_files.get(cid, set())}
            for qname in members:
                qname_to_files.setdefault(qname, set()).update(files)

    by_file: dict[str, list[MethodEntry]] = {}
    for cr in cluster_results.values():
        for cid in owned_cids:
            members = cr.clusters.get(cid)
            if not members:
                continue
            files_for_cluster = {normalize_repo_path(fp, repo_root) for fp in cr.cluster_to_files.get(cid, set())}
            for qname in members:
                method = node_lookup.get(qname)
                if method is None:
                    continue
                file_path = _pick_file_for_qname(qname, files_for_cluster, qname_to_files)
                if not file_path:
                    continue
                by_file.setdefault(file_path, []).append(method)

    refreshed_groups = [
        FileMethodGroup(
            file_path=fp,
            methods=sorted(
                _dedup_methods(methods),
                key=lambda m: (m.start_line, m.end_line, m.qualified_name),
            ),
        )
        for fp, methods in sorted(by_file.items())
        if not refresh_files or fp in refresh_files
    ]
    if not refresh_files:
        component.file_methods = refreshed_groups
        return

    refreshed_by_file = {group.file_path: group for group in refreshed_groups}
    merged_groups = [group for group in component.file_methods if group.file_path not in refreshed_by_file]
    merged_groups.extend(refreshed_groups)
    component.file_methods = sorted(merged_groups, key=lambda group: group.file_path)


def _pick_file_for_qname(
    qname: str,
    files_for_cluster: set[str],
    qname_to_files: dict[str, set[str]],
) -> str:
    """Resolve which file in *files_for_cluster* a particular qname lives in.

    Prefers a dotted-path substring match (longest wins for specificity).
    Falls back to any file the qname is associated with, then to the first
    file in the cluster.
    """
    dotted = lambda fp: fp.replace("/", ".").rsplit(".", 1)[0]
    matches = [fp for fp in files_for_cluster if dotted(fp) and dotted(fp) in qname]
    if matches:
        return max(matches, key=lambda fp: len(dotted(fp)))
    other = qname_to_files.get(qname, set())
    if other:
        return sorted(other)[0]
    return next(iter(sorted(files_for_cluster)), "")


def _dedup_methods(methods: list[MethodEntry]) -> list[MethodEntry]:
    seen: set[str] = set()
    out: list[MethodEntry] = []
    for method in methods:
        if method.qualified_name in seen:
            continue
        seen.add(method.qualified_name)
        out.append(method)
    return out


# ---------------------------------------------------------------------------
# Prune (deterministic, no LLM)
# ---------------------------------------------------------------------------
def remove_deleted_files(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    live_files: set[str],
) -> set[str]:
    dropped_files: set[str] = _scrub_one_analysis(root_analysis, live_files)
    for sub in sub_analyses.values():
        dropped_files |= _scrub_one_analysis(sub, live_files)
    if dropped_files:
        logger.info("[incremental] dropped %d deleted file(s)", len(dropped_files))
    return dropped_files


def _scrub_one_analysis(analysis: AnalysisInsights, live_files: set[str]) -> set[str]:
    """Drop dead-file references in one ``AnalysisInsights``; returns the dropped paths."""
    dropped: set[str] = set()
    for component in analysis.components:
        kept_groups = []
        for group in component.file_methods:
            if group.file_path in live_files:
                kept_groups.append(group)
            else:
                dropped.add(group.file_path)
        component.file_methods = kept_groups
        component.key_entities = [
            ke for ke in component.key_entities if ke.reference_file is None or ke.reference_file in live_files
        ]
    dropped |= {fp for fp in analysis.files if fp not in live_files}
    analysis.files = {fp: entry for fp, entry in analysis.files.items() if fp in live_files}
    return dropped


def prune_empty_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> set[str]:
    """Remove components with no methods after scrub+repopulation; cascades into sub-analyses.

    Also strips relations referencing removed components by id.
    """
    removed_ids: set[str] = set()

    def _has_methods(component: Component) -> bool:
        return any(group.methods for group in component.file_methods)

    def _collect_empty(analysis: AnalysisInsights) -> None:
        for component in analysis.components:
            if component.component_id and not _has_methods(component):
                removed_ids.add(component.component_id)

    _collect_empty(root_analysis)
    for sub in sub_analyses.values():
        _collect_empty(sub)

    if not removed_ids:
        return set()

    root_analysis.components = [c for c in root_analysis.components if c.component_id not in removed_ids]
    _strip_relations(root_analysis, removed_ids)

    for sub in sub_analyses.values():
        sub.components = [c for c in sub.components if c.component_id not in removed_ids]
        _strip_relations(sub, removed_ids)

    for cid in list(sub_analyses.keys()):
        if cid in removed_ids:
            del sub_analyses[cid]

    return removed_ids


def _strip_relations(analysis: AnalysisInsights, removed_ids: set[str]) -> None:
    analysis.components_relations = [
        rel for rel in analysis.components_relations if rel.src_id not in removed_ids and rel.dst_id not in removed_ids
    ]
