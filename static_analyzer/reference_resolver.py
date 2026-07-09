import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.agent_responses import AnalysisInsights, RelationEdge, SourceCodeReference, call_site_coordinate
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import LANGUAGE_EXTENSIONS, Language
from static_analyzer.internal_references import looks_internal_reference

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeyEdgeResolution:
    """Validation-facing classification for one relation key edge."""

    description: str
    valid: bool = False
    same_endpoint: bool = False
    unresolved: bool = False


class StaticReferenceResolver:
    """Resolve LLM source references against static-analysis results."""

    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults):
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis

    def fix_source_code_reference_lines(self, analysis: AnalysisInsights) -> AnalysisInsights:
        logger.info(f"Fixing source code reference lines for the analysis: {analysis.llm_str()}")
        self.fix_key_entities_refs(analysis)
        self.fix_edge_refs(analysis)
        self.remove_unresolved_references(analysis)
        return self.relative_paths(analysis)

    def fix_key_entities_refs(self, analysis: AnalysisInsights) -> None:
        """Resolve component key entity references."""
        for component in analysis.components:
            for reference in component.key_entities:
                if (
                    reference.reference_file is not None
                    and os.path.exists(reference.reference_file)
                    and reference.reference_start_line is not None
                    and reference.reference_end_line is not None
                ):
                    continue

                file_candidates = component.file_paths() or None
                self.resolve_reference(reference, file_candidates)

    def fix_edge_refs(self, analysis: AnalysisInsights) -> None:
        """Resolve relation edge endpoint references."""
        component_by_name = {component.name: component for component in analysis.components}
        for relation in analysis.components_relations:
            src_component = component_by_name.get(relation.src_name)
            dst_component = component_by_name.get(relation.dst_name)
            src_candidates = [fm.file_path for fm in src_component.file_methods] if src_component else []
            dst_candidates = [fm.file_path for fm in dst_component.file_methods] if dst_component else []
            for edge in relation.key_edges:
                self.resolve_reference(edge.source, src_candidates)
                self.resolve_reference(edge.target, dst_candidates)
                self.attach_static_call_sites(edge)

    def resolve_reference(self, reference: SourceCodeReference, file_candidates: list[str] | None = None) -> None:
        """Resolve a source reference in-place."""
        qname = reference.qualified_name.replace(os.sep, ".")
        languages = self.static_analysis.get_languages()

        for lang in languages:
            if self._try_exact_match(reference, qname, lang):
                return

        for lang in languages:
            if self._try_loose_match(reference, qname, lang):
                return

        for lang in languages:
            if self._try_symbol_token_match(reference, qname, lang):
                return

        for lang in languages:
            if self._try_file_path_resolution(reference, qname, lang, file_candidates):
                return

        logger.warning(f"[Reference Resolution] Could not resolve reference {reference.qualified_name} in any language")

    def resolve_node(self, reference: SourceCodeReference):
        """Resolve a source reference to a static-analysis node without mutating it."""
        qname = reference.qualified_name.replace(os.sep, ".")
        for lang in self.static_analysis.get_languages():
            try:
                return self.static_analysis.get_reference(lang, qname)
            except (ValueError, FileExistsError):
                pass

        for lang in self.static_analysis.get_languages():
            _, node = self.static_analysis.get_loose_reference(lang, qname)
            if node is not None:
                return node

        return None

    def classify_key_edge(self, edge: RelationEdge, cfg_graphs: dict[str, Any]) -> KeyEdgeResolution:
        """Classify whether a relation key edge supports relation evidence."""
        source_node = self.resolve_node(edge.source)
        target_node = self.resolve_node(edge.target)
        description = edge.llm_str()

        if source_node is None or target_node is None:
            if self.has_external_unresolved_endpoint(edge, source_node, target_node):
                return KeyEdgeResolution(description=description, valid=True)
            return KeyEdgeResolution(description=description, unresolved=True)

        if self.node_identity(source_node) == self.node_identity(target_node):
            return KeyEdgeResolution(description=description, same_endpoint=True)

        if not self.has_cfg_edge(source_node.fully_qualified_name, target_node.fully_qualified_name, cfg_graphs):
            if not edge.description.strip():
                return KeyEdgeResolution(description=description)

        return KeyEdgeResolution(description=description, valid=True)

    def has_external_unresolved_endpoint(self, edge: RelationEdge, source_node, target_node) -> bool:
        """Return true when one endpoint is repo code and the other looks external."""
        if source_node is not None and target_node is None:
            return not looks_internal_reference(self.static_analysis, edge.target.qualified_name)
        if source_node is None and target_node is not None:
            return not looks_internal_reference(self.static_analysis, edge.source.qualified_name)
        return False

    def has_cfg_edge(self, source_qname: str, target_qname: str, cfg_graphs: dict[str, Any] | None = None) -> bool:
        """Return true when the static CFG has this exact source-to-target edge."""
        if cfg_graphs:
            for cfg in cfg_graphs.values():
                for edge in cfg.edges:
                    if edge.get_source() == source_qname and edge.get_destination() == target_qname:
                        return True
        for lang in self.static_analysis.get_languages():
            try:
                cfg = self.static_analysis.get_cfg(lang)
            except ValueError:
                continue
            for edge in cfg.edges:
                if edge.get_source() == source_qname and edge.get_destination() == target_qname:
                    return True
        return False

    @staticmethod
    def node_identity(node) -> str:
        """Return a stable identity for a static-analysis node."""
        return f"{node.fully_qualified_name}:{node.file_path}:{node.line_start}:{node.line_end}"

    def keep_relation_edge(self, edge: RelationEdge) -> bool:
        """Keep relation edges that resolve internally or target external code."""
        if self.same_resolved_relation_endpoint(edge):
            return False
        if self.resolved_relation_edge(edge):
            return True
        return self.external_relation_edge(edge)

    def resolved_relation_edge(self, edge: RelationEdge) -> bool:
        """Return true when both edge endpoints resolve to existing files."""
        return (
            edge.source.reference_file is not None
            and self.reference_file_exists(edge.source.reference_file)
            and edge.target.reference_file is not None
            and self.reference_file_exists(edge.target.reference_file)
        )

    def external_relation_edge(self, edge: RelationEdge) -> bool:
        """Return true when one endpoint is repo code and the other is external."""
        source_resolved = edge.source.reference_file is not None and self.reference_file_exists(
            edge.source.reference_file
        )
        target_resolved = edge.target.reference_file is not None and self.reference_file_exists(
            edge.target.reference_file
        )
        if source_resolved == target_resolved:
            return False
        unresolved = edge.target if source_resolved else edge.source
        return not looks_internal_reference(self.static_analysis, unresolved.qualified_name)

    def reference_file_exists(self, reference_file: str) -> bool:
        """Return true for absolute paths or paths relative to the analyzed repo."""
        path = Path(reference_file)
        if path.is_absolute():
            return path.exists()
        return (self.repo_dir / path).exists()

    def attach_static_call_sites(self, edge: RelationEdge) -> None:
        """Copy call-site metadata from the exact static CFG edge when present."""
        static_edge = self.find_static_edge(edge)
        if static_edge is None:
            return
        edge.call_sites = [
            {
                "line": call_site_coordinate(site.get("line", 0)),
                "column": call_site_coordinate(site.get("column", 0)),
            }
            for site in static_edge.call_sites
        ]

    def find_static_edge(self, relation_edge: RelationEdge):
        source_qname = relation_edge.source.qualified_name
        target_qname = relation_edge.target.qualified_name
        if not source_qname or not target_qname:
            return None
        for lang in self.static_analysis.get_languages():
            try:
                cfg = self.static_analysis.get_cfg(lang)
            except ValueError:
                continue
            for edge in cfg.edges:
                if edge.get_source() == source_qname and edge.get_destination() == target_qname:
                    return edge
        return None

    @staticmethod
    def same_resolved_relation_endpoint(edge: RelationEdge) -> bool:
        """Return true when a relation edge points to the same resolved symbol."""
        source = edge.source
        target = edge.target
        if source.qualified_name != target.qualified_name:
            return False
        if source.reference_file != target.reference_file:
            return False
        return (
            source.reference_start_line == target.reference_start_line
            and source.reference_end_line == target.reference_end_line
        )

    def remove_unresolved_references(self, analysis: AnalysisInsights) -> None:
        """Remove references that could not be resolved to existing files."""
        for component in analysis.components:
            original_ref_count = len(component.key_entities)
            component.key_entities = [
                ref
                for ref in component.key_entities
                if ref.reference_file is not None and self.reference_file_exists(ref.reference_file)
            ]
            removed_ref_count = original_ref_count - len(component.key_entities)
            if removed_ref_count > 0:
                logger.info(
                    f"[Reference Resolution] Removed {removed_ref_count} unresolved reference(s) "
                    f"from component '{component.name}'"
                )

        resolved_relations = []
        for relation in analysis.components_relations:
            original_edge_count = len(relation.key_edges)
            relation.key_edges = [edge for edge in relation.key_edges if self.keep_relation_edge(edge)]
            removed_edge_count = original_edge_count - len(relation.key_edges)
            if removed_edge_count > 0:
                logger.info(
                    f"[Reference Resolution] Removed {removed_edge_count} unresolved key edge(s) "
                    f"from relation '{relation.src_name}' -> '{relation.dst_name}'"
                )
            if not relation.is_static:
                relation.all_edges = relation.key_edges
                if not relation.key_edges:
                    if not relation.evidence.strip():
                        logger.info(
                            f"[Reference Resolution] Removed unsupported relation '{relation.src_name}' -> "
                            f"'{relation.dst_name}' after all key edges failed to resolve"
                        )
                        continue
            else:
                relation.all_edges = [edge for edge in relation.all_edges if self.keep_relation_edge(edge)]
            resolved_relations.append(relation)
        analysis.components_relations = resolved_relations

    def relative_paths(self, analysis: AnalysisInsights) -> AnalysisInsights:
        """Convert all reference file paths to relative paths."""
        for component in analysis.components:
            for reference in component.key_entities:
                if reference.reference_file and reference.reference_file.startswith(str(self.repo_dir)):
                    reference.reference_file = os.path.relpath(reference.reference_file, self.repo_dir)
        for relation in analysis.components_relations:
            for edge in relation.key_edges:
                if edge.source.reference_file and edge.source.reference_file.startswith(str(self.repo_dir)):
                    edge.source.reference_file = os.path.relpath(edge.source.reference_file, self.repo_dir)
                if edge.target.reference_file and edge.target.reference_file.startswith(str(self.repo_dir)):
                    edge.target.reference_file = os.path.relpath(edge.target.reference_file, self.repo_dir)
            for edge in relation.all_edges:
                if edge.source.reference_file and edge.source.reference_file.startswith(str(self.repo_dir)):
                    edge.source.reference_file = os.path.relpath(edge.source.reference_file, self.repo_dir)
                if edge.target.reference_file and edge.target.reference_file.startswith(str(self.repo_dir)):
                    edge.target.reference_file = os.path.relpath(edge.target.reference_file, self.repo_dir)
        return analysis

    def _try_exact_match(self, reference: SourceCodeReference, qname: str, lang: Language) -> bool:
        try:
            node = self.static_analysis.get_reference(lang, qname)
            reference.reference_file = node.file_path
            reference.reference_start_line = node.line_start
            reference.reference_end_line = node.line_end
            reference.qualified_name = qname
            logger.info(
                f"[Reference Resolution] Matched {reference.qualified_name} in {lang} at {reference.reference_file}"
            )
            return True
        except (ValueError, FileExistsError) as e:
            logger.warning(f"[Reference Resolution] Exact match failed for {reference.qualified_name} in {lang}: {e}")
            return False

    def _try_loose_match(self, reference: SourceCodeReference, qname: str, lang: Language) -> bool:
        try:
            _, node = self.static_analysis.get_loose_reference(lang, qname)
            if node is not None:
                self._apply_resolved_node(reference, node)
                logger.info(
                    f"[Reference Resolution] Loosely matched {reference.qualified_name} in {lang} at {reference.reference_file}"
                )
                return True
        except Exception as e:
            logger.warning(f"[Reference Resolution] Loose match failed for {qname} in {lang}: {e}")
        return False

    def _try_symbol_token_match(self, reference: SourceCodeReference, qname: str, lang: Language) -> bool:
        query_tokens = self._symbol_tokens(qname)
        if not query_tokens:
            return False

        candidates = [
            node
            for node in self.static_analysis.iter_reference_nodes(lang)
            if self._candidate_tokens_match(query_tokens, self._symbol_tokens(node.fully_qualified_name))
        ]
        if not candidates:
            return False

        selected = self._unique_backwards_token_match(query_tokens, candidates)
        if selected is None:
            return False

        self._apply_resolved_node(reference, selected)
        logger.info(
            f"[Reference Resolution] Token matched {qname} -> {reference.qualified_name} in {lang} at {reference.reference_file}"
        )
        return True

    @staticmethod
    def _apply_resolved_node(reference: SourceCodeReference, node: Any) -> None:
        reference.reference_file = node.file_path
        reference.reference_start_line = node.line_start
        reference.reference_end_line = node.line_end
        reference.qualified_name = node.fully_qualified_name

    def _unique_backwards_token_match(self, query_tokens: list[str], candidates: list[Any]) -> Any | None:
        matching = candidates
        for suffix_len in range(1, len(query_tokens) + 1):
            query_suffix = query_tokens[-suffix_len:]
            narrowed = [
                node
                for node in matching
                if len(self._symbol_tokens(node.fully_qualified_name)) >= suffix_len
                and self._symbol_tokens(node.fully_qualified_name)[-suffix_len:] == query_suffix
            ]
            if len(narrowed) == 1:
                return narrowed[0]
            if narrowed:
                matching = narrowed
        return None

    @staticmethod
    def _symbol_tokens(qualified_name: str) -> list[str]:
        return [token.lower() for token in re.split(r"[.:/\\]+", qualified_name) if token]

    @staticmethod
    def _candidate_tokens_match(query_tokens: list[str], candidate_tokens: list[str]) -> bool:
        if candidate_tokens[-1:] != query_tokens[-1:]:
            return False
        required_context = [token for token in query_tokens[:-1] if token.startswith("_")]
        return all(token in candidate_tokens for token in required_context)

    def _try_file_path_resolution(
        self, reference: SourceCodeReference, qname: str, lang: Language, file_candidates: list[str] | None = None
    ) -> bool:
        if self._try_existing_reference_file(reference, lang):
            return True
        return self._try_qualified_name_as_path(reference, qname, lang, file_candidates)

    def _try_existing_reference_file(self, reference: SourceCodeReference, lang: Language) -> bool:
        if (reference.reference_file is not None) and (not Path(reference.reference_file).is_absolute()):
            joined_path = os.path.join(self.repo_dir, reference.reference_file)
            if os.path.exists(joined_path):
                reference.reference_file = joined_path
                logger.info(
                    f"[Reference Resolution] File path matched for {reference.qualified_name} in {lang} at {reference.reference_file}"
                )
                return True
            reference.reference_file = None
        return False

    def _try_qualified_name_as_path(
        self, reference: SourceCodeReference, qname: str, lang: Language, file_candidates: list[str] | None = None
    ) -> bool:
        file_path = qname.replace(".", os.sep)
        full_path = os.path.join(self.repo_dir, file_path)
        file_ref = ".".join(full_path.rsplit(os.sep, 1))
        language_extensions = LANGUAGE_EXTENSIONS[Language(lang)]
        paths = [full_path, *(f"{file_path}{extension}" for extension in language_extensions), file_ref]

        for path in paths:
            if os.path.exists(path):
                reference.reference_file = str(path)
                logger.info(
                    f"[Reference Resolution] Path matched for {reference.qualified_name} in {lang} at {reference.reference_file}"
                )
                return True

        if file_candidates:
            qname_segments = qname.split(".")
            for candidate in file_candidates:
                candidate_full = os.path.join(self.repo_dir, candidate) if not os.path.isabs(candidate) else candidate
                if not os.path.exists(candidate_full):
                    continue
                candidate_stem = os.path.splitext(candidate)[0].replace("/", os.sep).replace("\\", os.sep)
                for end in range(len(qname_segments), 0, -1):
                    prefix_as_path = os.sep.join(qname_segments[:end])
                    if candidate_stem.endswith(prefix_as_path):
                        reference.reference_file = str(candidate_full)
                        logger.info(
                            f"[Reference Resolution] File candidate matched for {reference.qualified_name} in {lang} at {reference.reference_file}"
                        )
                        return True
        return False
