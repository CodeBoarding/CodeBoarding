"""Incremental grouping agent + deterministic stitching helpers.

One LLM call: route the cluster delta to existing components by name, or
create new ones with a ``parent_id``. Stitching back into the live tree
(``stitch_delta``) and downstream refresh/prune is deterministic.
"""

import logging
import os
from enum import StrEnum
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import ClusterAnalysis, ClustersComponent, MetaAnalysisInsights, RelationLLM, ScopeRelations
from agents.analysis_models import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    MethodEntry,
    Relation,
    assign_component_ids,
    index_components_by_id,
    iter_components,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin, _hash_method_body, _read_source_lines
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
    def generate_scope_relations(self, scope: AnalysisInsights, scope_name: str) -> list[RelationLLM]:
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
        merged = merge_relations([Relation.from_llm(r) for r in result.components_relations], [], scope)
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
        all_llm_rels: list[tuple[str, list[RelationLLM]]] = []
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


def _log_stitch_summary(routing_rows: list[dict], verdicts: dict[Verdict, int], redetail_ids: set[str]) -> None:
    lines = [
        f"[stitch] ADD={verdicts[Verdict.ADD]} UPDATE={verdicts[Verdict.UPDATE]} "
        f"NOOP={verdicts[Verdict.NOOP]}  redetail={sorted(redetail_ids)}"
    ]
    for r in routing_rows:
        parent_str = f"under {r['parent']}" if r["parent"] else ""
        redetail_str = "redetail" if r["redetail"] else "keep"
        lines.append(
            f"  {r['verdict']:8s}  {r['id']:5s} {r['name']:40s}  "
            f"clusters={r['clusters']}  {redetail_str}  {parent_str}"
        )
    logger.info("\n".join(lines))


def _log_scope_relations_summary(all_rels: list[tuple[str, list[RelationLLM]]]) -> None:
    lines = ["[scope_relations] LLM-generated inter-component relations:"]
    for scope_name, rels in all_rels:
        for r in rels:
            lines.append(f"  {scope_name:8s}  {r.src_name:40s} --{r.relation}--> {r.dst_name}")
    logger.info("\n".join(lines))


def _ancestor_ids(component_id: str) -> list[str]:
    """Return the ancestor chain of ``component_id`` from immediate parent up.

    Hierarchical IDs encode ancestry by dotted prefix (``"1.1.3" -> "1.1" -> "1"``).
    Returns ``[]`` for top-level (depth-1) ids.
    """
    parts = component_id.split(".")
    return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def _propagate_clusters_to_ancestors(
    component_id: str,
    cluster_ids: set[int],
    component_index: dict[str, Component],
    redetail_ids: set[str],
) -> None:
    """Union ``cluster_ids`` into every ancestor of ``component_id``.

    Maintains the parents-transitively-own-descendants invariant the
    full-analysis path produces naturally: when a leaf gains a cluster, every
    enclosing component must reflect it so the next incremental cycle sees
    the right "affected" set in ``_format_existing_components`` and so
    ``repopulate_touched_scopes`` rebuilds the ancestors' ``file_methods``.
    """
    if not cluster_ids:
        return
    for ancestor_id in _ancestor_ids(component_id):
        ancestor = component_index.get(ancestor_id)
        if ancestor is None:
            continue
        before = set(ancestor.source_cluster_ids)
        merged = before | cluster_ids
        if merged != before:
            ancestor.source_cluster_ids = sorted(merged)
            redetail_ids.add(ancestor_id)


def stitch_delta(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    delta_cluster_analysis: ClusterAnalysis,
    delta: ClusterDelta,
) -> set[str]:
    """Apply the delta ClusterAnalysis to the live tree.

    Routing is by ``existing_component_id`` (decision #4). The LLM either
    sets it to a live component_id (route into that component) or leaves it
    null (create a new component). Name matching is *not* used — that path
    silently forks a duplicate component on every rename.

    Returns the set of component_ids that need to be redetailed (their
    ``source_cluster_ids`` set changed, or they were newly inserted).
    """
    component_index = index_components_by_id(root_analysis, sub_analyses)

    cluster_id_remap = delta.merged_cluster_id_remap()
    dropped_cluster_ids = delta.all_dropped_cluster_ids()
    # Member-set churn (id unchanged) still requires redetail: file_methods
    # depends on cluster MEMBERS, not just IDs.
    changed_cluster_ids: set[int] = {
        cluster_id_remap.get(cid, cid) for ld in delta.by_language.values() for cid in ld.changed_cluster_ids
    }

    redetail_ids: set[str] = set()

    for component in component_index.values():
        before = set(component.source_cluster_ids)
        remapped = {cluster_id_remap.get(cid, cid) for cid in before}
        remapped -= dropped_cluster_ids
        if remapped != before:
            component.source_cluster_ids = sorted(remapped)
            if component.component_id:
                redetail_ids.add(component.component_id)
        if component.component_id and remapped & changed_cluster_ids:
            redetail_ids.add(component.component_id)

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
                        "clusters": sorted(set(cc.cluster_ids)),
                        "redetail": cc.redetail_needed,
                        "parent": None,
                    }
                )
                if cc.redetail_needed:
                    if cc.name and cc.name != existing.name:
                        existing.name = cc.name
                    if cc.description and cc.description != existing.description:
                        existing.description = cc.description
                updated = sorted(set(existing.source_cluster_ids) | set(cc.cluster_ids))
                if updated != existing.source_cluster_ids:
                    existing.source_cluster_ids = updated
                    if existing.component_id and cc.redetail_needed:
                        redetail_ids.add(existing.component_id)
                if existing.component_id:
                    _propagate_clusters_to_ancestors(
                        existing.component_id, set(cc.cluster_ids), component_index, redetail_ids
                    )
                continue

        verdicts[Verdict.ADD] += 1
        routing_rows.append(
            {
                "verdict": Verdict.ADD,
                "id": "?",
                "name": cc.name,
                "clusters": sorted(set(cc.cluster_ids)),
                "redetail": True,
                "parent": cc.parent_id,
            }
        )
        new_component = Component(
            name=cc.name,
            description=cc.description or "",
            key_entities=[],
            source_group_names=[cc.name],
            source_cluster_ids=sorted(set(cc.cluster_ids)),
        )
        new_components.append((new_component, cc.parent_id))

    if new_components:
        _attach_new_components(new_components, root_analysis, sub_analyses, component_index, redetail_ids)

    if delta_cluster_analysis.cluster_components:
        _log_stitch_summary(routing_rows, verdicts, redetail_ids)

    return redetail_ids


def _attach_new_components(
    new_components: list[tuple[Component, str | None]],
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    component_index: dict[str, Component],
    redetail_ids: set[str],
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
            redetail_ids.add(component.component_id)
            _propagate_clusters_to_ancestors(
                component.component_id,
                set(component.source_cluster_ids),
                component_index,
                redetail_ids,
            )


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

    repo_dir = helpers.repo_dir
    node_lookup = _build_node_lookup(helpers.static_analysis, cluster_results, repo_dir)

    if any(c.component_id in redetail_ids for c in root_analysis.components):
        touched_scopes.add("")
        for component in root_analysis.components:
            if component.component_id in redetail_ids:
                _refresh_component_file_methods(component, cluster_results, node_lookup, repo_dir)
        helpers.build_static_relations(root_analysis)

    for scope_id, sub in sub_analyses.items():
        if any(c.component_id in redetail_ids for c in sub.components):
            touched_scopes.add(scope_id)
            for component in sub.components:
                if component.component_id in redetail_ids:
                    _refresh_component_file_methods(component, cluster_results, node_lookup, repo_dir)
            helpers.build_static_relations(sub)

    return touched_scopes


def _build_node_lookup(
    static_analysis: StaticAnalysisResults, cluster_results: dict[str, ClusterResult], repo_dir: Path
) -> dict[str, MethodEntry]:
    """Map qname -> ``MethodEntry`` built from the live CFG (fresh file/line metadata + content_hash)."""
    lookup: dict[str, MethodEntry] = {}
    source_cache: dict[str, list[str] | None] = {}
    for language in cluster_results:
        try:
            cfg = static_analysis.get_cfg(Language(language))
        except (ValueError, KeyError):
            continue
        for qname, node in cfg.nodes.items():
            if qname in lookup:
                continue
            rel_path = os.path.relpath(node.file_path, repo_dir) if os.path.isabs(node.file_path) else node.file_path
            lookup[qname] = MethodEntry(
                qualified_name=qname,
                start_line=node.line_start,
                end_line=node.line_end,
                node_type=node.type.name,
                content_hash=_hash_method_body(
                    _read_source_lines(repo_dir, rel_path, source_cache),
                    node.line_start,
                    node.line_end,
                ),
            )
    return lookup


def _refresh_component_file_methods(
    component: Component,
    cluster_results: dict[str, ClusterResult],
    node_lookup: dict[str, MethodEntry],
    repo_dir: Path | str | None = None,
) -> None:
    """Rebuild ``component.file_methods`` from live cluster_results, grouped by file.

    Methods missing from ``node_lookup`` (source deleted) are dropped. Paths
    are normalised to repo-relative posix to match ``analysis.json`` indexes —
    without this the wrapper's ``file_to_component`` lookup misses on the
    next incremental cycle. Dedup is by qname (vs. the mixin's
    span-keyed most-specific-qname dedup), since the incremental path already
    has canonical qnames from the cluster snapshot.
    """
    owned_cids = set(component.source_cluster_ids)
    if not owned_cids:
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

    component.file_methods = [
        FileMethodGroup(
            file_path=fp,
            methods=sorted(
                _dedup_methods(methods),
                key=lambda m: (m.start_line, m.end_line, m.qualified_name),
            ),
        )
        for fp, methods in sorted(by_file.items())
    ]


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
