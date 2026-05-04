"""Build a ``PriorClusterIndex`` from a live analysis tree.

Walks the root + every sub-analysis, flattens each ``Component.file_methods``
into a qname set, and produces an index of ``(name, members)`` pairs the
arbiter can match new clusters against.
"""

from agents.agent_responses import AnalysisInsights, iter_components
from agents.prior_cluster_index import PriorCluster, PriorClusterIndex


def build_prior_index(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> PriorClusterIndex:
    """Construct the prior index from the live tree.

    Why every level: ``DetailsAgent.run`` recurses through ``depth_level``,
    naming sub-clusters at each layer. The arbiter has to apply at every
    layer, so the index must include components from every layer too.
    Components without a name or with no method-level members are skipped —
    they can't function as anchors for similarity matching.
    """
    priors: list[PriorCluster] = []
    for component in iter_components(root_analysis, sub_analyses):
        if not component.name:
            continue
        members = {m.qualified_name for fm in component.file_methods for m in fm.methods}
        if not members:
            continue
        priors.append(PriorCluster(name=component.name, members=frozenset(members)))
    return PriorClusterIndex(priors=priors)
