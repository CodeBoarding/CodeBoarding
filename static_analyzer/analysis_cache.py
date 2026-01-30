"""
Analysis cache management for iterative static analysis.

This module provides functionality to save and load static analysis results
to/from disk, enabling incremental analysis by reusing cached data for
unchanged files.
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from static_analyzer.graph import CallGraph, Node

logger = logging.getLogger(__name__)


@dataclass
class AnalysisCacheMetadata:
    """Metadata for cached analysis results."""

    commit_hash: str
    iteration_id: int
    timestamp: float
    version: str = "1.0"


class AnalysisCacheManager:
    """
    Manages persistence and loading of static analysis results.

    Provides methods to save analysis results to disk in a structured format
    and load them back, enabling incremental analysis workflows.
    """

    def __init__(self):
        """Initialize the cache manager."""
        pass

    def save_cache(self, cache_path: Path, analysis_result: dict, commit_hash: str, iteration_id: int) -> None:
        """
        Save static analysis results to cache file.

        Args:
            cache_path: Path where to save the cache file
            analysis_result: Dictionary containing analysis results with keys:
                - 'call_graph': CallGraph object
                - 'class_hierarchies': dict of class hierarchy information
                - 'package_relations': dict of package dependency information
                - 'references': list of Node objects
                - 'source_files': list of analyzed file paths
            commit_hash: Git commit hash for the cached analysis
            iteration_id: Unique iteration identifier

        Raises:
            ValueError: If analysis_result is missing required keys
            OSError: If cache file cannot be written
        """
        # Validate input
        required_keys = {"call_graph", "class_hierarchies", "package_relations", "references", "source_files"}
        if not all(key in analysis_result for key in required_keys):
            missing_keys = required_keys - set(analysis_result.keys())
            raise ValueError(f"Analysis result missing required keys: {missing_keys}")

        temp_path: Path | None = None
        try:
            # Create cache directory if it doesn't exist
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            # Prepare cache data structure
            cache_data = {
                "metadata": {
                    "commit_hash": commit_hash,
                    "iteration_id": iteration_id,
                    "timestamp": time.time(),
                    "version": "1.0",
                },
                "call_graph": self._serialize_call_graph(analysis_result["call_graph"]),
                "class_hierarchies": analysis_result["class_hierarchies"],
                "package_relations": analysis_result["package_relations"],
                "references": self._serialize_references(analysis_result["references"]),
                "source_files": [str(path) for path in analysis_result["source_files"]],
            }

            # Write to cache file atomically
            temp_path = cache_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)

            # Atomic rename
            temp_path.replace(cache_path)

            logger.info(f"Saved analysis cache to {cache_path} (commit: {commit_hash}, iteration: {iteration_id})")

        except Exception as e:
            logger.error(f"Failed to save analysis cache: {e}")
            # Clean up temp file if it exists
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
            raise

    def load_cache(self, cache_path: Path) -> tuple[dict, str, int] | None:
        """
        Load static analysis results from cache file.

        Args:
            cache_path: Path to the cache file

        Returns:
            Tuple of (analysis_result, commit_hash, iteration_id) if successful,
            None if cache doesn't exist or is invalid

        The analysis_result dict contains:
            - 'call_graph': CallGraph object
            - 'class_hierarchies': dict of class hierarchy information
            - 'package_relations': dict of package dependency information
            - 'references': list of Node objects
            - 'source_files': list of analyzed file paths
        """
        if not cache_path.exists():
            logger.info(f"Cache file does not exist: {cache_path}")
            return None

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)

            # Validate cache structure
            if not self._validate_cache_structure(cache_data):
                logger.warning(f"Invalid cache structure in {cache_path}")
                return None

            # Extract metadata
            metadata = cache_data["metadata"]
            commit_hash = metadata["commit_hash"]
            iteration_id = metadata["iteration_id"]

            # Deserialize analysis data
            analysis_result = {
                "call_graph": self._deserialize_call_graph(cache_data["call_graph"]),
                "class_hierarchies": cache_data["class_hierarchies"],
                "package_relations": cache_data["package_relations"],
                "references": self._deserialize_references(cache_data["references"]),
                "source_files": [Path(path) for path in cache_data["source_files"]],
            }

            logger.info(f"Loaded analysis cache from {cache_path} (commit: {commit_hash}, iteration: {iteration_id})")
            return analysis_result, commit_hash, iteration_id

        except Exception as e:
            logger.error(f"Failed to load analysis cache from {cache_path}: {e}")
            return None

    def invalidate_files(self, analysis_result: dict[str, Any], changed_files: set[Path]) -> dict[str, Any]:
        """
        Remove analysis data for changed files from the cached results.

        This method performs comprehensive invalidation by:
        1. Removing all nodes from changed files
        2. Removing all edges that reference nodes from changed files
        3. Removing class hierarchies from changed files
        4. Updating package relations to exclude changed files
        5. Removing references from changed files
        6. Validating no dangling references remain

        Args:
            analysis_result: Dictionary containing cached analysis results
            changed_files: Set of file paths that have changed

        Returns:
            Updated analysis_result with data for changed files removed

        Raises:
            ValueError: If dangling references are detected after invalidation
        """
        changed_file_strs = {str(path) for path in changed_files}

        logger.info(f"Starting file invalidation for {len(changed_files)} changed files")
        logger.debug(f"Changed file strings: {changed_file_strs}")

        # Debug: Check if any reference file paths match
        sample_refs = analysis_result.get("references", [])[:3]
        if sample_refs:
            logger.debug(f"Sample reference file_paths: {[r.file_path for r in sample_refs]}")
            for ref in sample_refs:
                logger.debug(f"  ref '{ref.file_path}' in changed_file_strs: {ref.file_path in changed_file_strs}")

        # Create a copy to avoid modifying the original
        updated_result = {
            "call_graph": CallGraph(),
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
        }

        # Step 1: Remove nodes from changed files
        call_graph = analysis_result["call_graph"]
        removed_nodes = set()
        kept_nodes = set()

        for node_name, node in call_graph.nodes.items():
            if node.file_path in changed_file_strs:
                removed_nodes.add(node_name)
                logger.debug(f"Removing node {node_name} from file {node.file_path}")
            else:
                updated_result["call_graph"].add_node(node)
                kept_nodes.add(node_name)

        # Step 2: Remove edges that reference removed nodes
        removed_edges = 0
        kept_edges = 0

        for edge in call_graph.edges:
            src_name = edge.get_source()
            dst_name = edge.get_destination()

            # Only keep edges where both nodes are kept
            if src_name in kept_nodes and dst_name in kept_nodes:
                try:
                    updated_result["call_graph"].add_edge(src_name, dst_name)
                    kept_edges += 1
                except ValueError as e:
                    logger.warning(f"Failed to add edge {src_name} -> {dst_name}: {e}")
                    removed_edges += 1
            else:
                removed_edges += 1
                logger.debug(f"Removing edge {src_name} -> {dst_name} (references removed node)")

        # Step 3: Remove class hierarchies from changed files
        removed_classes = 0
        for class_name, class_info in analysis_result["class_hierarchies"].items():
            class_file_path = class_info.get("file_path", "")
            if class_file_path not in changed_file_strs:
                updated_result["class_hierarchies"][class_name] = class_info.copy()
            else:
                removed_classes += 1
                logger.debug(f"Removing class hierarchy {class_name} from file {class_file_path}")

        # Step 4: Update package relations to exclude changed files
        removed_packages = 0
        updated_packages = 0

        for package_name, package_info in analysis_result["package_relations"].items():
            original_files = package_info.get("files", [])
            remaining_files = [f for f in original_files if f not in changed_file_strs]

            if remaining_files:
                # Package still has files, update it
                updated_package_info = package_info.copy()
                updated_package_info["files"] = remaining_files
                updated_result["package_relations"][package_name] = updated_package_info

                if len(remaining_files) < len(original_files):
                    updated_packages += 1
                    logger.debug(
                        f"Updated package {package_name}: {len(original_files)} -> {len(remaining_files)} files"
                    )
            else:
                # Package has no remaining files, remove it entirely
                removed_packages += 1
                logger.debug(f"Removing package {package_name} (no remaining files)")

        # Step 5: Remove references from changed files
        removed_references = 0
        for ref in analysis_result["references"]:
            if ref.file_path not in changed_file_strs:
                updated_result["references"].append(ref)
            else:
                removed_references += 1
                logger.debug(f"Removing reference {ref.fully_qualified_name} from file {ref.file_path}")

        # Step 6: Filter source files
        original_source_count = len(analysis_result["source_files"])
        for file_path in analysis_result["source_files"]:
            if str(file_path) not in changed_file_strs:
                updated_result["source_files"].append(file_path)

        # Step 7: Validate no dangling references remain
        self._validate_no_dangling_references(updated_result)

        # Log summary
        logger.info(f"File invalidation complete:")
        logger.info(f"  - Removed {len(removed_nodes)} nodes, kept {len(kept_nodes)} nodes")
        logger.info(f"  - Removed {removed_edges} edges, kept {kept_edges} edges")
        logger.info(
            f"  - Removed {removed_classes} class hierarchies, kept {len(updated_result['class_hierarchies'])} class hierarchies"
        )
        logger.info(
            f"  - Removed {removed_packages} packages, updated {updated_packages} packages, kept {len(updated_result['package_relations'])} packages"
        )
        logger.info(f"  - Removed {removed_references} references, kept {len(updated_result['references'])} references")
        logger.info(f"  - Source files: {original_source_count} -> {len(updated_result['source_files'])}")

        return updated_result

    def _validate_no_dangling_references(self, analysis_result: dict[str, Any]) -> None:
        """
        Validate that no dangling references remain after file invalidation.

        Checks that:
        1. All edges reference existing nodes
        2. All references in the references list correspond to existing nodes
        3. All class hierarchies reference valid files
        4. All package relations reference valid files

        Args:
            analysis_result: Analysis result to validate

        Raises:
            ValueError: If dangling references are found
        """
        call_graph = analysis_result["call_graph"]
        existing_nodes = set(call_graph.nodes.keys())
        errors = []

        # Check edges reference existing nodes
        for edge in call_graph.edges:
            src_name = edge.get_source()
            dst_name = edge.get_destination()

            if src_name not in existing_nodes:
                errors.append(f"Edge source '{src_name}' references non-existent node")
            if dst_name not in existing_nodes:
                errors.append(f"Edge destination '{dst_name}' references non-existent node")

        # Check references correspond to existing nodes or are standalone
        for ref in analysis_result["references"]:
            # References can be standalone (not necessarily in call graph nodes)
            # but they should have valid file paths that exist in source_files
            source_file_strs = {str(path) for path in analysis_result["source_files"]}
            if ref.file_path not in source_file_strs:
                errors.append(
                    f"Reference '{ref.fully_qualified_name}' from file '{ref.file_path}' references non-existent source file"
                )

        # Check class hierarchies reference valid files
        source_file_strs = {str(path) for path in analysis_result["source_files"]}
        for class_name, class_info in analysis_result["class_hierarchies"].items():
            class_file_path = class_info.get("file_path", "")
            if class_file_path and class_file_path not in source_file_strs:
                errors.append(f"Class hierarchy '{class_name}' references non-existent file '{class_file_path}'")

        # Check package relations reference valid files
        for package_name, package_info in analysis_result["package_relations"].items():
            package_files = package_info.get("files", [])
            for package_file in package_files:
                if package_file not in source_file_strs:
                    errors.append(f"Package '{package_name}' references non-existent file '{package_file}'")

        if errors:
            error_msg = f"Dangling references detected after file invalidation:\n" + "\n".join(
                f"  - {error}" for error in errors
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.debug("Validation passed: no dangling references found")

    def merge_results(self, cached_result: dict[str, Any], new_result: dict[str, Any]) -> dict[str, Any]:
        """
        Merge new analysis results with cached results.

        Args:
            cached_result: Existing cached analysis results
            new_result: New analysis results for changed files

        Returns:
            Merged analysis results combining both datasets
        """
        merged_result = {
            "call_graph": CallGraph(),
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
        }

        # Merge call graphs
        # First add all nodes from both graphs
        for node_name, node in cached_result["call_graph"].nodes.items():
            merged_result["call_graph"].add_node(node)

        for node_name, node in new_result["call_graph"].nodes.items():
            merged_result["call_graph"].add_node(node)

        # Then add all edges from both graphs
        for edge in cached_result["call_graph"].edges:
            try:
                merged_result["call_graph"].add_edge(edge.get_source(), edge.get_destination())
            except ValueError:
                # Edge references nodes that don't exist - skip it
                pass

        for edge in new_result["call_graph"].edges:
            try:
                merged_result["call_graph"].add_edge(edge.get_source(), edge.get_destination())
            except ValueError:
                # Edge references nodes that don't exist - skip it
                pass

        # Merge class hierarchies (new results override cached)
        merged_result["class_hierarchies"].update(cached_result["class_hierarchies"])
        merged_result["class_hierarchies"].update(new_result["class_hierarchies"])

        # Merge package relations (new results override cached)
        merged_result["package_relations"].update(cached_result["package_relations"])
        merged_result["package_relations"].update(new_result["package_relations"])

        # Merge references
        # Get file paths from new result to identify which cached references to replace
        new_file_paths = {str(path) for path in new_result.get("source_files", [])}

        # Only keep cached references that are NOT from files in the new result
        for ref in cached_result["references"]:
            if ref.file_path not in new_file_paths:
                merged_result["references"].append(ref)

        # Add all references from new result (these replace the old ones for those files)
        merged_result["references"].extend(new_result["references"])

        # Merge source files
        # Keep cached files that are NOT in the new result (unchanged files)
        for file_path in cached_result["source_files"]:
            if str(file_path) not in new_file_paths:
                merged_result["source_files"].append(file_path)

        # Add all source files from new result (changed files that were reanalyzed)
        merged_result["source_files"].extend(new_result["source_files"])

        logger.info("Merged cached and new analysis results")
        return merged_result

    def _serialize_call_graph(self, call_graph: CallGraph) -> dict:
        """Serialize CallGraph to JSON-compatible format."""
        nodes_data = {}
        for node_name, node in call_graph.nodes.items():
            nodes_data[node_name] = {
                "fully_qualified_name": node.fully_qualified_name,
                "file_path": node.file_path,
                "line_start": node.line_start,
                "line_end": node.line_end,
                "type": node.type,
            }

        edges_data = []
        for edge in call_graph.edges:
            edges_data.append([edge.get_source(), edge.get_destination()])

        return {"nodes": nodes_data, "edges": edges_data}

    def _deserialize_call_graph(self, call_graph_data: dict) -> CallGraph:
        """Deserialize CallGraph from JSON format."""
        call_graph = CallGraph()

        # Add nodes
        for node_name, node_data in call_graph_data["nodes"].items():
            node = Node(
                fully_qualified_name=node_data["fully_qualified_name"],
                node_type=node_data["type"],
                file_path=node_data["file_path"],
                line_start=node_data["line_start"],
                line_end=node_data["line_end"],
            )
            call_graph.add_node(node)

        # Add edges
        for src_name, dst_name in call_graph_data["edges"]:
            try:
                call_graph.add_edge(src_name, dst_name)
            except ValueError:
                # Edge references non-existent nodes - skip it
                logger.debug(f"Skipping edge {src_name} -> {dst_name}: nodes not found")

        return call_graph

    def _serialize_references(self, references: list[Node]) -> list[dict]:
        """Serialize list of Node objects to JSON-compatible format."""
        return [
            {
                "fully_qualified_name": ref.fully_qualified_name,
                "file_path": ref.file_path,
                "line_start": ref.line_start,
                "line_end": ref.line_end,
                "type": ref.type,
            }
            for ref in references
        ]

    def _deserialize_references(self, references_data: list[dict]) -> list[Node]:
        """Deserialize list of Node objects from JSON format."""
        return [
            Node(
                fully_qualified_name=ref_data["fully_qualified_name"],
                node_type=ref_data["type"],
                file_path=ref_data["file_path"],
                line_start=ref_data["line_start"],
                line_end=ref_data["line_end"],
            )
            for ref_data in references_data
        ]

    def remove_nodes_for_files(self, call_graph: CallGraph, file_paths: set[str]) -> tuple[CallGraph, set[str]]:
        """
        Remove all nodes from the call graph that belong to the specified files.

        Args:
            call_graph: CallGraph to filter
            file_paths: Set of file paths to remove nodes from

        Returns:
            Tuple of (filtered_call_graph, set_of_removed_node_names)
        """
        filtered_graph = CallGraph()
        removed_nodes = set()

        for node_name, node in call_graph.nodes.items():
            if node.file_path in file_paths:
                removed_nodes.add(node_name)
            else:
                filtered_graph.add_node(node)

        return filtered_graph, removed_nodes

    def remove_edges_referencing_nodes(self, call_graph: CallGraph, removed_nodes: set[str]) -> CallGraph:
        """
        Remove all edges that reference any of the specified nodes.

        Args:
            call_graph: CallGraph to filter edges from
            removed_nodes: Set of node names that have been removed

        Returns:
            CallGraph with edges filtered to exclude references to removed nodes
        """
        filtered_graph = CallGraph()

        # Copy all existing nodes
        for node_name, node in call_graph.nodes.items():
            filtered_graph.add_node(node)

        # Add only edges that don't reference removed nodes
        for edge in call_graph.edges:
            src_name = edge.get_source()
            dst_name = edge.get_destination()

            if src_name not in removed_nodes and dst_name not in removed_nodes:
                try:
                    filtered_graph.add_edge(src_name, dst_name)
                except ValueError:
                    # Edge references nodes that don't exist - skip it
                    logger.debug(f"Skipping edge {src_name} -> {dst_name}: nodes not found")

        return filtered_graph

    def remove_class_hierarchies_for_files(
        self, class_hierarchies: dict[str, Any], file_paths: set[str]
    ) -> dict[str, Any]:
        """
        Remove class hierarchies that belong to the specified files.

        Args:
            class_hierarchies: Dictionary of class hierarchy information
            file_paths: Set of file paths to remove class hierarchies from

        Returns:
            Filtered class hierarchies dictionary
        """
        filtered_hierarchies = {}

        for class_name, class_info in class_hierarchies.items():
            class_file_path = class_info.get("file_path", "")
            if class_file_path not in file_paths:
                filtered_hierarchies[class_name] = class_info.copy()

        return filtered_hierarchies

    def remove_package_relations_for_files(
        self, package_relations: dict[str, Any], file_paths: set[str]
    ) -> dict[str, Any]:
        """
        Update package relations to exclude the specified files.
        Removes entire packages if they have no remaining files.

        Args:
            package_relations: Dictionary of package relation information
            file_paths: Set of file paths to remove from package relations

        Returns:
            Updated package relations dictionary
        """
        filtered_relations = {}

        for package_name, package_info in package_relations.items():
            original_files = package_info.get("files", [])
            remaining_files = [f for f in original_files if f not in file_paths]

            if remaining_files:
                # Package still has files, keep it with updated file list
                updated_package_info = package_info.copy()
                updated_package_info["files"] = remaining_files
                filtered_relations[package_name] = updated_package_info
            # If no remaining files, package is completely removed (not added to filtered_relations)

        return filtered_relations

    def remove_references_for_files(self, references: list[Node], file_paths: set[str]) -> list[Node]:
        """
        Remove references that belong to the specified files.

        Args:
            references: List of Node objects representing references
            file_paths: Set of file paths to remove references from

        Returns:
            Filtered list of references
        """
        filtered_references = []

        for ref in references:
            if ref.file_path not in file_paths:
                filtered_references.append(ref)

        return filtered_references

    def identify_analysis_data_for_files(
        self, analysis_result: dict[str, Any], file_paths: set[Path]
    ) -> dict[str, Any]:
        """
        Identify all analysis data associated with the specified files.

        This method provides a comprehensive view of what data would be affected
        by invalidating the specified files, useful for debugging and validation.

        Args:
            analysis_result: Dictionary containing analysis results
            file_paths: Set of file paths to analyze

        Returns:
            Dictionary containing counts and details of affected data:
            {
                'nodes': list of node names,
                'edges': list of edge descriptions,
                'class_hierarchies': list of class names,
                'package_relations': list of package names,
                'references': list of reference names,
                'summary': dict with counts
            }
        """
        file_path_strs = {str(path) for path in file_paths}

        # Identify affected nodes
        affected_nodes = []
        call_graph = analysis_result["call_graph"]
        for node_name, node in call_graph.nodes.items():
            if node.file_path in file_path_strs:
                affected_nodes.append(node_name)

        # Identify affected edges (edges that reference affected nodes)
        affected_edges = []
        for edge in call_graph.edges:
            src_name = edge.get_source()
            dst_name = edge.get_destination()
            src_node = call_graph.nodes[src_name]
            dst_node = call_graph.nodes[dst_name]

            if src_node.file_path in file_path_strs or dst_node.file_path in file_path_strs:
                affected_edges.append(f"{src_name} -> {dst_name}")

        # Identify affected class hierarchies
        affected_classes = []
        for class_name, class_info in analysis_result["class_hierarchies"].items():
            if class_info.get("file_path", "") in file_path_strs:
                affected_classes.append(class_name)

        # Identify affected package relations
        affected_packages = []
        for package_name, package_info in analysis_result["package_relations"].items():
            package_files = package_info.get("files", [])
            if any(f in file_path_strs for f in package_files):
                affected_packages.append(package_name)

        # Identify affected references
        affected_references = []
        for ref in analysis_result["references"]:
            if ref.file_path in file_path_strs:
                affected_references.append(ref.fully_qualified_name)

        return {
            "nodes": affected_nodes,
            "edges": affected_edges,
            "class_hierarchies": affected_classes,
            "package_relations": affected_packages,
            "references": affected_references,
            "summary": {
                "node_count": len(affected_nodes),
                "edge_count": len(affected_edges),
                "class_count": len(affected_classes),
                "package_count": len(affected_packages),
                "reference_count": len(affected_references),
            },
        }

    def _validate_cache_structure(self, cache_data: dict) -> bool:
        """Validate that cache data has the expected structure."""
        required_top_level = {
            "metadata",
            "call_graph",
            "class_hierarchies",
            "package_relations",
            "references",
            "source_files",
        }
        if not all(key in cache_data for key in required_top_level):
            return False

        # Validate metadata structure
        metadata = cache_data.get("metadata", {})
        required_metadata = {"commit_hash", "iteration_id", "timestamp", "version"}
        if not all(key in metadata for key in required_metadata):
            return False

        # Validate call graph structure
        call_graph = cache_data.get("call_graph", {})
        if not isinstance(call_graph, dict) or "nodes" not in call_graph or "edges" not in call_graph:
            return False

        return True
