"""LSP Diagnostics-based health check for detecting dead/unused code.

This module provides health checks based on LSP server diagnostics, which is more
reliable than custom call graph analysis for detecting:
- Unused imports
- Unused variables
- Unused functions/methods
- Dead code
- Unreachable code
"""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from static_analyzer.lsp_client.diagnostics import LSPDiagnostic
from health.models import (
    FindingEntity,
    FindingGroup,
    HealthCheckConfig,
    Severity,
    StandardCheckSummary,
)

logger = logging.getLogger(__name__)


class DeadCodeCategory(str, Enum):
    """Categories of dead/unused code detected by LSP diagnostics."""

    UNUSED_IMPORT = "unused_import"
    UNUSED_VARIABLE = "unused_variable"
    UNUSED_FUNCTION = "unused_function"
    UNUSED_CLASS = "unused_class"
    UNUSED_PARAMETER = "unused_parameter"
    DEAD_CODE = "dead_code"
    UNREACHABLE_CODE = "unreachable_code"
    UNKNOWN = "unknown"


@dataclass
class DiagnosticIssue:
    """Represents a single diagnostic issue from LSP."""

    file_path: str
    line_start: int
    line_end: int
    message: str
    code: str | None
    category: DeadCodeCategory
    severity: Severity


# LSP DiagnosticTag values
DIAGNOSTIC_TAG_UNNECESSARY = 1

# Mapping of diagnostic codes to categories for different LSP servers
DIAGNOSTIC_CODE_MAPPINGS: dict[str, DeadCodeCategory] = {
    # Pyright/Pylance (Python)
    "reportUnusedImport": DeadCodeCategory.UNUSED_IMPORT,
    "reportUnusedVariable": DeadCodeCategory.UNUSED_VARIABLE,
    "reportUnusedFunction": DeadCodeCategory.UNUSED_FUNCTION,
    "reportUnusedClass": DeadCodeCategory.UNUSED_CLASS,
    "reportUnusedParameter": DeadCodeCategory.UNUSED_PARAMETER,
    "reportUnreachable": DeadCodeCategory.UNREACHABLE_CODE,
    # TypeScript/JavaScript
    "noUnusedLocals": DeadCodeCategory.UNUSED_VARIABLE,
    "noUnusedParameters": DeadCodeCategory.UNUSED_PARAMETER,
    "6133": DeadCodeCategory.UNUSED_VARIABLE,  # TS error code for unused locals
    "6138": DeadCodeCategory.UNUSED_PARAMETER,  # TS error code for unused parameters
    "6196": DeadCodeCategory.UNUSED_IMPORT,  # TS error code for unused imports
    # gopls (Go)
    "unusedparams": DeadCodeCategory.UNUSED_PARAMETER,
    "unusedvariable": DeadCodeCategory.UNUSED_VARIABLE,
    "shadow": DeadCodeCategory.UNKNOWN,
    # rust-analyzer (Rust)
    "dead_code": DeadCodeCategory.DEAD_CODE,
    "unused_variables": DeadCodeCategory.UNUSED_VARIABLE,
    "unused_imports": DeadCodeCategory.UNUSED_IMPORT,
    "unused_functions": DeadCodeCategory.UNUSED_FUNCTION,
    # Intelephense (PHP)
    "unusedSymbol": DeadCodeCategory.UNKNOWN,
    "unusedUseStatement": DeadCodeCategory.UNUSED_IMPORT,
    "P1001": DeadCodeCategory.UNUSED_VARIABLE,  # Intelephense unused variable
    "P1002": DeadCodeCategory.UNUSED_IMPORT,  # Intelephense unused import
    # Eclipse JDT (Java)
    "org.eclipse.jdt.core.compiler.problem.unusedImport": DeadCodeCategory.UNUSED_IMPORT,
    "org.eclipse.jdt.core.compiler.problem.unusedLocal": DeadCodeCategory.UNUSED_VARIABLE,
    "org.eclipse.jdt.core.compiler.problem.unusedPrivateMember": DeadCodeCategory.UNKNOWN,
    "org.eclipse.jdt.core.compiler.problem.deadCode": DeadCodeCategory.DEAD_CODE,
    # ESLint (JavaScript/TypeScript)
    "no-unused-vars": DeadCodeCategory.UNUSED_VARIABLE,
    "unused-imports/no-unused-imports": DeadCodeCategory.UNUSED_IMPORT,
    "@typescript-eslint/no-unused-vars": DeadCodeCategory.UNUSED_VARIABLE,
}

# Keywords that indicate unused/dead code in diagnostic messages
DIAGNOSTIC_MESSAGE_KEYWORDS: dict[str, DeadCodeCategory] = {
    "not accessed": DeadCodeCategory.UNUSED_VARIABLE,
    "not read": DeadCodeCategory.UNUSED_VARIABLE,
    "never read": DeadCodeCategory.UNUSED_VARIABLE,
    "is declared but": DeadCodeCategory.UNUSED_VARIABLE,
    "is never used": DeadCodeCategory.UNUSED_VARIABLE,
    "is unused": DeadCodeCategory.UNUSED_VARIABLE,
    "unused import": DeadCodeCategory.UNUSED_IMPORT,
    "imported but": DeadCodeCategory.UNUSED_IMPORT,
    "not used": DeadCodeCategory.UNUSED_VARIABLE,
    "dead code": DeadCodeCategory.DEAD_CODE,
    "unreachable": DeadCodeCategory.UNREACHABLE_CODE,
    "never executed": DeadCodeCategory.UNREACHABLE_CODE,
}


@dataclass
class FileDiagnostic:
    """A diagnostic associated with its source file path."""

    file_path: str
    diagnostic: LSPDiagnostic


class LSPDiagnosticsCollector:
    """Collects and categorizes LSP diagnostics for dead code detection."""

    def __init__(self):
        self.diagnostics: list[FileDiagnostic] = []
        self.issues: list[DiagnosticIssue] = []

    def add_diagnostic(self, file_path: str, diagnostic: LSPDiagnostic) -> None:
        self.diagnostics.append(FileDiagnostic(file_path=file_path, diagnostic=diagnostic))

    def process_diagnostics(self) -> list[DiagnosticIssue]:
        """Process all collected diagnostics and convert to issues."""
        self.issues = []
        skipped = 0
        seen_issues: set[tuple[str, str, int]] = set()  # (file_path, code, line_start)

        for item in self.diagnostics:
            issue = self._convert_to_issue(item.file_path, item.diagnostic)
            if issue:
                # Deduplicate: same file, code, and line
                key = (issue.file_path, issue.code or "", issue.line_start)
                if key not in seen_issues:
                    seen_issues.add(key)
                    self.issues.append(issue)
                else:
                    skipped += 1
                    logger.debug(f"Skipping duplicate issue: {key}")
            else:
                skipped += 1

        # Log summary
        category_counts: dict[str, int] = {}
        for issue in self.issues:
            cat = issue.category.value
            category_counts[cat] = category_counts.get(cat, 0) + 1

        logger.info(
            f"Processed {len(self.diagnostics)} diagnostics: {len(self.issues)} recognized as unused code issues, {skipped} skipped"
        )
        logger.info(f"Issues by category: {category_counts}")

        return self.issues

    def _convert_to_issue(self, file_path: str, diagnostic: LSPDiagnostic) -> DiagnosticIssue | None:
        """Convert a single LSP diagnostic to a DiagnosticIssue."""
        code = diagnostic.code
        message = diagnostic.message
        tags = diagnostic.tags
        severity = self._map_severity(diagnostic.severity)

        # Debug logging
        logger.debug(f"Processing diagnostic: code={code!r}, message={message[:50]!r}, tags={tags}")

        # Determine category from code or message
        category = self._categorize_diagnostic(code, message, tags)

        if category == DeadCodeCategory.UNKNOWN:
            # Skip if we can't categorize it as a dead code issue
            logger.debug(f"Skipping diagnostic (unknown category): code={code!r}, message={message[:50]!r}")
            return None

        logger.debug(f"Categorized diagnostic: code={code!r} -> category={category.value}")

        return DiagnosticIssue(
            file_path=file_path,
            line_start=diagnostic.range.start.line,
            line_end=diagnostic.range.end.line,
            message=message,
            code=code if code else None,
            category=category,
            severity=severity,
        )

    def _categorize_diagnostic(self, code: str, message: str, tags: list[int]) -> DeadCodeCategory:
        """Categorize a diagnostic based on code, message, and tags."""
        # Check diagnostic code first (most specific)
        if code in DIAGNOSTIC_CODE_MAPPINGS:
            return DIAGNOSTIC_CODE_MAPPINGS[code]

        # Check if code contains any known patterns
        for key, category in DIAGNOSTIC_CODE_MAPPINGS.items():
            if key.lower() in code.lower():
                return category

        # Check for Unnecessary tag (indicates unused code)
        if DIAGNOSTIC_TAG_UNNECESSARY in tags:
            # Try to determine what kind from message
            category = self._categorize_by_message(message)
            if category != DeadCodeCategory.UNKNOWN:
                return category
            return DeadCodeCategory.UNUSED_VARIABLE  # Default for unnecessary

        # Check message for keywords as last resort
        return self._categorize_by_message(message)

    def _categorize_by_message(self, message: str) -> DeadCodeCategory:
        """Categorize based on message content."""
        message_lower = message.lower()
        for keyword, category in DIAGNOSTIC_MESSAGE_KEYWORDS.items():
            if keyword.lower() in message_lower:
                return category
        return DeadCodeCategory.UNKNOWN

    def _map_severity(self, lsp_severity: int) -> Severity:
        """Map LSP severity (1-4) to our Severity enum."""
        # LSP severity: 1=Error, 2=Warning, 3=Info, 4=Hint
        if lsp_severity == 1:
            return Severity.CRITICAL
        elif lsp_severity == 2:
            return Severity.WARNING
        else:
            return Severity.INFO

    def get_issues_by_category(self) -> dict[DeadCodeCategory, list[DiagnosticIssue]]:
        """Group issues by category."""
        result: dict[DeadCodeCategory, list[DiagnosticIssue]] = {}
        for issue in self.issues:
            if issue.category not in result:
                result[issue.category] = []
            result[issue.category].append(issue)
        return result


def check_unused_code_diagnostics(
    diagnostics_collector: LSPDiagnosticsCollector,
    config: HealthCheckConfig | None = None,
) -> StandardCheckSummary:
    """Run dead/unused code detection based on LSP diagnostics.

    Args:
        diagnostics_collector: Collector with LSP diagnostics
        config: Optional health check configuration

    Returns:
        StandardCheckSummary with findings grouped by category
    """
    # Process all diagnostics
    issues = diagnostics_collector.process_diagnostics()
    issues_by_category = diagnostics_collector.get_issues_by_category()

    # Build finding groups
    finding_groups: list[FindingGroup] = []
    total_entities_checked = 0
    warning_count = 0

    for category, category_issues in issues_by_category.items():
        if not category_issues:
            continue

        # Create entities for this category
        entities: list[FindingEntity] = []
        for issue in category_issues:
            # Create a descriptive entity name from the message
            entity_name = f"[{category.value}] {issue.message[:50]}"
            if len(issue.message) > 50:
                entity_name += "..."

            entity = FindingEntity(
                entity_name=entity_name,
                file_path=issue.file_path,
                line_start=issue.line_start,
                line_end=issue.line_end,
                metric_value=0.0,  # Not applicable for diagnostics
            )
            entities.append(entity)

        # Determine severity based on issues in this category
        category_severity = max((issue.severity for issue in category_issues), default=Severity.INFO)

        # Create finding group
        group = FindingGroup(
            severity=category_severity,
            threshold=0,
            description=get_category_description(category),
            entities=entities,
        )
        finding_groups.append(group)

        total_entities_checked += len(category_issues)
        if category_severity == Severity.WARNING:
            warning_count += len(category_issues)

    # Calculate score
    if total_entities_checked == 0:
        score = 1.0
    else:
        # Score based on number of issues - more issues = lower score
        # Each issue reduces score by 0.05, minimum 0.0
        score = max(0.0, 1.0 - (len(issues) * 0.05))

    return StandardCheckSummary(
        check_name="unused_code_diagnostics",
        description="Detects unused imports, variables, functions, and dead code via LSP diagnostics",
        total_entities_checked=total_entities_checked,
        findings_count=len(issues),
        warning_count=warning_count,
        score=score,
        finding_groups=finding_groups,
    )


def get_category_description(category: DeadCodeCategory) -> str:
    """Get a human-readable description for a dead code category."""
    descriptions: dict[DeadCodeCategory, str] = {
        DeadCodeCategory.UNUSED_IMPORT: "Unused import statements that should be removed",
        DeadCodeCategory.UNUSED_VARIABLE: "Variables declared but never used",
        DeadCodeCategory.UNUSED_FUNCTION: "Functions/methods defined but never called",
        DeadCodeCategory.UNUSED_CLASS: "Classes defined but never instantiated",
        DeadCodeCategory.UNUSED_PARAMETER: "Function parameters that are not used",
        DeadCodeCategory.DEAD_CODE: "Code that will never be executed",
        DeadCodeCategory.UNREACHABLE_CODE: "Code that cannot be reached during execution",
        DeadCodeCategory.UNKNOWN: "Potentially unused or dead code",
    }
    return descriptions.get(category, f"Issues of type: {category.value}")
