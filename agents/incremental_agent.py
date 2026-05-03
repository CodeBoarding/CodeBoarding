"""Incremental grouping agent: thin clone of ``AbstractionAgent.step_clusters_grouping``.

Mirrors the full-analysis shape: one LLM call that takes the cluster *delta*
(``new_cluster_ids + changed_cluster_ids``) plus the existing component tree by
name, and returns a ``ClusterAnalysis`` whose entries either reuse an existing
component name (route the cluster there) or invent a new one with a
``parent_id`` pointing at where to attach. Stitching back into the live analysis
is deterministic — no second LLM call.
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
        # Routing decisions need at most a single representative qname's source
        # to disambiguate; the rest of the toolkit (read_file, read_packages,
        # read_structure, read_file_structure) is wide-scope code reading that
        # the model uses speculatively when given the full set. Constraining
        # the toolkit here keeps the ReAct loop bounded.
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
        # Two-tier rendering per the design plan: components whose files
        # overlap with the affected clusters get full info (name +
        # description + cluster ids), every other component gets a single
        # "id (name)" line so the LLM can still pick it as a routing
        # target without bloating the prompt with full descriptions.
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

    Two-tier rendering driven by the design plan: components that own at
    least one file in *affected_files* are emitted with their full
    description (the LLM is most likely to modify these or attach new
    components under them), every other component gets a single
    ``id "name"`` line so it remains a valid routing target without
    bloating the prompt with descriptions the LLM probably won't need.

    When *affected_files* is None, every component gets the full line
    (legacy / no-scope-known behaviour).
    """
    all_components: list[Component] = list(root_analysis.components)
    for sub in sub_analyses.values():
        all_components.extend(sub.components)

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
    if len(desc) > 200:
        desc = desc[:197] + "..."
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
    component_index = _index_components(root_analysis, sub_analyses)
    name_lookup = {component.name.lower(): component for component in component_index.values()}

    cluster_id_remap = delta.merged_cluster_id_remap()
    dropped_cluster_ids = delta.all_dropped_cluster_ids()
    # Clusters whose member set shifted (without changing id) — components
    # that own them must also be re-detailed: their file_methods reflect the
    # OLD member set and would otherwise drift away from the live CFG.
    changed_cluster_ids: set[int] = {
        cluster_id_remap.get(cid, cid) for ld in delta.by_language.values() for cid in ld.changed_cluster_ids
    }

    redetail_ids: set[str] = set()

    # Step 1 — apply the cluster_id_remap and drop removed clusters across every component.
    # Run before merging delta ids so we don't accidentally re-add a remapped id below.
    for component in component_index.values():
        before = set(component.source_cluster_ids)
        remapped = {cluster_id_remap.get(cid, cid) for cid in before}
        remapped -= dropped_cluster_ids
        if remapped != before:
            component.source_cluster_ids = sorted(remapped)
            if component.component_id:
                redetail_ids.add(component.component_id)
        # Even when source_cluster_ids stayed numerically the same, mark the
        # component for redetail if any of its clusters had their member set
        # mutated by the delta — file_methods is a function of cluster
        # MEMBERS, not just cluster IDs.
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


def _index_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, Component]:
    index: dict[str, Component] = {}
    for component in root_analysis.components:
        if component.component_id:
            index[component.component_id] = component
    for sub in sub_analyses.values():
        for component in sub.components:
            if component.component_id:
                index[component.component_id] = component
    return index


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
    """Refresh ``file_methods`` for components whose clusters changed and rebuild
    static relations on every scope that contains one of those components.

    Per-component repopulation:
      - For each component in ``redetail_ids``, recompute its ``file_methods``
        directly from ``cluster_results``: take every node in every cluster the
        component declares ownership of and bucket by file. Siblings whose
        clusters DIDN'T change keep their existing ``file_methods`` exactly as
        they were so test-style "only-the-target-changed" invariants hold.
      - The scope-wide ``populate_file_methods`` previously used here would
        re-route ALL nodes across every component in the scope, which makes
        the LEAF "fallback" component absorb all orphan nodes globally.

    After per-component refresh, ``build_static_relations`` re-runs at scope
    level (cheap and order-independent) so component-to-component edges
    reflect the new ownership.

    Returns the set of scope ids that were touched (``""`` for root).
    """
    touched_scopes: set[str] = set()
    if not redetail_ids:
        return touched_scopes

    node_lookup = _build_node_lookup(helpers.static_analysis, cluster_results)
    repo_dir = getattr(helpers, "repo_dir", None) or getattr(helpers, "repository_path", None)

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
    """Map every clustered qualified name to a fresh ``MethodEntry`` built from
    the live CFG so ``file_methods`` carries up-to-date file/line metadata."""
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
    """Rebuild ``component.file_methods`` from the live cluster_results.

    Walks the component's ``source_cluster_ids`` across every language's
    ``ClusterResult``, pulls each cluster's members, looks up each qname's
    file path in the live CFG (via ``node_lookup`` which was built from
    ``static_analysis``), and groups by file. Methods missing from the
    lookup are dropped (their source was deleted). File paths are
    normalised to repo-relative form (matching what ``analysis.json``
    stores in ``files`` and ``methods_index``); without this the wrapper's
    ``file_to_component`` lookup would silently miss every method on the
    next incremental cycle.
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

    # qname -> file_path lookup also lives on node_lookup_files, built
    # alongside node_lookup; we encode it inline by re-deriving from the
    # ClusterResult's cluster_to_files which is the canonical mapping.
    # Paths are normalized to repo-relative posix here once so the substring
    # match in ``_pick_file_for_qname`` doesn't get poisoned by absolute
    # snapshot-worktree prefixes (``/private/var/.../snapshot-worktree/...``).
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

    Prefers an exact substring match between the file's dotted form and the
    qname (so ``a/b/foo.py`` matches ``a.b.foo.bar``). Falls back to any
    file the qname is otherwise associated with, then to the first file in
    the cluster as a last resort.
    """
    dotted = lambda fp: fp.replace("/", ".").rsplit(".", 1)[0]
    matches = [fp for fp in files_for_cluster if dotted(fp) and dotted(fp) in qname]
    if matches:
        # If multiple match, prefer the longest dotted form (most specific).
        return max(matches, key=lambda fp: len(dotted(fp)))
    other = qname_to_files.get(qname, set())
    if other:
        # Use any file the qname belongs to; deterministic via sort.
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
    """Drop every reference to a file no longer on disk before cluster math.

    Walks every component and the top-level ``files`` index, removing any
    ``file_methods`` group whose ``file_path`` is not in *live_files*, any
    ``key_entities`` whose ``reference_file`` is gone, and any
    ``analysis.files`` entry for a vanished path. After this pass a deleted
    file simply doesn't exist in the analysis — the cluster pipeline can
    then operate on a consistent baseline and the prune step naturally
    sweeps any component whose every file group disappeared.

    This is the right home for delete handling because the LLM-side cluster
    routing is incidental: a component's "real" home is whatever files its
    file_methods point at, and the file going away is the unambiguous
    signal that the component (or part of it) is gone. Returns the set of
    file paths that were dropped (for logging/telemetry).
    """
    dropped_files: set[str] = set()

    def _drop_from_analysis(analysis: AnalysisInsights) -> None:
        for component in analysis.components:
            kept_groups = []
            for group in component.file_methods:
                if group.file_path in live_files:
                    kept_groups.append(group)
                else:
                    dropped_files.add(group.file_path)
            component.file_methods = kept_groups
            component.key_entities = [
                ke for ke in component.key_entities if ke.reference_file is None or ke.reference_file in live_files
            ]
        kept_files = {fp: entry for fp, entry in analysis.files.items() if fp in live_files}
        for fp in set(analysis.files) - set(kept_files):
            dropped_files.add(fp)
        analysis.files = kept_files

    _drop_from_analysis(root_analysis)
    for sub in sub_analyses.values():
        _drop_from_analysis(sub)

    if dropped_files:
        logger.info(
            "[incremental] scrub: dropped %d deleted file(s) from analysis: %s",
            len(dropped_files),
            sorted(dropped_files)[:10],
        )
    return dropped_files


def prune_empty_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> set[str]:
    """Remove components whose ``file_methods`` is empty after scrub + repopulation.

    The user-visible criterion for "this component still represents code" is
    whether it owns any methods at all. With ``scrub_deleted_files`` running
    first, deleted source files are gone from every component's
    ``file_methods``; any component left with no file groups (or only empty
    groups) has nothing to point at and is removed here.

    Cascades into sub-analyses that hung off pruned components and strips
    relations that reference any removed component (by id or by name).
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
