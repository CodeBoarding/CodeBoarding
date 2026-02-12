from __future__ import annotations

# Shared dependency-discovery patterns used by both:
# - ExternalDepsTool (tooling output for agents)
# - MetaCache dependency fingerprinting (cache invalidation inputs)
#
# Keeping a single source of truth prevents drift where one consumer
# updates dependency file patterns but the other does not.
COMMON_DEPENDENCY_FILES: tuple[str, ...] = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "dev-requirements.txt",
    "test-requirements.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "environment.yml",
    "environment.yaml",
    "conda.yml",
    "conda.yaml",
    "pixi.toml",
    "uv.lock",
    # Node.js / TypeScript specific
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "tsconfig.json",  # TypeScript compiler configuration (not dependencies, but relevant)
)

COMMON_DEPENDENCY_SUBDIRS: tuple[str, ...] = ("requirements", "deps", "dependencies", "env")
COMMON_DEPENDENCY_GLOBS: tuple[str, ...] = ("*.txt", "*.yml", "*.yaml", "*.toml")
