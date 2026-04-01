"""EASE-encoded JSON Patch flow for incremental sub-analysis updates.

Given impacted components from the tracer, extracts the parent sub-analysis,
EASE-encodes it, asks the LLM for RFC 6902 patches, applies them, validates
the result, and merges back into the full analysis.
"""

import json
import logging
from typing import Any

import jsonpatch
from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError
from trustcall import create_extractor

from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference
from diagram_analysis.ease import ease_decode, ease_encode
from diagram_analysis.incremental_models import AnalysisPatch, ImpactedComponent

logger = logging.getLogger(__name__)

MAX_PATCH_RETRIES = 3


# ---------------------------------------------------------------------------
# Extract sub-analysis as a plain dict
# ---------------------------------------------------------------------------
def _sub_analysis_to_dict(sub: AnalysisInsights) -> dict[str, Any]:
    """Serialize a sub-analysis to a plain dict suitable for EASE encoding."""
    components = []
    for c in sub.components:
        comp: dict[str, Any] = {
            "name": c.name,
            "component_id": c.component_id,
            "description": c.description,
            "key_entities": [
                {
                    "qualified_name": ke.qualified_name,
                    "reference_file": ke.reference_file,
                    "reference_start_line": ke.reference_start_line,
                    "reference_end_line": ke.reference_end_line,
                }
                for ke in c.key_entities
            ],
        }
        components.append(comp)

    relations = [
        {
            "relation": r.relation,
            "src_name": r.src_name,
            "dst_name": r.dst_name,
            "src_id": r.src_id,
            "dst_id": r.dst_id,
        }
        for r in sub.components_relations
    ]
    return {
        "description": sub.description,
        "components": components,
        "components_relations": relations,
    }


def _encode_sub_analysis(sub_dict: dict[str, Any]) -> dict[str, Any]:
    """EASE-encode a sub-analysis dict."""
    encoded = ease_encode(sub_dict, ["components", "components_relations"])
    # Encode nested arrays within each component
    if isinstance(encoded.get("components"), dict):
        for key, comp in encoded["components"].items():
            if key == "display_order" or not isinstance(comp, dict):
                continue
            encoded["components"][key] = ease_encode(comp, ["key_entities"])
    return encoded


def _decode_sub_analysis(encoded: dict[str, Any]) -> dict[str, Any]:
    """Decode EASE-encoded sub-analysis back to plain arrays."""
    if isinstance(encoded.get("components"), dict):
        for key, comp in encoded["components"].items():
            if key == "display_order" or not isinstance(comp, dict):
                continue
            encoded["components"][key] = ease_decode(comp, ["key_entities"])
    return ease_decode(encoded, ["components", "components_relations"])


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
_PATCH_SYSTEM = """\
You are a precise JSON patch generator for software architecture diagrams.

Given an EASE-encoded sub-analysis and an impact dossier describing what
changed, produce RFC 6902 JSON Patch operations to update the sub-analysis.

EASE encoding: arrays are stored as dicts with two-character keys (aa, ab, ...)
and a display_order list. Use the two-character keys in your patch paths.

Rules:
- Only patch what actually changed. Untouched siblings must remain as-is.
- Use "replace" for updating existing values.
- Use "add" for new entries (append to display_order too).
- Use "remove" for deleted entries (remove from display_order too).
- Paths use JSON Pointer syntax: /components/aa/description
"""


def _build_patch_prompt(
    encoded_sub: dict[str, Any],
    impact: ImpactedComponent,
    sub_analysis_id: str,
) -> str:
    parts = [
        "# Current Sub-Analysis (EASE-encoded)\n",
        f"```json\n{json.dumps(encoded_sub, indent=2)}\n```\n",
        "# Impact Dossier\n",
        f"Sub-analysis ID: {sub_analysis_id}\n",
        f"Impacted methods: {', '.join(impact.impacted_methods)}\n",
        "\nGenerate a patch to update component descriptions, key_entities, and relations",
        "to reflect the semantic changes indicated by the impacted methods.",
        "Respond with sub_analysis_id, reasoning, and patches (list of op/path/value).",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Apply patches
# ---------------------------------------------------------------------------
def _apply_patches(encoded: dict[str, Any], patch_ops: list[dict]) -> dict[str, Any]:
    """Apply RFC 6902 patches to an EASE-encoded dict."""
    patch = jsonpatch.JsonPatch(patch_ops)
    return patch.apply(encoded)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate_patched(decoded: dict[str, Any]) -> AnalysisInsights | None:
    """Validate that a decoded sub-analysis can round-trip to AnalysisInsights."""
    try:
        components = []
        for c in decoded.get("components", []):
            components.append(
                Component(
                    name=c["name"],
                    component_id=c.get("component_id", ""),
                    description=c["description"],
                    key_entities=c.get("key_entities", []),
                )
            )
        relations = [
            Relation(
                relation=r["relation"],
                src_name=r["src_name"],
                dst_name=r["dst_name"],
                src_id=r.get("src_id", ""),
                dst_id=r.get("dst_id", ""),
            )
            for r in decoded.get("components_relations", [])
        ]
        return AnalysisInsights(
            description=decoded.get("description", ""),
            components=components,
            components_relations=relations,
        )
    except (ValidationError, KeyError, TypeError) as exc:
        logger.warning("Patched sub-analysis failed validation: %s", exc)
        return None


def _merge_patched_onto_original(
    decoded: dict[str, Any],
    original: AnalysisInsights,
) -> AnalysisInsights | None:
    """Merge LLM-edited fields from *decoded* back onto *original*, preserving deterministic data.

    Updates ``name``, ``description``, ``key_entities``, and ``components_relations``
    from the decoded dict while keeping ``source_group_names``, ``source_cluster_ids``,
    ``file_methods``, ``component_id``, and the ``files`` index from *original*.

    Returns ``None`` (with a warning) when the decoded dict adds, removes, or
    renames component IDs — incremental patching is not meant to reshape structure.
    """
    try:
        decoded_components = decoded.get("components", [])
        original_by_id = {c.component_id: c for c in original.components}

        if len(decoded_components) != len(original_by_id):
            logger.warning(
                "Patch changed component count (%d -> %d); rejecting",
                len(original_by_id),
                len(decoded_components),
            )
            return None

        merged: list[Component] = []
        for c in decoded_components:
            cid = c.get("component_id", "")
            orig = original_by_id.get(cid)
            if orig is None:
                logger.warning("Patch introduced unknown component_id %r; rejecting", cid)
                return None
            key_entities = [
                SourceCodeReference(
                    qualified_name=ke["qualified_name"],
                    reference_file=ke.get("reference_file"),
                    reference_start_line=ke.get("reference_start_line"),
                    reference_end_line=ke.get("reference_end_line"),
                )
                for ke in c.get("key_entities", [])
            ]
            merged.append(
                Component(
                    name=c["name"],
                    description=c["description"],
                    key_entities=key_entities,
                    source_group_names=orig.source_group_names,
                    source_cluster_ids=orig.source_cluster_ids,
                    file_methods=orig.file_methods,
                    component_id=orig.component_id,
                )
            )

        relations = [
            Relation(
                relation=r["relation"],
                src_name=r["src_name"],
                dst_name=r["dst_name"],
                src_id=r.get("src_id", ""),
                dst_id=r.get("dst_id", ""),
            )
            for r in decoded.get("components_relations", [])
        ]

        return AnalysisInsights(
            description=decoded.get("description", ""),
            files=original.files,
            components=merged,
            components_relations=relations,
        )
    except (ValidationError, KeyError, TypeError) as exc:
        logger.warning("Merge onto original failed validation: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main patch flow
# ---------------------------------------------------------------------------
def patch_sub_analysis(
    sub_analysis: AnalysisInsights,
    sub_analysis_id: str,
    impact: ImpactedComponent,
    parsing_llm: BaseChatModel,
) -> AnalysisInsights | None:
    """Patch a single sub-analysis using EASE-encoded JSON patches.

    Returns the patched AnalysisInsights, or None if patching fails after retries.
    """
    sub_dict = _sub_analysis_to_dict(sub_analysis)
    encoded = _encode_sub_analysis(sub_dict)

    prompt = _build_patch_prompt(encoded, impact, sub_analysis_id)
    extractor = create_extractor(parsing_llm, tools=[AnalysisPatch], tool_choice=AnalysisPatch.__name__)

    last_error = ""
    for attempt in range(MAX_PATCH_RETRIES):
        try:
            user_content = prompt
            if last_error:
                user_content += f"\n\nPrevious attempt failed validation: {last_error}\nPlease fix the patch."

            result = extractor.invoke(  # type: ignore[arg-type]
                {"messages": [{"role": "system", "content": _PATCH_SYSTEM}, {"role": "user", "content": user_content}]}
            )
            if "responses" not in result or not result["responses"]:
                logger.warning("Patch extractor returned no responses (attempt %d)", attempt + 1)
                continue

            patch_response = AnalysisPatch.model_validate(result["responses"][0])
            patch_ops = [op.model_dump(exclude_none=True) for op in patch_response.patches]

            if not patch_ops:
                logger.info("LLM returned empty patch for %s — no changes needed", sub_analysis_id)
                return sub_analysis

            patched = _apply_patches(encoded, patch_ops)
            decoded = _decode_sub_analysis(patched)
            validated = _merge_patched_onto_original(decoded, sub_analysis)

            if validated is not None:
                logger.info("Successfully patched sub-analysis %s on attempt %d", sub_analysis_id, attempt + 1)
                return validated

            last_error = "Decoded result failed Pydantic validation"

        except jsonpatch.JsonPatchException as exc:
            last_error = f"JSON Patch application error: {exc}"
            logger.warning("Patch apply failed for %s (attempt %d): %s", sub_analysis_id, attempt + 1, exc)
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Patch flow error for %s (attempt %d): %s", sub_analysis_id, attempt + 1, exc)

    logger.error("Patching failed for sub-analysis %s after %d attempts", sub_analysis_id, MAX_PATCH_RETRIES)
    return None


# ---------------------------------------------------------------------------
# Merge patched sub-analyses back
# ---------------------------------------------------------------------------
def merge_patched_sub_analyses(
    sub_analyses: dict[str, AnalysisInsights],
    patched: dict[str, AnalysisInsights],
) -> None:
    """Merge patched sub-analyses back into the analysis structures. Mutates in place."""
    for sub_id, patched_sub in patched.items():
        if sub_id in sub_analyses:
            sub_analyses[sub_id] = patched_sub
            logger.info("Merged patched sub-analysis %s", sub_id)
        else:
            logger.warning("Patched sub-analysis %s not found in existing sub_analyses", sub_id)
