"""Prompts for the retained metadata and tree-planning agents."""

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

SYSTEM_META_ANALYSIS_MESSAGE = """You extract high-level project metadata for architecture documentation.

Use project documentation, repository structure, and dependency metadata as the primary evidence.
Identify the project type, domain, technology stack, and broad architectural patterns. Focus on
facts that help a maintainer understand the project rather than implementation details."""

META_INFORMATION_PROMPT = """Analyze project '{project_name}' and return its project type, domain,
technology stack, architectural patterns, expected component categories, and concise architectural
guidance. Read documentation and dependency metadata first and use at most two targeted tool calls."""

VALIDATION_FEEDBACK_MESSAGE = """Correct the output below without regenerating correct parts.

Previous output:
{original_output}

Issues to fix:
{feedback_list}

Original task context:
{original_prompt}"""
