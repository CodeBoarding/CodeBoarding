"""The known ways a run finishes with less than it should have.

One builder per code, each owning its own wording. Consumers (the CLI, the
GitHub Action, the webview) render ``title``/``detail``/``remedy`` verbatim, so
this module is the single place the user-facing phrasing lives.

A ``remedy`` is written only where the reader could actually change the outcome.
Where the cause is ours, it stays empty and the surfaces offer "report this"
instead of an instruction nobody can follow.
"""

from run_diagnostics.models import Diagnostic, DiagnosticSeverity

# Below this share of a repository's code, an unanalyzable language is a stray
# script rather than a hole in the diagram, and saying so every run is noise.
UNANALYZED_LANGUAGE_MIN_SHARE = 5.0


def language_server_unavailable(language: str, reason: str) -> Diagnostic:
    """A configured language server never came up; that language is absent entirely."""
    return Diagnostic(
        code="static.language_server_unavailable",
        severity=DiagnosticSeverity.DEGRADED,
        title=f"{language} could not be analyzed",
        detail=(
            f"Its language server failed to start ({reason}), so no {language} file contributed a "
            "component, a call or a relation to this diagram."
        ),
        remedy=(
            f"Check the run log for the server's own error and confirm the {language} toolchain is "
            "installed where the analysis ran, then run the analysis again."
        ),
        subject=language,
    )


def language_engine_failed(language: str, reason: str) -> Diagnostic:
    """The server started but indexing raised; the language's symbols are missing."""
    return Diagnostic(
        code="static.language_engine_failed",
        severity=DiagnosticSeverity.DEGRADED,
        title=f"{language} analysis did not finish",
        detail=(
            f"The {language} language server errored while indexing ({reason}). Components that own "
            f"{language} code look smaller than they are, and calls in or out of it are missing."
        ),
        remedy=(
            "Confirm the project builds and its dependencies are installed where the analysis ran, "
            "then run the analysis again."
        ),
        subject=language,
    )


def language_not_supported(language: str, share_percent: float) -> Diagnostic:
    """A material part of the repository is written in a language we cannot read."""
    return Diagnostic(
        code="static.language_not_supported",
        severity=DiagnosticSeverity.DEGRADED,
        title=f"{language} is not analyzed",
        detail=(
            f"{share_percent:.0f}% of this repository's code is {language}, which CodeBoarding has no "
            "language server for. Those files are absent from the diagram, and so is anything that "
            "only they reach."
        ),
        subject=language,
    )


def no_source_files(language: str) -> Diagnostic:
    """The scan found the language, the pass found no files — almost always a filter."""
    return Diagnostic(
        code="static.no_source_files",
        severity=DiagnosticSeverity.DEGRADED,
        title=f"No {language} files were indexed",
        detail=(
            f"The scan detected {language} in this repository, but no {language} file survived the "
            "analysis filters. Everything written in it is missing from the diagram."
        ),
        remedy=(
            "Check .codeboardingignore and the project's own configuration (for example the include "
            "patterns in tsconfig.json) for a rule that excludes them."
        ),
        subject=language,
    )


def names_not_generated(unnamed: int, total: int) -> Diagnostic:
    """Semantic naming did not answer, so components kept their deterministic names."""
    return Diagnostic(
        code="semantics.names_not_generated",
        severity=DiagnosticSeverity.DEGRADED,
        title="Some components kept their fallback names",
        detail=(
            f"Naming did not complete for {unnamed} of {total} scopes, so those components are named "
            "after the folders they were drawn from rather than what they do. Their grouping, members "
            "and relations are unaffected."
        ),
        remedy=(
            "This is usually a provider timeout or a rate limit. Run the analysis again to name them; "
            "check the run log for the provider's own error if it repeats."
        ),
    )
