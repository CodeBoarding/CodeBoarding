"""Clustering stage of the analysis pipeline.

``ClusteringService`` (``service``) turns ``StaticAnalysisResults`` into
``ClusteringResults`` — leaf clusters, scoped call graphs, and their
deterministic grouping — which the agents receive as their single analysis
input. ``models`` holds the stage's data types; ``separability`` the
deterministic split-or-keep policy.

Import from the submodules directly: this ``__init__`` stays import-free
because ``static_analyzer.graph`` pulls in ``models`` while the
``static_analyzer`` package itself is still initializing.
"""
