"""Prompt template for ``IncrementalAgent.step_group_delta``.

Why this is provider-agnostic: the incremental grouping prompt is a thinner
restatement of ``CLUSTER_GROUPING_MESSAGE`` plus a context block listing the
existing components. It's a routing decision, not a creative-writing task, so
the variation across LLM families is small enough that a single template
suffices. If a provider eventually needs a tuned variant, fold it back into
``prompt_factory`` like the other prompts.
"""

INCREMENTAL_GROUPING_MESSAGE = """\
Update the architecture of `{project_name}` by routing changed and new CFG \
clusters to the right components.

Tool usage policy for this routing task:
For each cluster you're uncertain about, you may read source. Keep each read \
small and targeted — the source of a single representative qname is usually \
the right unit. Continue reading further (still in small, focused steps) only \
while you remain uncertain about that specific cluster's placement, and stop \
as soon as your confidence is high. Don't broaden the scope of a single read \
to cover ground you don't yet need.

Project Context:
{meta_context}

Project Type: {project_type}

The previous analysis established the components below. Most clusters are \
unchanged and stay where they are; this prompt only shows the slice that \
changed (new clusters or clusters whose member methods changed).

### Existing components (each line shows `component_id "name"`)
{existing_components}

### Cluster groups to assign
{cfg_clusters}

Your task:
For each cluster id shown above, decide which component it belongs to. \
There are exactly two options:

1. **Route to an existing component.** Set `existing_component_id` to the \
   exact component_id from the list above (e.g. `"1.3"`). Reuse that \
   component's `name` and a short `description` verbatim, and put the \
   cluster ids in `cluster_ids`. Several entries may share the same \
   `existing_component_id` if multiple groups of clusters route to the \
   same component.

2. **Create a new component.** Leave `existing_component_id` as null, \
   provide a fresh `name` (distinct from every existing component), write \
   a `description` paragraph explaining what this new component does and \
   why these clusters belong together, and set `parent_id` to the \
   component_id under which this new component should attach (or null \
   for root). Choose the parent whose scope most naturally encloses the \
   new component.

Identity is by component_id, not by name. Reusing an existing \
component's name without setting `existing_component_id` will fork a \
duplicate component — that is wrong. If clusters belong in an existing \
component, you MUST set `existing_component_id`.

Output format:
Return a `ClusterAnalysis` with `cluster_components`. For every entry, set:
  - `name` (string),
  - `cluster_ids` (list of integers, the cluster ids assigned to this entry),
  - `description` (string),
  - `existing_component_id` (string component_id, or null) — set when \
    routing to an existing component; null when creating a new one,
  - `parent_id` (string component_id, or null) — required when \
    `existing_component_id` is null; ignored otherwise.

Coverage requirement: every cluster id listed in the "Cluster groups to \
assign" section must appear in exactly one entry's `cluster_ids`."""


def get_incremental_grouping_message() -> str:
    return INCREMENTAL_GROUPING_MESSAGE
