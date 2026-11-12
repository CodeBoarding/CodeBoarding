import base64
import hashlib
import logging
import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, model_validator

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
from diagram_analysis.exceptions import (
    AnalysisFormatError,
    AnalysisInvariantError,
    SymbolIdCollisionError,
    UnreadableAnalysisError,
)
from infra.analysis_format import ANALYSIS_FORMAT_VERSION, RECORD_WIDTH, SYMBOL_ID_BYTES
from infra.clustering_ids import ComponentId
from infra.repo_utils.path_utils import normalize_repo_path

logger = logging.getLogger(__name__)


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
        description="SHA-256 over the sorted per-file content hashes; the source-state version key."
    )
    repo_name: str = Field(description="Name of the analyzed repository.")
    depth_level: int = Field(description="Maximum depth level actually reached by this analysis.")
    depth_cap: int = Field(
        description="Safety-valve depth ceiling the run was configured with (see --depth-cap); "
        "may exceed depth_level when separability stopped expansion before the cap. "
        "The value future incremental/partial runs reuse as their own cap."
    )
    file_coverage_summary: FileCoverageSummary = Field(description="Lightweight file coverage counts.")
    tree_spec: dict = Field(
        description="The tree specification the components were drawn from: per scope, the prefixes "
        "and words each component owns. Drafted on a full analysis; incremental and partial runs "
        "replay it, so the partition cannot move underneath unchanged code. Empty on an analysis "
        "written before it existed.",
    )
    format_version: str = Field(description="Layout of the analysis.json document.")


# The analysis.json document. The writer builds it and the reader validates into it, so a file that
# does not fit is unreadable rather than half-read. Each fact is stated once; see build_unified_analysis_json.
CallSites = tuple[tuple[int, int], ...]
# [start_line, end_line, type, content_hash, id]
MethodRow = tuple[int, int, str, str, str]
# [source id, target id, call sites] plus the description when there is one
EdgeRow = tuple[str, str, CallSites] | tuple[str, str, CallSites, str]
# [source id, target id] plus the description when there is one
KeyEdgeRow = tuple[str, str] | tuple[str, str, str]


class FileDocument(BaseModel):
    content_hash: str
    module_hash: str
    methods: dict[str, MethodRow]


class RelationDocument(BaseModel):
    relation: str
    src_name: str
    dst_name: str
    evidence: str
    src_id: str
    dst_id: str
    is_static: bool
    key_edges: list[KeyEdgeRow]


class ComponentDocument(BaseModel):
    name: str
    component_id: str
    description: str
    key_entities: list[SourceCodeReference]
    source_cluster_ids: list[ComponentId]
    can_expand: bool
    # A leaf's members, by id; a parent's members are its children's, so it has none.
    file_methods: list[str] | None = None
    components: list["ComponentDocument"]
    components_relations: list[RelationDocument]


class AnalysisDocument(BaseModel):
    metadata: AnalysisMetadata
    description: str
    files: dict[str, FileDocument]
    components: list[ComponentDocument]
    components_relations: list[RelationDocument]
    edges: list[EdgeRow]

    @model_validator(mode="after")
    def _cites_only_what_it_holds(self) -> "AnalysisDocument":
        """Every cited id is a listed method and every key edge an edge, so reading never misses a lookup."""
        ids = {row[4] for entry in self.files.values() for row in entry.methods.values()}
        pairs = {(row[0], row[1]) for row in self.edges}
        components = list(_walk(self.components))
        relations = [*self.components_relations, *(r for c in components for r in c.components_relations)]
        cited = [
            *(ref for row in self.edges for ref in row[:2]),
            *(ref for c in components for ref in c.file_methods or []),
        ]
        unknown = next((ref for ref in cited if ref not in ids), None)
        if unknown is not None:
            raise ValueError(f"cites method id {unknown!r}, which no file lists")
        stray = next((key for relation in relations for key in relation.key_edges if key[:2] not in pairs), None)
        if stray is not None:
            raise ValueError(f"has a key edge {stray[0]} -> {stray[1]} that is not in edges")
        return self


def _walk(components: list[ComponentDocument]) -> Iterable[ComponentDocument]:
    for component in components:
        yield component
        yield from _walk(component.components)


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


def _source_reference_method_key(reference: SourceCodeReference, repo_dir: Path) -> tuple[str, str]:
    return normalize_repo_path(reference.reference_file or "", repo_dir), reference.qualified_name


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

    - ``files[path].methods[qualified_name] = [start_line, end_line, type, content_hash, id]``;
      everything else cites a method by that id.
    - ``edges`` lists every method edge once: ``[source, target, [[line, column], ...]]``, plus
      its description when it has one.
    - Only leaves list ``file_methods``; a parent's members are its children's.
    - Relations hold no edges: a reader files each edge under its relations with ``_edges_by_relation``.
    - A key edge is ``[source, target, description?]``, one of its relation's edges.

    Raises ``AnalysisInvariantError`` when the analysis breaks one of those rules, instead of
    writing what a reader would not rebuild.
    """
    files_index = _build_files_index_from_analysis(analysis, sub_analyses)
    ids = _symbol_ids(files_index)
    writer = _DocumentWriter(ids, repo_dir, sub_analyses or {})

    components = [writer.component(component, expandable_components) for component in analysis.components]
    relations = merge_relations_by_pair(analysis.components_relations, include_relation=True)
    edges = writer.edge_table(relations)
    leaf_of, lineage = _leaf_lineage(components)
    leaf_members = [ref for c in _walk(components) if not c.components for ref in c.file_methods or []]
    if len(leaf_members) != len(leaf_of):
        raise AnalysisInvariantError(f"{len(leaf_members) - len(leaf_of)} method(s) are members of more than one leaf")
    held = _edges_by_relation(edges, leaf_of, lineage)
    writer.fill_relations(held)

    metadata = AnalysisMetadata(
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_tree_hash=source_tree_hash,
        repo_name=repo_name,
        depth_level=_compute_depth_level(sub_analyses),
        depth_cap=depth_cap,
        file_coverage_summary=file_coverage_summary
        or FileCoverageSummary(total_files=0, analyzed=0, not_analyzed=0, not_analyzed_by_reason={}),
        tree_spec=tree_spec or {},
        format_version=ANALYSIS_FORMAT_VERSION,
    )
    document = AnalysisDocument(
        metadata=metadata,
        description=analysis.description,
        # A pathless file has no entry of its own; it is written only to index its methods.
        files={
            file_path: _file_document(file_path, entry, ids)
            for file_path, entry in files_index.items()
            if file_path or entry.methods
        },
        components=components,
        components_relations=[
            writer.relation(relation, held.get(("", relation.src_id, relation.dst_id), [])) for relation in relations
        ],
        edges=edges,
    )
    return dump_analysis_document(document.model_dump(mode="json", exclude_none=True))


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

    Raises ``UnreadableAnalysisError`` (``AnalysisFormatError`` for another format) when *data*
    is not an ``AnalysisDocument``.
    """
    require_format_version(read_metadata(data))
    try:
        document = AnalysisDocument.model_validate(data)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "the document"
        raise UnreadableAnalysisError(
            f"analysis.json is not a valid {ANALYSIS_FORMAT_VERSION!r} document ({error.error_count()} problem(s); "
            f"at {location}: {first['msg']}); run a full analysis to rewrite it"
        ) from error
    reader = _DocumentReader(document)
    root_analysis = reader.analysis(document.description, document.components, document.components_relations, "")

    root_analysis.files = {path: FileEntry().merge_from(entry) for path, entry in reader.files.items()}
    for sub in reader.sub_analyses.values():
        # Same shape a live scope gets from ``build_files_index``: only the files its components
        # own. The root carries the whole index, so a save loses nothing.
        owned = {group.file_path for component in sub.components for group in component.file_methods}
        sub.files = {path: FileEntry().merge_from(entry) for path, entry in reader.files.items() if path in owned}

    return root_analysis, reader.sub_analyses


def read_metadata(document: object) -> dict:
    """The metadata block of a decoded analysis.json in any format.

    Raises ``UnreadableAnalysisError`` when *document* is not an object with an object for metadata.
    """
    metadata = document.get("metadata") if isinstance(document, dict) else None
    if not isinstance(metadata, dict):
        raise UnreadableAnalysisError("analysis.json is not a JSON object with a metadata object")
    return metadata


def require_format_version(metadata: dict) -> None:
    """Raise ``AnalysisFormatError`` unless *metadata* is from a document in the format this Core reads."""
    version = metadata.get("format_version")
    if version != ANALYSIS_FORMAT_VERSION:
        raise AnalysisFormatError(version, ANALYSIS_FORMAT_VERSION)


def file_nested_relation_edges(
    root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights], repo_dir: Path
) -> None:
    """Give each nested relation the top-level edges ``_edges_by_relation`` files under it.

    Its key edges are those among them. Why: the document leaves nested relation edges out and
    readers rebuild them with that rule, so the generator gives nested relations exactly these.
    """
    leaf_of, lineage = _memory_lineage(root_analysis, sub_analyses)

    def keys(edge: RelationEdge) -> tuple[tuple[str, str], tuple[str, str]]:
        return _source_reference_method_key(edge.source, repo_dir), _source_reference_method_key(edge.target, repo_dir)

    table = {keys(edge): edge for relation in root_analysis.components_relations for edge in relation.all_edges}
    held = _edges_by_relation([(*pair, edge) for pair, edge in table.items()], leaf_of, lineage)
    for scope_id, scope in sub_analyses.items():
        relations = []
        for relation in scope.components_relations:
            by_pair = {keys(row[2]): row[2] for row in held.get((scope_id, relation.src_id, relation.dst_id), [])}
            key_edges = [
                by_pair[pair].model_copy(update={"description": key.description})
                for key in relation.key_edges
                if (pair := keys(key)) in by_pair
            ]
            relations.append(relation.model_copy(update={"all_edges": list(by_pair.values()), "key_edges": key_edges}))
        scope.components_relations = relations


def build_id_to_name_map(root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> dict[str, str]:
    """Build a mapping from component_id to component name across all analysis levels."""
    id_to_name: dict[str, str] = {c.component_id: c.name for c in root_analysis.components}
    for sub_analysis in sub_analyses.values():
        for comp in sub_analysis.components:
            id_to_name[comp.component_id] = comp.name
    return id_to_name


def _symbol_ids(files_index: dict[str, FileEntry]) -> dict[tuple[str, str], str]:
    """Map each indexed method's (path, qualified name) to its id; two methods sharing an id fail the write."""
    ids: dict[tuple[str, str], str] = {}
    owners: dict[str, tuple[str, str]] = {}
    for file_path, entry in files_index.items():
        for method in entry.methods:
            key = (file_path, method.qualified_name)
            symbol_id = _symbol_id(file_path, method.qualified_name)
            if owners.setdefault(symbol_id, key) != key:
                raise SymbolIdCollisionError(symbol_id, "|".join(owners[symbol_id]), "|".join(key))
            ids[key] = symbol_id
    return ids


def _symbol_id(file_path: str, qualified_name: str) -> str:
    encoded = json.dumps([file_path, qualified_name], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.blake2b(encoded, digest_size=SYMBOL_ID_BYTES).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def _file_document(file_path: str, entry: FileEntry, ids: dict[tuple[str, str], str]) -> FileDocument:
    return FileDocument(
        content_hash=entry.content_hash,
        module_hash=entry.module_hash,
        methods={
            method.qualified_name: (
                method.start_line,
                method.end_line,
                method.node_type,
                method.content_hash,
                ids[(file_path, method.qualified_name)],
            )
            for method in entry.methods
        },
    )


class _DocumentWriter:
    """Builds the document's components and relations, refusing anything a reader would not rebuild."""

    def __init__(
        self,
        ids: dict[tuple[str, str], str],
        repo_dir: Path,
        sub_analyses: dict[str, tuple[AnalysisInsights, list[Component]]],
    ) -> None:
        self.ids = ids
        self.repo_dir = repo_dir
        self.sub_analyses = sub_analyses
        self.written: set[str] = set()
        # Each written parent with the relations of its scope, filled once the edge table is known.
        self.scopes: list[tuple[ComponentDocument, list[Relation]]] = []

    def component(self, component: Component, expandable: list[Component]) -> ComponentDocument:
        """A component and its subtree; a component is a parent exactly when it holds a sub-analysis."""
        component_id = component.component_id
        sub = self.sub_analyses.get(component_id)
        children: list[ComponentDocument] = []
        relations: list[Relation] = []
        if component_id in self.written:
            raise AnalysisInvariantError(f"Component {component_id} appears twice in the tree")
        if sub is not None and sub[0].components:
            self.written.add(component_id)
            children = [self.component(child, sub[1]) for child in sub[0].components]
            relations = merge_relations_by_pair(sub[0].components_relations, include_relation=True)
        members = list(
            dict.fromkeys(
                self.cite((group.file_path, method.qualified_name), f"component {component_id}")
                for group in component.file_methods
                for method in group.methods
            )
        )
        if children and set(members) != _members_of(children):
            raise AnalysisInvariantError(
                f"Component {component_id} holds {len(set(members) ^ _members_of(children))} method(s) "
                "not held by exactly one of its children"
            )
        document = ComponentDocument(
            name=component.name,
            component_id=component_id,
            description=component.description,
            key_entities=_relativize_key_entities(component.key_entities, self.repo_dir),
            source_cluster_ids=component.source_cluster_ids,
            can_expand=bool(children) or any(c.component_id == component_id for c in expandable),
            file_methods=None if children else members,
            components=children,
            components_relations=[],
        )
        if children:
            self.scopes.append((document, relations))
        return document

    def edge_table(self, relations: list[Relation]) -> list[EdgeRow]:
        """Every edge of the top-level relations once, sorted by (source key, target key)."""
        rows: dict[tuple[tuple[str, str], tuple[str, str]], EdgeRow] = {}
        for relation in relations:
            for edge in relation.all_edges:
                row = self.edge_row(edge, f"relation {relation.src_id} -> {relation.dst_id}")
                pair = (
                    _source_reference_method_key(edge.source, self.repo_dir),
                    _source_reference_method_key(edge.target, self.repo_dir),
                )
                if rows.setdefault(pair, row) != row:
                    raise AnalysisInvariantError(f"The edge {pair[0]} -> {pair[1]} is held twice, differently")
        return [rows[pair] for pair in sorted(rows)]

    def fill_relations(self, held: dict[tuple[str, str, str], list[EdgeRow]]) -> None:
        for document, relations in self.scopes:
            document.components_relations = [
                self.relation(relation, held.get((document.component_id, relation.src_id, relation.dst_id), []))
                for relation in relations
            ]

    def relation(self, relation: Relation, held: list[EdgeRow]) -> RelationDocument:
        where = f"relation {relation.src_id} -> {relation.dst_id}"
        rows = {self.edge_row(edge, where) for edge in relation.all_edges}
        if rows != set(held):
            raise AnalysisInvariantError(
                f"The {where} holds {len(rows ^ set(held))} edge(s) other than those its scope files under it"
            )
        # Merging folded each key edge into the relation's edges, so the check above covers them too.
        key_edges: list[KeyEdgeRow] = [
            (*self.edge_row(edge, where)[:2], edge.description) if edge.description else self.edge_row(edge, where)[:2]
            for edge in relation.key_edges
        ]
        return RelationDocument(
            relation=relation.relation,
            src_name=relation.src_name,
            dst_name=relation.dst_name,
            evidence=relation.evidence,
            src_id=relation.src_id,
            dst_id=relation.dst_id,
            is_static=relation.is_static,
            key_edges=key_edges,
        )

    def edge_row(self, edge: RelationEdge, where: str) -> EdgeRow:
        source = self.cite(_source_reference_method_key(edge.source, self.repo_dir), where)
        target = self.cite(_source_reference_method_key(edge.target, self.repo_dir), where)
        call_sites = tuple((site.line, site.column) for site in edge.call_sites)
        return (source, target, call_sites, edge.description) if edge.description else (source, target, call_sites)

    def cite(self, key: tuple[str, str], where: str) -> str:
        symbol_id = self.ids.get(key)
        if symbol_id is None:
            raise AnalysisInvariantError(f"The {where} cites {key!r}, which no file indexes")
        return symbol_id


def _members_of(components: list[ComponentDocument]) -> set[str]:
    members: set[str] = set()
    for component in components:
        members |= (
            set(component.file_methods) if component.file_methods is not None else _members_of(component.components)
        )
    return members


def _leaf_lineage(components: list[ComponentDocument]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Map each leaf member id to its leaf, and each leaf to its ancestry ending in itself."""
    leaf_of: dict[str, str] = {}
    lineage: dict[str, list[str]] = {}

    def visit(level: list[ComponentDocument], ancestors: list[str]) -> None:
        for component in level:
            if component.components:
                visit(component.components, [*ancestors, component.component_id])
                continue
            lineage[component.component_id] = [*ancestors, component.component_id]
            for ref in component.file_methods or []:
                leaf_of[ref] = component.component_id

    visit(components, [])
    return leaf_of, lineage


def _memory_lineage(
    root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]
) -> tuple[dict[tuple[str, str], str], dict[str, list[str]]]:
    """``_leaf_lineage`` of an in-memory tree, keyed by (path, qualified name)."""
    leaf_of: dict[tuple[str, str], str] = {}
    lineage: dict[str, list[str]] = {}

    def visit(level: list[Component], ancestors: list[str]) -> None:
        for component in level:
            sub = sub_analyses.get(component.component_id)
            if sub is not None and sub.components:
                visit(sub.components, [*ancestors, component.component_id])
                continue
            lineage[component.component_id] = [*ancestors, component.component_id]
            for group in component.file_methods:
                for method in group.methods:
                    leaf_of[(group.file_path, method.qualified_name)] = component.component_id

    visit(root_analysis.components, [])
    return leaf_of, lineage


def _edges_by_relation[Row: Sequence, Ref](
    edges: Iterable[Row], leaf_of: dict[Ref, str], lineage: dict[str, list[str]]
) -> dict[tuple[str, str, str], list[Row]]:
    """File each edge row under the relations that hold it, keyed by (scope, src_id, dst_id).

    A row starts with its source and target. The top-level relations (scope '') join leaves, so
    an edge sits under its endpoints' leaves. A nested scope's relations join its children, so
    an edge also sits in the one scope where its endpoints' leaves first part: their lowest
    common ancestor, between the children of it that hold each leaf. An endpoint no leaf owns
    files the edge nowhere.
    """
    held: dict[tuple[str, str, str], list[Row]] = {}
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
    """Rebuilds the in-memory analysis from a validated ``AnalysisDocument``."""

    def __init__(self, document: AnalysisDocument) -> None:
        self.files: dict[str, FileEntry] = {}
        self.methods: dict[str, tuple[str, MethodEntry]] = {}
        self.sub_analyses: dict[str, AnalysisInsights] = {}
        for file_path, entry in document.files.items():
            methods = []
            for qualified_name, (start_line, end_line, node_type, content_hash, symbol_id) in entry.methods.items():
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
                    methods=methods, content_hash=entry.content_hash, module_hash=entry.module_hash
                )
        self.held = _edges_by_relation(document.edges, *_leaf_lineage(document.components))
        self.call_sites = {(row[0], row[1]): row[2] for row in document.edges}

    def analysis(
        self, description: str, components: list[ComponentDocument], relations: list[RelationDocument], scope: str
    ) -> AnalysisInsights:
        return AnalysisInsights(
            description=description,
            components=[self.component(component) for component in components],
            components_relations=[self.relation(relation, scope) for relation in relations],
        )

    def component(self, document: ComponentDocument) -> Component:
        component_id = document.component_id
        if not document.components:
            groups = _groups([self.methods[ref] for ref in document.file_methods or []])
        else:
            self.sub_analyses[component_id] = self.analysis(
                document.description, document.components, document.components_relations, component_id
            )
            union = {
                (group.file_path, method.qualified_name): (group.file_path, method)
                for child in self.sub_analyses[component_id].components
                for group in child.file_methods
                for method in group.methods
            }
            groups = _groups(sorted(union.values(), key=lambda member: member[0]))
        return Component(
            name=document.name,
            component_id=component_id,
            description=document.description,
            key_entities=document.key_entities,
            file_methods=groups,
            source_cluster_ids=document.source_cluster_ids,
        )

    def relation(self, document: RelationDocument, scope: str) -> Relation:
        rows = self.held.get((scope, document.src_id, document.dst_id), [])
        key_edges = [
            self.edge((key[0], key[1], self.call_sites[key[0], key[1]], *key[2:]))  # type: ignore[arg-type]
            for key in document.key_edges
        ]
        return Relation(
            relation=document.relation,
            src_name=document.src_name,
            dst_name=document.dst_name,
            evidence=document.evidence,
            key_edges=key_edges,
            src_id=document.src_id,
            dst_id=document.dst_id,
            is_static=document.is_static,
            all_edges=[self.edge(row) for row in rows],
        )

    def edge(self, row: EdgeRow) -> RelationEdge:
        return RelationEdge(
            source=self.reference(row[0]),
            target=self.reference(row[1]),
            call_sites=[RelationCallSite(line=line, column=column) for line, column in row[2]],
            description=row[3] if len(row) > 3 else "",
        )

    def reference(self, ref: str) -> SourceCodeReference:
        file_path, method = self.methods[ref]
        return SourceCodeReference(
            qualified_name=method.qualified_name,
            reference_file=file_path or None,
            reference_start_line=method.start_line,
            reference_end_line=method.end_line,
        )


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
