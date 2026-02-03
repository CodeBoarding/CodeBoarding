# Integration Tests for Static Analysis Consistency

## Quick Start

```bash
# Step 1: Install LSP servers and setup configuration
python install.py

# Verify all LSP servers are properly installed
python scripts/check_lsp_servers.py

# Step 2: Generate fixtures for languages with working LSP servers
uv run python scripts/generate_integration_fixtures.py --repo codeboarding

# Step 3: Run integration tests
uv run pytest -m integration -v

# Alternative: Generate all fixtures at once (requires all LSP servers)
# uv run python scripts/generate_integration_fixtures.py --all
```

## Overview

Integration tests verify that the static analysis pipeline produces **consistent and reproducible results** across multiple programming languages. These tests clone real repositories at pinned commits, run static analysis, and compare the results against fixture files containing expected metrics.

### Purpose

- **Regression Detection**: Catch unintended changes in analysis metrics that might indicate bugs or regressions
- **Language Support Validation**: Ensure each language's LSP integration works correctly
- **Reproducibility Verification**: Confirm that analysis produces identical results across different environments and time
- **Performance Baseline**: Establish expected metrics as a baseline for monitoring

### Key Characteristics

- **Slow**: These tests clone and analyze real repositories, so they take 2-5 minutes per repository
- **Deterministic**: Fixed at pinned commits to ensure reproducible results
- **Non-blocking**: Do NOT run on every commit; only on manual trigger, merge to main, or scheduled runs
- **Granular**: Each language can be tested independently

## Supported Languages

| Language | Repository | Pinned Version | Status |
|----------|------------|---|--------|
| Python | [CodeBoarding](https://github.com/CodeBoarding/CodeBoarding) | `03b25afe8d37ce733e5f70c3cbcdfb52f4883dcd` | ✅ Generated |
| Java | [Mockito](https://github.com/mockito/mockito) | `v5.14.2` | ⏳ Placeholder |
| Go | [Prometheus](https://github.com/prometheus/prometheus) | `v3.0.1` | ⏳ Placeholder |
| TypeScript | [Excalidraw](https://github.com/excalidraw/excalidraw) | `v0.18.0` | ⏳ Placeholder |
| PHP | [WordPress](https://github.com/WordPress/WordPress) | `6.7` | ⏳ Placeholder |
| JavaScript | [Lodash](https://github.com/lodash/lodash) | `4.17.21` | ⏳ Placeholder |

## Running Integration Tests

### Prerequisites

**Important**: LSP servers must be installed before running integration tests:

```bash
# Install all required LSP servers
python install.py
```

This installs:
- **Pyright** - Python language server
- **TypeScript Language Server** - TypeScript and JavaScript
- **gopls** - Go language server
- **JDTLS** - Java language server
- **Intelephense** - PHP language server

### Run All Integration Tests

```bash
uv run pytest -m integration -v
```

### Run Tests for a Specific Language

```bash
# Python only
uv run pytest -m "integration and python_lang" -v

# Java only
uv run pytest -m "integration and java_lang" -v

# Go only
uv run pytest -m "integration and go_lang" -v

# TypeScript only
uv run pytest -m "integration and typescript_lang" -v

# PHP only
uv run pytest -m "integration and php_lang" -v

# JavaScript only
uv run pytest -m "integration and javascript_lang" -v
```

### Run Tests for Multiple Languages

```bash
# All JVM languages (Java)
uv run pytest -m "integration and java_lang" -v

# All dynamically-typed languages (Python, PHP, JavaScript)
uv run pytest -m "integration and (python_lang or php_lang or javascript_lang)" -v
```

### Exclude Integration Tests from Regular Test Runs

```bash
# Run all tests EXCEPT integration tests
uv run pytest -m "not integration"

# Run with coverage (integration tests are skipped)
uv run pytest -m "not integration" --cov=. --cov-report=term --cov-fail-under=80
```

### Run with Verbose Output

```bash
uv run pytest -m integration -v --tb=long
```

## Generating Fixtures

Integration test fixtures contain expected metrics for a specific repository and language at a pinned commit. Fixtures are JSON files stored in `tests/integration/fixtures/`.

### Prerequisites for Fixture Generation

Before generating fixtures, ensure all required LSP servers are installed and configured:

```bash
# Run the setup script to install all LSP servers
python install.py

# Verify servers are installed
which pyright-langserver      # Python
which typescript-language-server  # TypeScript/JavaScript
which gopls                   # Go
which intelephense           # PHP
java -version                # Java (check version >= 17)

# For Java, verify JDTLS is downloaded
ls static_analyzer/servers/jdtls/bin/jdtls.py
```

The `static_analysis_config.yml` file defines where each language server is located. Make sure paths are correct for your environment before generating fixtures.

### Fixture Format

Each fixture file contains:

```json
{
  "metadata": {
    "repo_url": "https://github.com/...",
    "pinned_commit": "abc123...",
    "language": "Python",
    "generated_at": "2026-02-03T...",
    "codeboarding_version": "0.2.0"
  },
  "metrics": {
    "references_count": 2559,
    "classes_count": 111,
    "packages_count": 20,
    "call_graph_nodes": 2563,
    "call_graph_edges": 902,
    "source_files_count": 80
  },
  "sample_references": ["...", "..."],
  "sample_classes": ["...", "..."]
}
```

### Measured Metrics

- **references_count**: Total number of source code symbols (functions, classes, variables, etc.)
- **classes_count**: Number of class definitions
- **packages_count**: Number of packages/modules
- **call_graph_nodes**: Number of nodes in the call graph (callable entities)
- **call_graph_edges**: Number of edges in the call graph (call relationships)
- **source_files_count**: Number of source files analyzed

### Generate a Single Fixture

Ensure LSP servers are installed first (see Prerequisites above).

```bash
# Generate fixture for CodeBoarding Python
uv run python scripts/generate_integration_fixtures.py --repo codeboarding

# Generate fixture for Mockito Java
uv run python scripts/generate_integration_fixtures.py --repo mockito

# Generate fixture for any repository (partial match)
uv run python scripts/generate_integration_fixtures.py --repo prometheus
uv run python scripts/generate_integration_fixtures.py --repo excalidraw
uv run python scripts/generate_integration_fixtures.py --repo wordpress
uv run python scripts/generate_integration_fixtures.py --repo lodash
```

If a language server is not found, you'll see an error like:
```
Error: Failed to start LSP server for <language>: ...
```

In this case, run `python install.py` to install missing servers.

### Generate All Fixtures

```bash
uv run python scripts/generate_integration_fixtures.py --all
```

This will clone each repository at its pinned commit, run static analysis, and save the metrics to fixture files. **This takes 20-30 minutes** as it processes all supported languages.

### Generate Fixtures Quietly

Suppress progress messages:

```bash
uv run python scripts/generate_integration_fixtures.py --all --quiet
```

### List Available Repositories

```bash
uv run python scripts/generate_integration_fixtures.py --list
```

Output:
```
Available repositories:
  codeboarding_python: https://github.com/CodeBoarding/CodeBoarding (Python)
  mockito_java: https://github.com/mockito/mockito (Java)
  prometheus_go: https://github.com/prometheus/prometheus (Go)
  excalidraw_typescript: https://github.com/excalidraw/excalidraw (TypeScript)
  wordpress_php: https://github.com/WordPress/WordPress (PHP)
  lodash_javascript: https://github.com/lodash/lodash (JavaScript)
```

## How Tests Work

### Test Execution Flow

1. **Clone Repository**: Uses `clone_repository()` to clone the test repository
2. **Checkout Pinned Commit**: Checks out the exact commit specified in the configuration
3. **Clear Cache**: Uses a fresh temporary directory for cache to ensure clean analysis
4. **Run Static Analysis**: Calls `get_static_analysis()` with mocked language detection (ProjectScanner.scan)
5. **Extract Metrics**: Counts nodes, edges, classes, packages, references, and files
6. **Compare Against Fixture**: Verifies metrics match fixture values exactly (0 tolerance)
7. **Verify Sample Entities**: Ensures known entities (sample references and classes) are present

### Why Mock ProjectScanner?

The tests mock `ProjectScanner.scan()` (which uses the Tokei binary) to:

- **Avoid external dependencies**: CI environments might not have Tokei installed
- **Control language detection**: Ensures exactly the expected languages are analyzed
- **Test real analysis**: LSP servers still run, testing actual language analysis

The real LSP clients and analysis logic are fully tested with this approach.

## Fixture Maintenance

### When to Regenerate Fixtures

Fixtures should be regenerated when:

1. **Code Changes to Static Analysis**: If you modify how metrics are calculated
2. **LSP Server Updates**: When upgrading language servers might change analysis results
3. **Baseline Refresh**: Periodically (e.g., monthly) to pick up new code in pinned repositories

### How to Update a Single Fixture

```bash
# Update the CodeBoarding fixture
uv run python scripts/generate_integration_fixtures.py --repo codeboarding
```

This will:
1. Clone the repository at the pinned commit
2. Run static analysis
3. Save new metrics to the existing fixture file (overwriting it)

### Pinned Commits

All repositories are pinned to specific commits/versions for reproducibility:

- **CodeBoarding**: Existing commit from health test
- **Mockito**: Latest stable release (v5.14.2)
- **Prometheus**: Latest stable release (v3.0.1)
- **Excalidraw**: Latest stable release (v0.18.0)
- **WordPress**: Latest stable release (6.7)
- **Lodash**: Latest stable release (4.17.21)

To update a pinned commit, edit `tests/integration/conftest.py` in the `RepositoryTestConfig` for that repository.

## GitHub Actions Integration

### Workflow: `integration-tests.yml`

Located at `.github/workflows/integration-tests.yml`, this workflow runs integration tests on:

1. **Manual Trigger** (`workflow_dispatch`):
   - Via GitHub Actions UI
   - Optional language filter dropdown

2. **Merge to Main** (`push` to `main`):
   - Runs automatically after merges
   - Tests all languages
   - 60-minute timeout

3. **Scheduled** (`schedule`):
   - Weekly run (Sunday 3 AM UTC)
   - Catches regressions
   - Tests all languages

### Available Workflow Inputs

When triggering manually:

- **language**: Optional language filter
  - Leave empty for all languages
  - Options: `python`, `java`, `go`, `typescript`, `php`, `javascript`

### Workflow Steps

1. Checkout code
2. Set up Python 3.12
3. Install dependencies
4. Setup all LSP servers:
   - Pyright (Python)
   - TypeScript Language Server
   - gopls (Go)
   - JDTLS (Java)
   - Intelephense (PHP)
5. Run tests (optionally filtered by language)
6. Upload test artifacts

## Important Notes on LSP Server Setup

### How LSP Servers are Located

When you run `python install.py`:

1. **Downloads/installs binaries**:
   - Node.js servers (Pyright, TypeScript LSP, Intelephense) → `static_analyzer/servers/node_modules/.bin/`
   - Go binary (gopls) → `static_analyzer/servers/gopls` (macOS) or `static_analyzer/servers/bin/macos/gopls`
   - JDTLS → `static_analyzer/servers/jdtls/`
   - Tokei → `static_analyzer/servers/tokei` (macOS) or `static_analyzer/servers/bin/macos/tokei`

2. **Updates `static_analysis_config.yml`**:
   - Replaces relative paths with absolute paths on your machine
   - Configures LSP clients to find the binaries

3. **Absolute paths are machine-specific**:
   - These paths won't work in CI/CD or on other machines
   - In GitHub Actions, `install.py` runs again to update paths for the CI environment

### Checking Server Status

Always run this after `install.py` to verify servers are working:

```bash
python scripts/check_lsp_servers.py
```

This checks:
- Python (Pyright) ✅
- TypeScript/JavaScript ✅
- Go (gopls) ✅
- PHP (Intelephense) ✅
- Java (JDTLS) ✅

## Troubleshooting

### Fixture Generated with All Zeros (No Metrics)

**Problem**: Fixture file shows all metrics as 0
```json
{
  "metrics": {
    "references_count": 0,
    "classes_count": 0,
    ...
  }
}
```

**Cause**: LSP server for that language failed to start or is not properly installed.

**Solution**: Test each LSP server individually and reinstall if needed:

```bash
# For Python
echo "Testing Python LSP..."
/Users/svilen/Documents/Projects/CodeBoarding/static_analyzer/servers/node_modules/.bin/pyright-langserver --version

# For TypeScript/JavaScript
echo "Testing TypeScript LSP..."
/Users/svilen/Documents/Projects/CodeBoarding/static_analyzer/servers/node_modules/.bin/typescript-language-server --version

# For Go
echo "Testing Go LSP..."
/Users/svilen/Documents/Projects/CodeBoarding/static_analyzer/servers/gopls version

# For Java
echo "Testing Java (required for JDTLS)..."
java -version
ls -la /Users/svilen/Documents/Projects/CodeBoarding/static_analyzer/servers/jdtls/bin/

# For PHP
echo "Testing PHP LSP..."
/Users/svilen/Documents/Projects/CodeBoarding/static_analyzer/servers/node_modules/.bin/intelephense --version
```

If any test fails, the LSP server needs to be reinstalled:
```bash
python install.py
```

### LSP Server Not Found or Connection Error

**Error**: `Failed to start LSP server for <language>: ...` or `FileNotFoundError: [Errno 2] No such file or directory: ...`

**Cause**: LSP servers are not installed or `static_analysis_config.yml` paths are incorrect.

**Solution**: Verify and fix LSP server setup:

```bash
# First, verify static_analysis_config.yml has correct paths
cat static_analysis_config.yml | grep -A 2 "command:"

# Expected paths (relative to project root):
# - Python: static_analyzer/servers/node_modules/.bin/pyright-langserver
# - TypeScript/JS: static_analyzer/servers/node_modules/.bin/typescript-language-server
# - PHP: static_analyzer/servers/node_modules/.bin/intelephense
# - Go: static_analyzer/servers/gopls OR ($(which gopls))
# - Java: JDTLS_ROOT env var pointing to static_analyzer/servers/jdtls

# If paths are wrong or servers missing, reinstall:
python install.py

# After reinstalling, verify install.py output says "Configuration update finished: success"
```

### Test Fails with "Fixture Not Found"

**Error**: `FileNotFoundError: [Errno 2] No such file or directory: '.../fixtures/...json'`

**Solution**: Generate the fixture:
```bash
# First ensure LSP servers are installed
python install.py

# Then generate the fixture
uv run python scripts/generate_integration_fixtures.py --repo <name>
```

### Test Fails with "Metrics Mismatch"

**Error**: `AssertionError: references_count: expected 2559, got 2600 (diff: 41)`

**Possible Causes**:
1. LSP server behavior changed
2. Repository content changed (but it shouldn't - it's pinned)
3. Bug in static analysis code

**Solution**:
1. Verify the pinned commit hasn't moved
2. Check if LSP servers were recently updated
3. If intentional, regenerate fixture: `uv run python scripts/generate_integration_fixtures.py --repo <name>`

### Test Fails with "Language Not in Results"

**Error**: `AssertionError: Expected language 'Java' not in results. Found: [...]`

**Possible Causes**:
1. LSP server for that language is not installed
2. LSP server crashed during analysis

**Solution**:
1. Verify the language server is installed: check `.github/workflows/integration-tests.yml`
2. Run test locally with verbose output: `uv run pytest -m "integration and java_lang" -vv --tb=long`
3. Check LSP server logs for errors

### Test Timeout

**Error**: `TimeoutError` or test hangs

**Possible Causes**:
1. Repository is very large or network is slow
2. LSP server crashed and test is waiting

**Solution**:
1. Increase timeout in `.github/workflows/integration-tests.yml` (currently 60 minutes)
2. Run locally with shorter timeout: `uv run pytest -m integration --timeout=300`
3. Check for hung processes: `ps aux | grep -E '(jdtls|gopls|node|php)'`

## Design Decisions

### Single Parameterized Test File

All tests are in `test_static_analysis_consistency.py` to:
- Reduce code duplication
- Ensure consistent test methodology
- Make test configuration easy to maintain

### One Language per Repository

Each repository tests a single language to:
- Have cleaner fixture files
- Make failures easier to diagnose
- Allow independent testing

### Exact Matching (0 Tolerance)

Metrics are compared with exact matching (no tolerance) because:
- Pinned commits ensure reproducible analysis
- Any difference indicates a real problem
- Different from health checks (which use tolerances for LSP non-determinism)

### Stable Release Tags for Pinned Commits

Uses official releases (not main branch commits) to:
- Ensure commits don't disappear
- Make fixtures stable over time
- Allow developers to understand code context

## Best Practices

### Running Locally

```bash
# Run tests before pushing
uv run pytest -m "not integration"  # Quick: ~30 seconds

# Run integration test for a language you modified
uv run pytest -m "integration and python_lang" -v

# Run full suite before creating PR
uv run pytest -m "not integration" --cov=. --cov-fail-under=80
```

### Updating Fixtures After Code Changes

If you modify static analysis code:

```bash
# Regenerate all fixtures
uv run python scripts/generate_integration_fixtures.py --all

# Verify tests pass
uv run pytest -m integration

# Commit fixture updates with your code changes
git add tests/integration/fixtures/
git commit -m "Update integration test fixtures after static analysis changes"
```

### Monitoring in CI

Monitor the integration test workflow in GitHub Actions:
1. Go to **Actions** tab
2. Select **Integration Tests** workflow
3. Check recent runs for failures

## Files Reference

- **Tests**: [tests/integration/test_static_analysis_consistency.py](tests/integration/test_static_analysis_consistency.py)
- **Configuration**: [tests/integration/conftest.py](tests/integration/conftest.py)
- **Fixtures**: [tests/integration/fixtures/](tests/integration/fixtures/)
- **Generator**: [scripts/generate_integration_fixtures.py](scripts/generate_integration_fixtures.py)
- **Workflow**: [.github/workflows/integration-tests.yml](.github/workflows/integration-tests.yml)
- **pytest Config**: [pyproject.toml](pyproject.toml) (markers section)

## Additional Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest Markers](https://docs.pytest.org/en/stable/how-to/mark.html)
- [GitHub Actions Workflows](https://docs.github.com/en/actions/using-workflows)
- [CodeBoarding Static Analysis](./static_analyzer/)
