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

### Existing components (do NOT invent name variants of these)
{existing_components}

### Cluster groups to assign
{cfg_clusters}

Your task:
For each cluster id shown above, decide which component it belongs to. There \
are exactly two options:
1. Assign the cluster to an existing component by reusing that component's \
   exact name. Several clusters may share the same component name.
2. Create a new component when no existing component fits. Provide:
   - **name**: a short, descriptive name distinct from every existing one
   - **description**: one paragraph explaining what this new component does \
     and why these clusters belong together
   - **parent_id**: the existing component_id under which this new component \
     should attach, or null to attach at root. Choose the parent whose scope \
     most naturally encloses the new component.

Output format:
Return a `ClusterAnalysis` with `cluster_components`. For every entry, set:
  - `name` (string),
  - `cluster_ids` (list of integers, the cluster ids assigned to this entry),
  - `description` (string; for existing components reuse a short summary; for \
    new components write a fresh paragraph),
  - `parent_id` (string component_id, or null) — required for new components, \
    ignored for existing ones.

Coverage requirement: every cluster id listed in the "Cluster groups to \
assign" section must appear in exactly one entry's `cluster_ids`."""


def get_incremental_grouping_message() -> str:
    return INCREMENTAL_GROUPING_MESSAGE
