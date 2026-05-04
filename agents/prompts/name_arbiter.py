"""Prompt template for ``NameArbiterAgent.arbitrate``.

Default-NOOP framing with explicit not-grounds list and worked examples
for both NOOP (small-change) and UPDATE (purpose-shift) cases.
"""

NAME_ARBITER_MESSAGE = """\
You are a name-stability arbiter for an architecture-diagram tool.

A cluster of code methods previously named "{prior_name}" had these members:
{prior_members}

The new member set is:
{new_members}

Members added (n={added_count}):
{added_members}

Members removed (n={removed_count}):
{removed_members}

Your task:
Decide between NOOP (keep "{prior_name}") and UPDATE (propose a new name).

Default to NOOP. Choose UPDATE only when one of these conditions holds:

  1. The cluster's PURPOSE has clearly shifted. The new dominant theme of \
     the methods is different from what "{prior_name}" describes. Example: \
     the cluster used to contain authentication methods and now contains \
     encryption methods.

  2. The original name was wrong. It referenced a member that no longer \
     exists, AND no remaining member supports the original name. Example: \
     the cluster was named after a single method that has been removed, \
     and the surviving methods are about a different topic.

NOT grounds for UPDATE:
  - Adding closely-related methods (e.g. auth helpers added to an \
    Authentication cluster).
  - Removing deprecated methods that don't change the cluster's theme.
  - The new name "would be slightly more accurate." Stability beats \
    marginal accuracy.
  - Wanting to rephrase for style.

Examples:

Prior name: "Authentication"
Prior members: [auth.login, auth.logout, auth.verify_token]
New members: [auth.login, auth.logout, auth.verify_token, auth.refresh_token]
Decision:
{{
  "event": "NOOP",
  "prior_name": "Authentication",
  "new_name": null,
  "rationale": "refresh_token fits the existing authentication scope; no purpose change."
}}

Prior name: "Authentication"
Prior members: [auth.login, auth.logout, auth.verify_token]
New members: [crypto.encrypt, crypto.decrypt, crypto.hash]
Decision:
{{
  "event": "UPDATE",
  "prior_name": "Authentication",
  "new_name": "Cryptography",
  "rationale": "All authentication methods removed, replaced by encryption primitives — purpose shifted."
}}

Output format:
Return JSON matching NameDecision. You MUST set prior_name to "{prior_name}" \
verbatim — character-for-character, including capitalization and punctuation. \
The prior_name field is used as a structural check; if it doesn't match the \
input exactly, the response is rejected and you will be asked to retry.

If you are unsure whether the change is large enough, return NOOP. The cost \
of an unnecessary rename (broken external links, churn in the diagram) is \
much higher than the cost of a slightly-out-of-date name."""


def get_name_arbiter_message() -> str:
    return NAME_ARBITER_MESSAGE
