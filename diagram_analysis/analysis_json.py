import base64
import hashlib
import logging
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from agents.agent_responses import (
    Component,
    Relation,
    AnalysisInsights,
    RelationCallSite,
    RelationEdge,
    SourceCodeReference,
)
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from agents.relation_edges import merge_relations_by_pair
from diagram_analysis.exceptions import AnalysisFormatError, SymbolIdCollisionError
from infra.analysis_format import ANALYSIS_FORMAT_VERSION, RECORD_WIDTH, SYMBOL_ID_BYTES
from infra.repo_utils.path_utils import normalize_repo_path

logger = logging.getLogger(__name__)


class RelationEdgeJson(BaseModel):
    source: str = Field(description="Method key '<file_path>|<qualified_name>' of the source.")
    target: str = Field(description="Method key '<file_path>|<qualified_name>' of the target.")
    call_sites: list[RelationCallSite] = Field(default_factory=list)
    description: str = Field(default="", description="Short explanation of how source reaches or configures target.")


class RelationJson(Relation):
    """Relation subclass that includes src_id/dst_id and static analysis evidence in JSON serialization."""

    key_edges: list[RelationEdgeJson] = Field(
        default_factory=list,
        description="Important source-to-target edges, by method key.",
    )
    src_id: str = Field(default="", description="Component ID of the source.")
    dst_id: str = Field(default="", description="Component ID of the destination.")
    is_static: bool = Field(default=False, description="True if derived from static CFG analysis.")
    all_edges: list[RelationEdgeJson] = Field(
        default_factory=list,
        description="All known source-to-target edges for this relation.",
    )


class ComponentJson(Component):
    # Override to include in JSON serialization (parent has exclude=True)
    component_id: str = Field(description="Deterministic unique identifier for this component.")
    source_cluster_ids: list[str] = Field(
        description="List of cluster IDs from CFG analysis that this component encompasses.",
        default_factory=list,
    )
    can_expand: bool = Field(
        description="Whether the component can be expanded in detail or not.",
        default=False,
    )
    file_methods: list["ComponentFileMethodGroupJson"] = Field(
        description="Component method references grouped by file. Each methods entry stores only qualified_name.",
        default_factory=list,
    )
    # Nested sub-analysis for expanded components
    components: list["ComponentJson"] = Field(
        description="Sub-components if expanded, empty otherwise.", default_factory=list
    )
    components_relations: list[RelationJson] = Field(
        description="Relations among sub-components if expanded, empty otherwise.",
        default_factory=list,
    )


class NotAnalyzedFile(BaseModel):
    path: str = Field(description="Relative path of the file.")
    reason: str = Field(description="Exclusion reason for the file.")


class FileCoverageSummary(BaseModel):
    total_files: int = Field(description="Total number of text files in the repository.")
    analyzed: int = Field(description="Number of files included in the analysis.")
    not_analyzed: int = Field(description="Number of files excluded from the analysis.")
    not_analyzed_by_reason: dict[str, int] = Field(
        default_factory=dict, description="Count of excluded files grouped by reason."
    )


class FileCoverageReport(BaseModel):
    version: int = Field(default=1, description="Schema version of the file coverage report.")
    generated_at: str = Field(description="ISO timestamp of when the report was generated.")
    analyzed_files: list[str] = Field(description="List of analyzed file paths.")
    not_analyzed_files: list[NotAnalyzedFile] = Field(description="List of excluded files with optional reasons.")
    summary: FileCoverageSummary = Field(description="Aggregated coverage counts.")


class AnalysisMetadata(BaseModel):
    generated_at: str = Field(description="ISO timestamp of when the analysis was generated.")
    source_tree_hash: str = Field(
        default="",
        description="SHA-256 over the sorted per-file content hashes; the source-state version key.",
    )
    repo_name: str = Field(description="Name of the analyzed repository.")
    depth_level: int = Field(description="Maximum depth level actually reached by this analysis.")
    depth_cap: int = Field(
        description="Safety-valve depth ceiling the run was configured with (see --depth-cap); "
        "may exceed depth_level when separability stopped expansion before the cap. "
        "The value future incremental/partial runs reuse as their own cap."
    )
    file_coverage_summary: FileCoverageSummary = Field(
        default_factory=lambda: FileCoverageSummary(
            total_files=0, analyzed=0, not_analyzed=0, not_analyzed_by_reason={}
        ),
        description="Lightweight file coverage counts.",
    )
    tree_spec: dict = Field(
        default_factory=dict,
        description="The tree specification the components were drawn from: per scope, the prefixes "
        "and words each component owns. Drafted on a full analysis; incremental and partial runs "
        "replay it, so the partition cannot move underneath unchanged code. Empty on an analysis "
        "written before it existed.",
    )
    format_version: str = Field(default=ANALYSIS_FORMAT_VERSION, description="Layout of the analysis.json document.")


class ComponentFileMethodGroupJson(BaseModel):
    file_path: str = Field(description="Relative path to the source file.")
    methods: list[str] = Field(
        default_factory=list,
        description="Qualified method/function names assigned to this component in this file.",
    )


def _build_files_index_from_analysis(
    analysis: AnalysisInsights,
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
) -> dict[str, FileEntry]:
    """Build a unified file index from the root and sub-analyses."""
    analyses = [analysis]
    if sub_analyses:
        analyses.extend(sub_analysis for sub_analysis, _ in sub_analyses.values())

    files_index: dict[str, FileEntry] = {}
    for current_analysis in analyses:
        for file_path, entry in current_analysis.files.items():
            files_index.setdefault(file_path, FileEntry()).merge_from(entry)
    return files_index


def _method_key(file_path: str, qualified_name: str) -> str:
    """Canonical method key ('<file_path>|<qualified_name>')."""
    return f"{file_path}|{qualified_name}"


def _source_reference_method_key(reference: SourceCodeReference, repo_dir: Path) -> str:
    file_path = normalize_repo_path(reference.reference_file or "", repo_dir)
    return _method_key(file_path, reference.qualified_name)


def _relativize_key_entities(key_entities: list[SourceCodeReference], repo_dir: Path) -> list[SourceCodeReference]:
    """Make each key entity's ``reference_file`` repo-relative, like every other path in the document.

    The reference resolver leaves ``reference_file`` absolute; unlike ``file_methods`` and relation
    endpoints it isn't normalized on the way in, so without this it serializes as an absolute path
    that no consumer can match against the repo tree.
    """
    return [
        (
            entity.model_copy(update={"reference_file": normalize_repo_path(entity.reference_file, repo_dir)})
            if entity.reference_file
            else entity
        )
        for entity in key_entities
    ]


def _compute_depth_level(
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None,
) -> int:
    """Compute the maximum depth level from the sub_analyses structure.

    Returns 1 if there are no sub-analyses (root only), 2 if there is one level of
    sub-analyses, etc. Recursively traverses nested sub-analyses to find true max depth.
    """
    if not sub_analyses:
        return 1

    def get_depth(analysis: AnalysisInsights, visited: set[str]) -> int:
        """Recursively compute depth for a sub-analysis."""
        max_depth = 1
        for comp in analysis.components:
            if comp.component_id in sub_analyses and comp.component_id not in visited:
                visited.add(comp.component_id)
                sub_analysis, _ = sub_analyses[comp.component_id]
                child_depth = 1 + get_depth(sub_analysis, visited)
                max_depth = max(max_depth, child_depth)
                visited.remove(comp.component_id)
        return max_depth

    max_depth = 1
    for cid, (sub_analysis, _) in sub_analyses.items():
        # Only compute depth for root-level sub-analyses (not referenced by others)
        is_root_level = True
        for other_cid, (other_analysis, _) in sub_analyses.items():
            if other_cid != cid:
                for comp in other_analysis.components:
                    if comp.component_id == cid:
                        is_root_level = False
                        break
            if not is_root_level:
                break

        if is_root_level:
            visited = {cid}
            depth = 1 + get_depth(sub_analysis, visited)
            max_depth = max(max_depth, depth)

    return max_depth


def build_unified_analysis_json(
    analysis: AnalysisInsights,
    expandable_components: list[Component],
    repo_name: str,
    repo_dir: Path,
    source_tree_hash: str,
    depth_cap: int,
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None,
    file_coverage_summary: FileCoverageSummary | None = None,
    tree_spec: dict | None = None,
) -> str:
    """Build the analysis.json document for an analysis and its nested sub-analyses.

    ``repo_dir`` relativizes relation-edge file paths. ``source_tree_hash`` is the precomputed
    whole-tree version key (the caller fingerprints the tree once and reuses it across saves).
    ``depth_level`` (realized depth) is computed automatically from the sub_analyses structure;
    ``depth_cap`` is the run's configured ceiling, persisted separately so a run that stopped
    short of the cap (separability-gated) doesn't leave a future incremental/partial run capped
    at the shallower realized depth.

    The document states each fact once:

    - ``files[path].methods[qualified_name] = [start_line, end_line, type, content_hash, id]``.
      Everything else cites a method by that id; a method outside the index is cited by its
      '<file_path>|<qualified_name>' key, which holds '|' where an id never does.
    - ``edges`` lists every method edge once: ``[source, target, [[line, column], ...]]``, plus
      its description when it has one.
    - Only leaves list ``file_methods``; a parent's members are its children's.
    - Relations omit ``all_edges``, which a reader rebuilds from ``edges`` (see ``_edges_by_relation``).
    - A key edge is ``[source, target, description?, call_sites?]``; its call sites are those of
      the same edge in its relation unless written.

    Where a rebuild would not give back what Core holds, the field is written out.
    """
    files_index = _build_files_index_from_analysis(analysis, sub_analyses)
    ids = _symbol_ids(files_index)

    relations = merge_relations_by_pair(analysis.components_relations, include_relation=True)
    rows_by_pair: dict[tuple[str, str], list] = {}
    for relation in relations:
        for edge in relation.all_edges:
            pair = (
                _source_reference_method_key(edge.source, repo_dir),
                _source_reference_method_key(edge.target, repo_dir),
            )
            rows_by_pair.setdefault(pair, _edge_row(edge, ids, repo_dir))
    edges = [rows_by_pair[pair] for pair in sorted(rows_by_pair)]

    processed_ids: set[str] = set()
    components = [
        _component_json(component, expandable_components, repo_dir, ids, sub_analyses, processed_ids)
        for component in analysis.components
    ]
    held = _edges_by_relation(edges, *_leaf_lineage(components))
    _write_nested_relations(components, held, ids, repo_dir)

    metadata = AnalysisMetadata(
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_tree_hash=source_tree_hash,
        repo_name=repo_name,
        depth_level=_compute_depth_level(sub_analyses),
        depth_cap=depth_cap,
        file_coverage_summary=file_coverage_summary
        or FileCoverageSummary(total_files=0, analyzed=0, not_analyzed=0, not_analyzed_by_reason={}),
        tree_spec=tree_spec or {},
    )
    document = {
        "metadata": metadata.model_dump(mode="json"),
        "description": analysis.description,
        # A pathless file has no entry of its own; it is written only to index its methods.
        "files": {
            file_path: _file_json(file_path, entry, ids)
            for file_path, entry in files_index.items()
            if file_path or entry.methods
        },
        "components": components,
        "components_relations": [
            _relation_json(relation, held.get(("", relation.src_id, relation.dst_id), []), ids, repo_dir)
            for relation in relations
        ],
        "edges": edges,
    }
    return dump_analysis_document(document)


def dump_analysis_document(data: dict) -> str:
    """Serialize *data* as compact JSON with one short record per line.

    A record whose compact form, trailing comma included, fits in ``RECORD_WIDTH`` characters
    stays on one line; a longer one puts each item on its own line, one space deeper. Why: a
    change to one method, edge or relation then touches only its own lines of a git diff.
    """
    lines: list[str] = []
    _emit_record(data, "", "", "", lines)
    return "\n".join(lines) + "\n"


def parse_unified_analysis(
    data: dict,
) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]]:
    """Parse an analysis.json document into the root AnalysisInsights and its sub-analyses.

    Returns:
        (root_analysis, sub_analyses_dict) where sub_analyses_dict maps component_id
        to its nested AnalysisInsights.
    """
    require_format_version(data.get("metadata") or {})
    reader = _DocumentReader(data)
    root_analysis = reader.analysis(data.get("description", ""), data["components"], data["components_relations"], "")
    if reader.missing:
        logger.warning("Missing method index entry for %d ref(s): %s", len(reader.missing), reader.missing)

    root_analysis.files = {path: FileEntry().merge_from(entry) for path, entry in reader.files.items()}
    for sub in reader.sub_analyses.values():
        # Same shape a live scope gets from ``build_files_index``: only the files its components
        # own. The root carries the whole index, so a save loses nothing.
        owned = {group.file_path for component in sub.components for group in component.file_methods}
        sub.files = {path: FileEntry().merge_from(entry) for path, entry in reader.files.items() if path in owned}

    return root_analysis, reader.sub_analyses


def require_format_version(metadata: dict) -> None:
    """Raise ``AnalysisFormatError`` unless *metadata* is from a document in the format this Core reads."""
    version = metadata.get("format_version")
    if version != ANALYSIS_FORMAT_VERSION:
        raise AnalysisFormatError(version, ANALYSIS_FORMAT_VERSION)


def build_id_to_name_map(root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> dict[str, str]:
    """Build a mapping from component_id to component name across all analysis levels."""
    id_to_name: dict[str, str] = {c.component_id: c.name for c in root_analysis.components}
    for sub_analysis in sub_analyses.values():
        for comp in sub_analysis.components:
            id_to_name[comp.component_id] = comp.name
    return id_to_name


def _symbol_ids(files_index: dict[str, FileEntry]) -> dict[str, str]:
    """Map each indexed method's key to its id; two methods sharing an id fail the write."""
    ids: dict[str, str] = {}
    owners: dict[str, str] = {}
    for file_path, entry in files_index.items():
        for method in entry.methods:
            key = _method_key(file_path, method.qualified_name)
            symbol_id = _symbol_id(file_path, method.qualified_name)
            if owners.setdefault(symbol_id, key) != key:
                raise SymbolIdCollisionError(symbol_id, owners[symbol_id], key)
            ids[key] = symbol_id
    return ids


def _symbol_id(file_path: str, qualified_name: str) -> str:
    encoded = json.dumps([file_path, qualified_name], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.blake2b(encoded, digest_size=SYMBOL_ID_BYTES).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def _cite(reference: SourceCodeReference, ids: dict[str, str], repo_dir: Path) -> str:
    key = _source_reference_method_key(reference, repo_dir)
    return ids.get(key, key)


def _edge_row(edge: RelationEdge, ids: dict[str, str], repo_dir: Path) -> list:
    row = [
        _cite(edge.source, ids, repo_dir),
        _cite(edge.target, ids, repo_dir),
        [[site.line, site.column] for site in edge.call_sites],
    ]
    if edge.description:
        row.append(edge.description)
    return row


def _key_edge_row(
    edge: RelationEdge, call_sites_of: dict[tuple[str, str], list], ids: dict[str, str], repo_dir: Path
) -> list:
    source, target, call_sites = _edge_row(edge, ids, repo_dir)[:3]
    if call_sites != call_sites_of.get((source, target), []):
        return [source, target, edge.description, call_sites]
    return [source, target, edge.description] if edge.description else [source, target]


def _file_json(file_path: str, entry: FileEntry, ids: dict[str, str]) -> dict:
    return {
        "content_hash": entry.content_hash,
        "module_hash": entry.module_hash,
        "methods": {
            method.qualified_name: [
                method.start_line,
                method.end_line,
                method.node_type,
                method.content_hash,
                ids[_method_key(file_path, method.qualified_name)],
            ]
            for method in entry.methods
        },
    }


def _component_json(
    component: Component,
    expandable_components: list[Component],
    repo_dir: Path,
    ids: dict[str, str],
    sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]] | None,
    processed_ids: set[str],
) -> dict:
    """A component and its subtree; nested relations stay in memory until ``_write_nested_relations``."""
    component_id = component.component_id
    if component_id in processed_ids:
        logger.warning(f"Component {component.name} (ID: {component_id}) already processed, skipping expansion")
        can_expand = False
    else:
        processed_ids.add(component_id)
        can_expand = any(c.component_id == component_id for c in expandable_components)

    if not can_expand and sub_analyses and component_id in sub_analyses:
        # Serializing skips the subtree below, and analysis.json is the store — so this
        # discards an already-analysed subtree permanently. Callers must mark a component
        # holding a sub-analysis as expandable; say so loudly rather than losing the work.
        logger.warning(
            "Component %s (ID: %s) holds an analysed sub-analysis but is not marked expandable; "
            "%d sub-component(s) will not be written",
            component.name,
            component_id,
            len(sub_analyses[component_id][0].components),
        )

    children: list[dict] = []
    relations: list[Relation] = []
    if can_expand and sub_analyses and component_id in sub_analyses:
        sub_analysis, sub_expandable = sub_analyses[component_id]
        children = [
            _component_json(child, sub_expandable, repo_dir, ids, sub_analyses, processed_ids)
            for child in sub_analysis.components
        ]
        relations = merge_relations_by_pair(sub_analysis.components_relations, include_relation=True)

    members = [
        ids.get(key, key)
        for group in component.file_methods
        for key in dict.fromkeys(_method_key(group.file_path, method.qualified_name) for method in group.methods)
    ]
    data: dict = {
        "name": component.name,
        "component_id": component_id,
        "description": component.description,
        "key_entities": [
            entity.model_dump(mode="json", exclude_none=True)
            for entity in _relativize_key_entities(component.key_entities, repo_dir)
        ],
        "source_cluster_ids": component.source_cluster_ids,
        "can_expand": can_expand,
    }
    if not children or set(members) != _members_of(children):
        data["file_methods"] = members
    data["components"] = children
    data["components_relations"] = relations
    return data


def _members_of(components: list[dict]) -> set[str]:
    members: set[str] = set()
    for component in components:
        members |= (
            set(component["file_methods"]) if "file_methods" in component else _members_of(component["components"])
        )
    return members


def _write_nested_relations(
    components: list[dict], held: dict[tuple[str, str, str], list[list]], ids: dict[str, str], repo_dir: Path
) -> None:
    for component in components:
        component["components_relations"] = [
            _relation_json(
                relation, held.get((component["component_id"], relation.src_id, relation.dst_id), []), ids, repo_dir
            )
            for relation in component["components_relations"]
        ]
        _write_nested_relations(component["components"], held, ids, repo_dir)


def _relation_json(relation: Relation, held: list[list], ids: dict[str, str], repo_dir: Path) -> dict:
    rows = [_edge_row(edge, ids, repo_dir) for edge in relation.all_edges]
    derived = sorted(map(json.dumps, rows)) == sorted(map(json.dumps, held))
    call_sites_of = {(row[0], row[1]): row[2] for row in (held if derived else rows)}
    data = {
        "relation": relation.relation,
        "src_name": relation.src_name,
        "dst_name": relation.dst_name,
        "evidence": relation.evidence,
        "src_id": relation.src_id,
        "dst_id": relation.dst_id,
        "is_static": relation.is_static,
        "key_edges": [_key_edge_row(edge, call_sites_of, ids, repo_dir) for edge in relation.key_edges],
    }
    if not derived:
        logger.debug(
            "Writing all_edges of %s -> %s: they are not the edges between its components",
            relation.src_id,
            relation.dst_id,
        )
        data["all_edges"] = rows
    return data


def _leaf_lineage(components: list[dict]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Map each leaf member reference to its leaf, and each leaf to its ancestry ending in itself."""
    leaf_of: dict[str, str] = {}
    lineage: dict[str, list[str]] = {}

    def visit(level: list[dict], ancestors: list[str]) -> None:
        for component in level:
            component_id = component["component_id"]
            if component.get("components"):
                visit(component["components"], [*ancestors, component_id])
                continue
            lineage[component_id] = [*ancestors, component_id]
            for ref in component.get("file_methods", []):
                leaf_of[ref] = component_id

    visit(components, [])
    return leaf_of, lineage


def _edges_by_relation(
    edges: list[list], leaf_of: dict[str, str], lineage: dict[str, list[str]]
) -> dict[tuple[str, str, str], list[list]]:
    """File each edge row under the relations that hold it, keyed by (scope, src_id, dst_id).

    The top-level relations (scope '') join leaves, so an edge sits under its endpoints'
    leaves. A nested scope's relations join its children, so an edge also sits in the one
    scope where its endpoints' leaves first part: their lowest common ancestor, between the
    children of it that hold each leaf.
    """
    held: dict[tuple[str, str, str], list[list]] = {}
    for row in edges:
        source_leaf, target_leaf = leaf_of.get(row[0]), leaf_of.get(row[1])
        if source_leaf is None or target_leaf is None:
            continue
        held.setdefault(("", source_leaf, target_leaf), []).append(row)
        source_line, target_line = lineage[source_leaf], lineage[target_leaf]
        depth = 0
        while depth < min(len(source_line), len(target_line)) and source_line[depth] == target_line[depth]:
            depth += 1
        if 0 < depth < min(len(source_line), len(target_line)):
            held.setdefault((source_line[depth - 1], source_line[depth], target_line[depth]), []).append(row)
    return held


class _DocumentReader:
    """Rebuilds the in-memory analysis from a parsed analysis.json document."""

    def __init__(self, data: dict) -> None:
        self.files: dict[str, FileEntry] = {}
        self.methods: dict[str, tuple[str, MethodEntry]] = {}
        self.sub_analyses: dict[str, AnalysisInsights] = {}
        self.missing: list[str] = []
        for file_path, entry in data["files"].items():
            methods = []
            for qualified_name, (start_line, end_line, node_type, content_hash, symbol_id) in entry["methods"].items():
                method = MethodEntry(
                    qualified_name=qualified_name,
                    start_line=start_line,
                    end_line=end_line,
                    node_type=node_type,
                    content_hash=content_hash,
                )
                self.methods[symbol_id] = (file_path, method)
                methods.append(method)
            if file_path:
                self.files[file_path] = FileEntry(
                    methods=methods, content_hash=entry["content_hash"], module_hash=entry["module_hash"]
                )
        self.held = _edges_by_relation(data["edges"], *_leaf_lineage(data["components"]))

    def analysis(self, description: str, components: list[dict], relations: list[dict], scope: str) -> AnalysisInsights:
        return AnalysisInsights(
            description=description,
            components=[self.component(component) for component in components],
            components_relations=[self.relation(relation, scope) for relation in relations],
        )

    def component(self, data: dict) -> Component:
        component_id = data["component_id"]
        children = data.get("components", [])
        if children:
            self.sub_analyses[component_id] = self.analysis(
                data["description"], children, data.get("components_relations", []), component_id
            )
        if "file_methods" in data or not children:
            members = [self.member(ref) for ref in data.get("file_methods", [])]
            groups = _groups([member for member in members if member is not None])
        else:
            union = {
                (group.file_path, method.qualified_name): (group.file_path, method)
                for child in self.sub_analyses[component_id].components
                for group in child.file_methods
                for method in group.methods
            }
            groups = _groups(sorted(union.values(), key=lambda member: member[0]))
        return Component(
            name=data["name"],
            component_id=component_id,
            description=data["description"],
            key_entities=[SourceCodeReference(**entity) for entity in data.get("key_entities", [])],
            file_methods=groups,
            source_cluster_ids=data.get("source_cluster_ids", []),
        )

    def member(self, ref: str) -> tuple[str, MethodEntry] | None:
        member = self.methods.get(ref)
        if member is None:
            if "|" not in ref:
                raise ValueError(f"Unknown method id {ref!r}")
            # A raw key names no indexed method, so there is no member to hold.
            self.missing.append(ref)
        return member

    def relation(self, data: dict, scope: str) -> Relation:
        rows = data.get("all_edges")
        if rows is None:
            rows = self.held.get((scope, data["src_id"], data["dst_id"]), [])
        call_sites_of = {(row[0], row[1]): row[2] for row in rows}
        return Relation(
            relation=data["relation"],
            src_name=data["src_name"],
            dst_name=data["dst_name"],
            evidence=data.get("evidence", ""),
            key_edges=[
                self.edge(
                    [row[0], row[1], row[3] if len(row) > 3 else call_sites_of.get((row[0], row[1]), [])] + row[2:3]
                )
                for row in data.get("key_edges", [])
            ],
            src_id=data["src_id"],
            dst_id=data["dst_id"],
            is_static=data.get("is_static", False),
            all_edges=[self.edge(row) for row in rows],
        )

    def edge(self, row: list) -> RelationEdge:
        return RelationEdge(
            source=self.reference(row[0]),
            target=self.reference(row[1]),
            call_sites=[RelationCallSite(line=line, column=column) for line, column in row[2]],
            description=row[3] if len(row) > 3 else "",
        )

    def reference(self, ref: str) -> SourceCodeReference:
        indexed = self.methods.get(ref)
        if indexed is not None:
            file_path, method = indexed
            return SourceCodeReference(
                qualified_name=method.qualified_name,
                reference_file=file_path or None,
                reference_start_line=method.start_line,
                reference_end_line=method.end_line,
            )
        if "|" not in ref:
            raise ValueError(f"Unknown method id {ref!r}")
        file_path, _, qualified_name = ref.partition("|")
        return SourceCodeReference(qualified_name=qualified_name, reference_file=file_path or None)


def _groups(members: list[tuple[str, MethodEntry]]) -> list[FileMethodGroup]:
    """Group members by file in first-seen order, each group's methods in line order."""
    by_file: dict[str, list[MethodEntry]] = {}
    for file_path, method in members:
        by_file.setdefault(file_path, []).append(method.model_copy())
    return [
        FileMethodGroup(
            file_path=file_path, methods=sorted(methods, key=lambda m: (m.start_line, m.end_line, m.qualified_name))
        )
        for file_path, methods in by_file.items()
    ]


def _emit_record(value: object, indent: str, prefix: str, tail: str, lines: list[str]) -> None:
    flat = _flat_json(value, RECORD_WIDTH - len(indent) - len(prefix) - len(tail))
    container = _json_items(value)
    if flat is not None or container is None:
        lines.append(f"{indent}{prefix}{flat}{tail}")
        return
    opening, closing, item_iter = container
    items = list(item_iter)
    lines.append(f"{indent}{prefix}{opening}")
    for position, (item_prefix, item) in enumerate(items):
        _emit_record(item, indent + " ", item_prefix, "," if position < len(items) - 1 else "", lines)
    lines.append(f"{indent}{closing}{tail}")


def _flat_json(value: object, budget: int) -> str | None:
    """Compact JSON for *value*; None for a container longer than *budget* characters.

    Why the budget: the attempt stops early, so a large container costs a few records, not its whole size.
    """
    container = _json_items(value)
    if container is None:
        return json.dumps(value, ensure_ascii=False)
    opening, closing, items = container
    budget -= 2
    parts: list[str] = []
    for item_prefix, item in items:
        flat_item = _flat_json(item, budget - len(item_prefix))
        if flat_item is None:
            return None
        part = item_prefix + flat_item
        budget -= len(part) + (1 if parts else 0)
        if budget < 0:
            return None
        parts.append(part)
    return opening + ",".join(parts) + closing


def _json_items(value: object) -> tuple[str, str, Iterable[tuple[str, object]]] | None:
    """A JSON container's brackets and its (key prefix, item) pairs; None for a scalar."""
    if isinstance(value, dict):
        return "{", "}", ((json.dumps(field, ensure_ascii=False) + ":", item) for field, item in value.items())
    if isinstance(value, list):
        return "[", "]", (("", item) for item in value)
    return None
