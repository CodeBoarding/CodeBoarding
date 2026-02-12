from __future__ import annotations

# Shared manifest/build-configuration filenames used across:
# - dependency discovery (ExternalDepsTool + meta cache invalidation)
# - language-specific project scanners (TypeScript/Java)
#
# Keep these constants centralized to avoid drift between scanners and cache/tools.

# Python ecosystem
PYTHON_DEPENDENCY_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "dev-requirements.txt",
    "test-requirements.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "environment.yml",
    "environment.yaml",
    "conda.yml",
    "conda.yaml",
    "pixi.toml",
    "uv.lock",
)

# Node.js / TypeScript ecosystem
NODE_PACKAGE_MANIFEST_FILE = "package.json"
TYPESCRIPT_CONFIG_FILES: tuple[str, ...] = ("tsconfig.json", "jsconfig.json")
TYPESCRIPT_WORKSPACE_CONFIG_FILES: tuple[str, ...] = TYPESCRIPT_CONFIG_FILES + (NODE_PACKAGE_MANIFEST_FILE,)
NODE_DEPENDENCY_FILES: tuple[str, ...] = (
    NODE_PACKAGE_MANIFEST_FILE,
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "bun.lock",
)

# Java ecosystem
JAVA_MAVEN_POM_FILE = "pom.xml"
JAVA_GRADLE_SETTINGS_FILES: tuple[str, ...] = ("settings.gradle", "settings.gradle.kts")
JAVA_DEPENDENCY_FILES: tuple[str, ...] = (
    JAVA_MAVEN_POM_FILE,
    *JAVA_GRADLE_SETTINGS_FILES,
    "build.gradle",
    "build.gradle.kts",
    "gradle.properties",
    "gradle.lockfile",
)

# Go ecosystem
GO_DEPENDENCY_FILES: tuple[str, ...] = (
    "go.mod",
    "go.sum",
    "go.work",
    "go.work.sum",
)

# PHP ecosystem
PHP_DEPENDENCY_FILES: tuple[str, ...] = (
    "composer.json",
    "composer.lock",
)

COMMON_DEPENDENCY_FILES: tuple[str, ...] = (
    *PYTHON_DEPENDENCY_FILES,
    *NODE_DEPENDENCY_FILES,
    *JAVA_DEPENDENCY_FILES,
    *GO_DEPENDENCY_FILES,
    *PHP_DEPENDENCY_FILES,
)
