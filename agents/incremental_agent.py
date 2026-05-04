"""Incremental grouping agent + deterministic stitching helpers.

One LLM call: route the cluster delta to existing components by name, or
create new ones with a ``parent_id``. Stitching back into the live tree
(``stitch_delta``) and downstream refresh/prune is deterministic.
"""

import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    Component,
    FileMethodGroup,
    MetaAnalysisInsights,
    MethodEntry,
    assign_component_ids,
    index_components_by_id,
    iter_components,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.prompts import get_system_message
from agents.prompts.incremental_grouping import get_incremental_grouping_message
from agents.validation import (
    ValidationContext,
    validate_cluster_coverage,
)
from diagram_analysis.cluster_delta import ClusterDelta
from diagram_analysis.io_utils import normalize_repo_path
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
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
        # Constrain the toolkit: routing only needs source disambiguation, not
        # full code-reading tools — keeps the ReAct loop bounded.
        super().__init__(
            repo_dir,
            static_analysis,
            get_system_message(),
            agent_llm,
            parsing_llm,
            tool_names=["read_source_reference"],
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
    def step_group_delta(
        self,
        delta: ClusterDelta,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> ClusterAnalysis:
        """LLM call: route delta clusters to existing or new components."""
        affected_cluster_ids = delta.all_affected_cluster_ids()
        if not affected_cluster_ids:
            logger.info("[IncrementalAgent] No affected cluster ids; skipping LLM call.")
            return ClusterAnalysis(cluster_components=[])

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"
        # Two-tier rendering: components touching affected files get full info,
        # the rest get a single "id (name)" line to keep them as routing targets
        # without bloating the prompt.
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

        result = self._validation_invoke(
            prompt,
            ClusterAnalysis,
            validators=[validate_cluster_coverage],
            context=ValidationContext(
                cluster_results=cluster_results,
                expected_cluster_ids=affected_cluster_ids,
            ),
            max_validation_attempts=3,
        )
        return result


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
def stitch_delta(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    delta_cluster_analysis: ClusterAnalysis,
    delta: ClusterDelta,
) -> set[str]:
    """Apply the delta ClusterAnalysis to the live tree.

    Returns the set of component_ids that need to be redetailed (their
    ``source_cluster_ids`` set changed, or they were newly inserted).
    """
    component_index = index_components_by_id(root_analysis, sub_analyses)
    name_lookup = {component.name.lower(): component for component in component_index.values()}

    cluster_id_remap = delta.merged_cluster_id_remap()
    dropped_cluster_ids = delta.all_dropped_cluster_ids()
    # Member-set churn (id unchanged) still requires redetail: file_methods
    # depends on cluster MEMBERS, not just IDs.
    changed_cluster_ids: set[int] = {
        cluster_id_remap.get(cid, cid) for ld in delta.by_language.values() for cid in ld.changed_cluster_ids
    }

    redetail_ids: set[str] = set()

    # Step 1 — remap and drop removed clusters before merging delta ids in,
    # so a remapped id can't get re-added below.
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

    # Step 2 — route delta cluster_components to existing or new components.
    new_components: list[tuple[Component, str | None]] = []
    for cc in delta_cluster_analysis.cluster_components:
        existing = name_lookup.get(cc.name.lower())
        if existing is not None:
            updated = sorted(set(existing.source_cluster_ids) | set(cc.cluster_ids))
            if updated != existing.source_cluster_ids:
                existing.source_cluster_ids = updated
                if existing.component_id:
                    redetail_ids.add(existing.component_id)
            continue

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


def _scope_for_parent(
    parent_id: str | None,
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    component_index: dict[str, Component],
) -> AnalysisInsights:
    """Pick the analysis scope (root or a sub-analysis) under which to insert a new component."""
    if not parent_id or parent_id not in component_index:
        return root_analysis
    if parent_id in sub_analyses:
        return sub_analyses[parent_id]
    return root_analysis


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

    node_lookup = _build_node_lookup(helpers.static_analysis, cluster_results)
    repo_dir = helpers.repo_dir

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


def _build_node_lookup(static_analysis, cluster_results: dict[str, ClusterResult]) -> dict[str, MethodEntry]:
    """Map qname -> ``MethodEntry`` built from the live CFG (fresh file/line metadata)."""
    lookup: dict[str, MethodEntry] = {}
    for language in cluster_results:
        try:
            cfg = static_analysis.get_cfg(language)
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
def scrub_deleted_files(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    live_files: set[str],
) -> set[str]:
    """Drop every reference to a file not in *live_files*; returns the dropped paths.

    Why this runs before cluster math: orphan-routed methods (assigned by
    file co-location, not cluster membership) are invisible to the cluster
    delta. Without an explicit scrub, deleting a file containing only such
    methods leaves stale ``file_methods`` / ``key_entities`` /
    ``analysis.files`` entries forever.
    """
    dropped_files: set[str] = _scrub_one_analysis(root_analysis, live_files)
    for sub in sub_analyses.values():
        dropped_files |= _scrub_one_analysis(sub, live_files)

    if dropped_files:
        logger.info(
            "[incremental] scrub: dropped %d deleted file(s) from analysis: %s",
            len(dropped_files),
            sorted(dropped_files)[:10],
        )
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

    Also strips relations referencing removed components (by id or name).
    """
    removed_ids: set[str] = set()
    removed_names: set[str] = set()

    def _has_methods(component: Component) -> bool:
        return any(group.methods for group in component.file_methods)

    def _collect_empty(analysis: AnalysisInsights) -> None:
        for component in analysis.components:
            if component.component_id and not _has_methods(component):
                removed_ids.add(component.component_id)
                if component.name:
                    removed_names.add(component.name)

    _collect_empty(root_analysis)
    for sub in sub_analyses.values():
        _collect_empty(sub)

    if not removed_ids:
        return set()

    root_analysis.components = [c for c in root_analysis.components if c.component_id not in removed_ids]
    _strip_relations(root_analysis, removed_ids, removed_names)

    for sub in sub_analyses.values():
        sub.components = [c for c in sub.components if c.component_id not in removed_ids]
        _strip_relations(sub, removed_ids, removed_names)

    for cid in list(sub_analyses.keys()):
        if cid in removed_ids:
            del sub_analyses[cid]

    return removed_ids


def _strip_relations(analysis: AnalysisInsights, removed_ids: set[str], removed_names: set[str]) -> None:
    analysis.components_relations = [
        rel
        for rel in analysis.components_relations
        if rel.src_id not in removed_ids
        and rel.dst_id not in removed_ids
        and rel.src_name not in removed_names
        and rel.dst_name not in removed_names
    ]
