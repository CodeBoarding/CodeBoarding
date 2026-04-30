"""Incremental-update engine.

Owns everything that turns "the analysis we already had + a diff" into
"the analysis after the diff": pipeline, tracer, updater, scope planner,
delta application, payload types. Depends on ``analysis_format`` for the
on-disk schema and on the agent + static-analysis primitives. The
orchestration layer (``codeboarding_workflows``) is the only caller.
"""
