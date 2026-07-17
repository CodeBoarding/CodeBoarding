"""Prompt and ordering helpers for Infomap-owned architecture partitions."""

from agents.analysis_result_responses import Component
from agents.full_analysis_responses import ClusterAnalysis


def module_architecture_prompt(
    cluster_analysis: ClusterAnalysis,
    cluster_evidence: str,
    scope_context: str = "",
) -> str:
    context = f"\n\nScope being decomposed:\n{scope_context}" if scope_context else ""
    return f"""Create the architecture for these deterministic Infomap modules.{context}

Infomap owns the structural partition. You may combine related modules into a coherent architecture component, but
you cannot split a module or assign it more than once. Every module name must occur in exactly one component's
source_group_names. Give every component a concise name, a current responsibility description, and two to five key
entities selected from its modules. Also describe the scope's overall purpose and main flow.

Modules:
{cluster_analysis.llm_str()}

Static module evidence:
{cluster_evidence}"""


def order_components_by_module(components: list[Component], cluster_analysis: ClusterAnalysis) -> list[Component]:
    module_rank = {module.name.casefold(): index for index, module in enumerate(cluster_analysis.cluster_components)}
    return sorted(
        components,
        key=lambda component: (
            min(module_rank[name.casefold()] for name in component.source_group_names),
            component.name.casefold(),
        ),
    )
