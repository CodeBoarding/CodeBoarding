from __future__ import annotations

from repo_utils.project_manifests import COMMON_DEPENDENCY_FILES

# Shared dependency-discovery patterns used by both:
# - ExternalDepsTool (tooling output for agents)
# - MetaAgentCache dependency fingerprinting (cache invalidation inputs)
#
# Keeping a single source of truth prevents drift where one consumer
# updates dependency file patterns but the other does not.
COMMON_DEPENDENCY_SUBDIRS: tuple[str, ...] = ("requirements", "deps", "dependencies", "env")
COMMON_DEPENDENCY_GLOBS: tuple[str, ...] = ("*.txt", "*.yml", "*.yaml", "*.toml")
