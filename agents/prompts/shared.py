"""Prompts for tree planning and deterministic scope analysis."""

TREE_PLAN_SYSTEM_MESSAGE = (
    "You group the candidate groups of one scope of a codebase into the components a maintainer "
    "would draw in an architecture diagram. Answer with JSON only."
)

TREE_PLAN_MESSAGE = """Scope: {scope} ({units} files in {count} candidate groups, listed largest first)
{groups}

Each candidate group has already merged the scopes that share a name. Fold them into at most
{budget} components, each for one responsibility, for example the web app, the mobile app and the
hybrid app into "Customer experiences".

Rules:
- Every label appears in exactly one component's members. Never split a candidate group.
- A component holds at least {floor} files. A group with fewer files joins the component whose
  purpose it serves; it never stands alone.
- Keep apart what does not belong together: use the budget before lumping unrelated groups.
- Name each component for its one responsibility. Never join two things with "and" or "&".
- owns: at most 5 lowercase single words this component claims beyond its group names, taken from
  the identifiers listed under its own groups. Never words naming how software is built (handler,
  service, repository, converter).

Answer with JSON only, no prose, in this shape:
{{"groups": [{{"name": "Customer experiences", "members": ["G1", "G4"], "owns": ["customer"]}}]}}
"""

SCOPE_ANALYSIS_SYSTEM_MESSAGE = """You name and describe fixed, deterministic component groups in one code scope.

The supplied group IDs, file membership, hierarchy, and known directed calls are authoritative. Never merge,
split, move, add, or remove groups. Work at the supplied scope exactly the same way whether it is root or nested.

Use the supplied files, grouping reasons, bordering files, and known connections before calling tools. A known
connection gives the number of distinct calls from one group to another and a few of them in full; cite only
symbols that appear in the input. Never reuse a name listed under `enclosing_components`: those are the
components this scope sits inside, and a child named after its parent overwrites its document. `readFile
may inspect a specific in-scope file and `getMethodCalls` may inspect one exact in-scope symbol in one direction.
Do not inventory the scope, browse every file, or repeat a tool call. A runtime budget limits all tools to six calls.

Name each editable group for one responsibility using the codebase's own vocabulary, add a concise description,
and select only clearly evidenced key entities. Label known directed connections by their architectural meaning.
You may add a missing non-static relation such as REST, queue, plugin, registry, file, or configuration wiring only
when exact source symbols on both component sides and concrete textual evidence support it. Names alone are not
evidence. Return JSON only, with no markdown fences."""

SCOPE_ANALYSIS_MESSAGE = """Analyze this deterministic scope:

{scope_context}

Return exactly this JSON shape:
{{
  "description": "one paragraph describing this scope's purpose and main flow",
  "components": [
    {{
      "group_id": "an exact supplied group_id",
      "name": "one responsibility",
      "description": "one concise sentence",
      "key_entities": [{{"qualified_name": "exact in-scope symbol"}}]
    }}
  ],
  "relations": [
    {{
      "source_group_id": "exact supplied group_id",
      "target_group_id": "exact supplied group_id",
      "relation": "short directed phrase",
      "evidence": "concrete evidence; empty for a supplied known connection",
      "key_edges": [
        {{
          "source": {{"qualified_name": "exact source-side symbol"}},
          "target": {{"qualified_name": "exact target-side symbol"}},
          "description": "how these symbols communicate"
        }}
      ]
    }}
  ]
}}

For incremental input, return component metadata only for groups whose status is `changed`. If `name_locked` is
true, repeat its existing name exactly. Relations may be returned for any pair touching a changed group; leave
relations between two unchanged groups alone. Include one component entry per editable group. Do not invent group
IDs, file membership, source symbols, or calls."""

STRICT_TREE_PLAN_SYSTEM_SUFFIX = """

Follow every constraint literally. Do not explain your reasoning or emit markdown."""

STRICT_TREE_PLAN_MESSAGE_SUFFIX = """

Before answering, verify that every supplied G-label appears exactly once, no unknown label appears,
and the result has no more than {budget} groups. Emit one JSON object and nothing else."""

STRICT_SCOPE_ANALYSIS_SYSTEM_SUFFIX = """

Mandatory execution order:
1. Read all supplied group and boundary context before deciding whether a tool is needed.
2. Use a tool only to answer one unresolved question about a named file or symbol.
3. Check every component, relation direction, and source symbol against the supplied context.
4. Emit one JSON object and nothing else. Do not explain your reasoning or use markdown."""

STRICT_SCOPE_ANALYSIS_MESSAGE_SUFFIX = """

Final checklist before answering:
- `components` contains exactly one entry for every editable group and no other group.
- Every group ID and qualified name is copied exactly from the input.
- Every relation is directed from source to target and has concrete evidence unless it is already known.
- The response is one valid JSON object with no prose before or after it."""
