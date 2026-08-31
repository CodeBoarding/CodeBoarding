"""Application-level constants for CodeBoarding."""

CODEBOARDING_DIR_NAME = ".codeboarding"
ANALYSIS_FILENAME = "analysis.json"
FINGERPRINT_FILENAME = "fingerprint.json"
STATIC_ANALYSIS_PKL = "static_analysis.pkl"
STATIC_ANALYSIS_SHA = "static_analysis.sha"
CODEBOARDING_VERSION_FILENAME = "codeboarding_version.json"
PERSISTED_ANALYSIS_ARTIFACT_FILENAMES = (
    ANALYSIS_FILENAME,
    FINGERPRINT_FILENAME,
    STATIC_ANALYSIS_PKL,
    STATIC_ANALYSIS_SHA,
    CODEBOARDING_VERSION_FILENAME,
)
# Ignore-file names read from a repo (``.gitignore`` at the repo root,
# ``.codeboardingignore`` under ``CODEBOARDING_DIR_NAME``).
GITIGNORE_FILENAME = ".gitignore"
CODEBOARDINGIGNORE_FILENAME = ".codeboardingignore"
DEFAULT_STATIC_RELATION_LABEL = "calls"


class AppConfig:
    MAX_CONCURRENT_JOBS = 5
    DEFAULT_REPO_ROOT = "./repos"
    DEFAULT_ROOT_RESULT = "./results"
    DEFAULT_LLM_SIZE_LIMIT = 2_500_000
