from constants import (
    ANALYSIS_FILENAME,
    CODEBOARDING_VERSION_FILENAME,
    FINGERPRINT_FILENAME,
    PERSISTED_ANALYSIS_ARTIFACT_FILENAMES,
    STATIC_ANALYSIS_PKL,
    STATIC_ANALYSIS_SHA,
)
from static_analyzer.analysis_cache import (
    STATIC_ANALYSIS_PKL as COMPAT_STATIC_ANALYSIS_PKL,
    STATIC_ANALYSIS_SHA as COMPAT_STATIC_ANALYSIS_SHA,
)
from utils import ANALYSIS_FILENAME as COMPAT_ANALYSIS_FILENAME
from utils import FINGERPRINT_FILENAME as COMPAT_FINGERPRINT_FILENAME


def test_persisted_analysis_artifact_manifest_and_compatibility_imports():
    assert PERSISTED_ANALYSIS_ARTIFACT_FILENAMES == (
        "analysis.json",
        "fingerprint.json",
        "static_analysis.pkl",
        "static_analysis.sha",
        "codeboarding_version.json",
    )
    assert CODEBOARDING_VERSION_FILENAME == "codeboarding_version.json"
    assert COMPAT_ANALYSIS_FILENAME == ANALYSIS_FILENAME
    assert COMPAT_FINGERPRINT_FILENAME == FINGERPRINT_FILENAME
    assert COMPAT_STATIC_ANALYSIS_PKL == STATIC_ANALYSIS_PKL
    assert COMPAT_STATIC_ANALYSIS_SHA == STATIC_ANALYSIS_SHA
