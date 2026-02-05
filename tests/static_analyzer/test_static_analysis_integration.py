from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from static_analyzer import StaticAnalyzer


PYRIGHT_LANGSERVER = Path("static_analyzer/servers/node_modules/.bin/pyright-langserver")


def _pyright_available() -> bool:
    return PYRIGHT_LANGSERVER.exists() and os.access(PYRIGHT_LANGSERVER, os.X_OK)


def test_static_analysis_python_e2e(tmp_path: Path) -> None:
    if not _pyright_available():
        pytest.skip("pyright-langserver not available")

    repo = tmp_path / "mini_repo"
    repo.mkdir()

    (repo / "a.py").write_text(
        "from b import bar, Baz\n\n"
        "DEFAULT_MULTIPLIER = 10\n\n\n"
        "def foo():\n"
        "    result = bar()\n"
        "    return result * DEFAULT_MULTIPLIER\n\n\n"
        "def process_items(items: list[int]) -> list[int]:\n"
        "    processor = Baz()\n"
        "    return [processor.transform(item) for item in items]\n\n\n"
        "def validate_input(value: int) -> bool:\n"
        "    return value > 0 and value < 1000\n",
        encoding="utf-8",
    )
    (repo / "b.py").write_text(
        "INITIAL_VALUE = 1\n\n\n"
        "def bar():\n"
        "    return compute_base() + INITIAL_VALUE\n\n\n"
        "def compute_base() -> int:\n"
        "    return 42\n\n\n"
        "class Baz:\n"
        "    def __init__(self):\n"
        "        self.factor = 2\n\n"
        "    def transform(self, value: int) -> int:\n"
        "        return value * self.factor\n\n"
        "    def reset(self) -> None:\n"
        "        self.factor = 2\n",
        encoding="utf-8",
    )

    results = StaticAnalyzer(repo).analyze()

    python_lang = next((lang for lang in results.get_languages() if lang.lower() == "python"), None)
    assert python_lang is not None

    files = {Path(p).name for p in results.get_source_files(python_lang)}
    assert {"a.py", "b.py"} <= files

    cfg = results.get_cfg(python_lang)
    assert "a.foo" in cfg.nodes
    assert "b.bar" in cfg.nodes
    assert any(edge.get_source() == "a.foo" and edge.get_destination() == "b.bar" for edge in cfg.edges)

    ref = results.get_reference(python_lang, "a.foo")
    assert Path(ref.file_path).name == "a.py"

    hierarchy = results.get_hierarchy(python_lang)
    assert "b.Baz" in hierarchy


def test_static_analysis_cross_file_links(tmp_path: Path) -> None:
    if not _pyright_available():
        pytest.skip("pyright-langserver not available")

    repo = tmp_path / "multi_file_repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)

    (pkg / "__init__.py").write_text(
        "from .entry import main\n\n" "__version__ = '1.0.0'\n" "__all__ = ['main']\n",
        encoding="utf-8",
    )
    (pkg / "util.py").write_text(
        "MAX_RETRIES = 3\n\n\n"
        "def helper(x: int) -> int:\n"
        "    return clamp(x + 1, 0, 100)\n\n\n"
        "def clamp(value: int, min_val: int, max_val: int) -> int:\n"
        "    if value < min_val:\n"
        "        return min_val\n"
        "    if value > max_val:\n"
        "        return max_val\n"
        "    return value\n\n\n"
        "def format_result(value: int) -> str:\n"
        "    return f'Result: {value}'\n",
        encoding="utf-8",
    )
    (pkg / "worker.py").write_text(
        "from .util import helper, MAX_RETRIES\n\n"
        "BATCH_SIZE = 10\n\n\n"
        "def run(value: int) -> int:\n"
        "    for attempt in range(MAX_RETRIES):\n"
        "        result = helper(value)\n"
        "        if result > 0:\n"
        "            return result\n"
        "    return 0\n\n\n"
        "def process_batch(items: list[int]) -> list[int]:\n"
        "    return [helper(item) for item in items[:BATCH_SIZE]]\n\n\n"
        "class WorkerConfig:\n"
        "    def __init__(self, timeout: int = 30):\n"
        "        self.timeout = timeout\n"
        "        self.retries = MAX_RETRIES\n",
        encoding="utf-8",
    )
    (pkg / "entry.py").write_text(
        "from .worker import run, WorkerConfig\n\n"
        "DEFAULT_INPUT = 41\n\n\n"
        "def main() -> int:\n"
        "    config = WorkerConfig(timeout=60)\n"
        "    return run(DEFAULT_INPUT)\n\n\n"
        "def run_with_config(value: int, config: WorkerConfig | None = None) -> int:\n"
        "    if config is None:\n"
        "        config = WorkerConfig()\n"
        "    return run(value)\n\n\n"
        "def cli() -> None:\n"
        "    result = main()\n"
        "    print(f'Execution completed with result: {result}')\n",
        encoding="utf-8",
    )

    results = StaticAnalyzer(repo).analyze()

    python_lang = next((lang for lang in results.get_languages() if lang.lower() == "python"), None)
    assert python_lang is not None

    cfg = results.get_cfg(python_lang)
    assert "pkg.util.helper" in cfg.nodes
    assert "pkg.worker.run" in cfg.nodes
    assert "pkg.entry.main" in cfg.nodes
    assert any(
        edge.get_source() == "pkg.worker.run" and edge.get_destination() == "pkg.util.helper" for edge in cfg.edges
    )
    assert any(
        edge.get_source() == "pkg.entry.main" and edge.get_destination() == "pkg.worker.run" for edge in cfg.edges
    )


def test_static_analysis_nested_structure(tmp_path: Path) -> None:
    if not _pyright_available():
        pytest.skip("pyright-langserver not available")

    repo = tmp_path / "nested_repo"
    top_pkg = repo / "top"
    nested_pkg = top_pkg / "nested"
    nested_pkg.mkdir(parents=True)

    (top_pkg / "__init__.py").write_text(
        "from .alpha import alpha\n\n" "__all__ = ['alpha']\n",
        encoding="utf-8",
    )
    (nested_pkg / "__init__.py").write_text(
        "from .beta import beta\n" "from .gamma import GammaProcessor\n\n" "__all__ = ['beta', 'GammaProcessor']\n",
        encoding="utf-8",
    )
    (top_pkg / "alpha.py").write_text(
        "from .nested.beta import beta\n"
        "from .nested.gamma import GammaProcessor\n\n"
        "SCALE_FACTOR = 100\n\n\n"
        "def alpha() -> int:\n"
        "    base = beta()\n"
        "    return base * SCALE_FACTOR\n\n\n"
        "def process_with_gamma(values: list[int]) -> list[int]:\n"
        "    processor = GammaProcessor()\n"
        "    return processor.transform_all(values)\n\n\n"
        "class AlphaCoordinator:\n"
        "    def __init__(self):\n"
        "        self.processor = GammaProcessor()\n\n"
        "    def run(self) -> int:\n"
        "        return alpha()\n",
        encoding="utf-8",
    )
    (nested_pkg / "beta.py").write_text(
        "from .gamma import compute_seed\n\n"
        "BASE_VALUE = 7\n\n\n"
        "def beta() -> int:\n"
        "    seed = compute_seed()\n"
        "    return BASE_VALUE + seed\n\n\n"
        "def beta_squared() -> int:\n"
        "    value = beta()\n"
        "    return value * value\n\n\n"
        "class BetaConfig:\n"
        "    def __init__(self, multiplier: int = 1):\n"
        "        self.multiplier = multiplier\n"
        "        self.base = BASE_VALUE\n",
        encoding="utf-8",
    )
    (nested_pkg / "gamma.py").write_text(
        "SEED_CONSTANT = 3\n\n\n"
        "def compute_seed() -> int:\n"
        "    return SEED_CONSTANT * 2\n\n\n"
        "def validate_value(value: int) -> bool:\n"
        "    return 0 <= value <= 1000\n\n\n"
        "class GammaProcessor:\n"
        "    def __init__(self, factor: int = 2):\n"
        "        self.factor = factor\n\n"
        "    def transform(self, value: int) -> int:\n"
        "        if not validate_value(value):\n"
        "            return 0\n"
        "        return value * self.factor\n\n"
        "    def transform_all(self, values: list[int]) -> list[int]:\n"
        "        return [self.transform(v) for v in values]\n",
        encoding="utf-8",
    )

    results = StaticAnalyzer(repo).analyze()

    python_lang = next((lang for lang in results.get_languages() if lang.lower() == "python"), None)
    assert python_lang is not None

    files = {Path(p).relative_to(repo).as_posix() for p in results.get_source_files(python_lang)}
    assert "top/alpha.py" in files
    assert "top/nested/beta.py" in files

    cfg = results.get_cfg(python_lang)
    assert "top.alpha.alpha" in cfg.nodes
    assert "top.nested.beta.beta" in cfg.nodes
    assert any(
        edge.get_source() == "top.alpha.alpha" and edge.get_destination() == "top.nested.beta.beta"
        for edge in cfg.edges
    )


CODEBOARDING_REPO = "https://github.com/CodeBoarding/CodeBoarding.git"
CODEBOARDING_COMMIT = "cc054a8"

# Expected minimum counts for regression detection at commit cc054a8 (produced by and from this commit)
# These are the exact counts from analyzing the repo at this commit
EXPECTED_MIN_NODES = 2448
EXPECTED_MIN_EDGES = 956

# Key nodes that MUST be detected - representative symbols across core modules
EXPECTED_NODES = {
    # Static analyzer core
    "static_analyzer.__init__.StaticAnalyzer",
    "static_analyzer.__init__.analyze",
    "static_analyzer.analysis_result.StaticAnalysisResults",
    "static_analyzer.analysis_result.get_cfg",
    "static_analyzer.analysis_result.get_reference",
    "static_analyzer.graph.CallGraph",
    "static_analyzer.graph.Edge",
    "static_analyzer.graph.Node",
    "static_analyzer.graph.add_edge",
    "static_analyzer.graph.add_node",
    "static_analyzer.lsp_client.client.LSPClient",
    "static_analyzer.lsp_client.client.build_static_analysis",
    "static_analyzer.lsp_client.typescript_client.TypeScriptClient",
    "static_analyzer.scanner.ProjectScanner",
    "static_analyzer.scanner.scan",
    "static_analyzer.programming_language.ProgrammingLanguage",
    # Agents
    "agents.agent.CodeBoardingAgent",
    "agents.agent.LargeModelAgent",
    "agents.abstraction_agent.AbstractionAgent",
    "agents.details_agent.DetailsAgent",
    "agents.validator_agent.ValidatorAgent",
    "agents.meta_agent.MetaAgent",
    "agents.planner_agent.PlannerAgent",
    "agents.llm_config.LLMConfig",
    "agents.tools.toolkit.CodeBoardingToolkit",
    # Diagram analysis
    "diagram_analysis.diagram_generator.DiagramGenerator",
    "diagram_analysis.diagram_generator.generate_analysis",
    # Main entry point
    "main.main",
    "main.process_local_repository",
    "main.process_remote_repository",
    # Output generators
    "output_generators.markdown.generate_markdown",
    "output_generators.html.generate_html",
    "output_generators.mdx.generate_mdx",
    "output_generators.sphinx.generate_rst",
    # Monitoring
    "monitoring.callbacks.MonitoringCallback",
    "monitoring.context.MonitorContext",
    "monitoring.writers.StreamingStatsWriter",
    # Repo utils
    "repo_utils.__init__.clone_repository",
    "repo_utils.ignore.RepoIgnoreManager",
    "repo_utils.git_diff.FileChange",
}

# Key edges (call relationships) that MUST be detected
EXPECTED_EDGES = {
    # StaticAnalyzer flow
    ("static_analyzer.__init__.StaticAnalyzer", "static_analyzer.__init__.__init__"),
    ("static_analyzer.__init__.StaticAnalyzer", "static_analyzer.__init__.analyze"),
    ("static_analyzer.__init__.__init__", "static_analyzer.__init__.create_clients"),
    # CallGraph operations
    ("static_analyzer.graph.CallGraph", "static_analyzer.graph.add_edge"),
    ("static_analyzer.graph.CallGraph", "static_analyzer.graph.add_node"),
    ("static_analyzer.graph.add_edge", "static_analyzer.graph.Edge"),
    # LSPClient flow
    ("static_analyzer.lsp_client.client.LSPClient", "static_analyzer.lsp_client.client.build_static_analysis"),
    ("static_analyzer.lsp_client.client.LSPClient", "static_analyzer.lsp_client.client.start"),
    (
        "static_analyzer.lsp_client.client.build_static_analysis",
        "static_analyzer.lsp_client.client._analyze_single_file",
    ),
    # Agent hierarchy
    ("agents.abstraction_agent.AbstractionAgent", "agents.abstraction_agent.run"),
    ("agents.details_agent.DetailsAgent", "agents.details_agent.run"),
    ("agents.validator_agent.ValidatorAgent", "agents.validator_agent.run"),
    # DiagramGenerator flow
    ("diagram_analysis.diagram_generator.DiagramGenerator", "diagram_analysis.diagram_generator.generate_analysis"),
    ("diagram_analysis.diagram_generator.generate_analysis", "diagram_analysis.diagram_generator.pre_analysis"),
    # Main entry flow
    ("main.main", "main.process_local_repository"),
    ("main.main", "main.process_remote_repository"),
    ("main.main", "logging_config.setup_logging"),
    ("main.process_local_repository", "main.generate_analysis"),
    ("main.generate_analysis", "diagram_analysis.diagram_generator.DiagramGenerator"),
    # Output generation
    ("output_generators.markdown.generate_markdown_file", "output_generators.markdown.generate_markdown"),
    ("output_generators.html.generate_html_file", "output_generators.html.generate_html"),
    # Monitoring
    ("monitoring.writers.StreamingStatsWriter", "monitoring.writers.start"),
    ("monitoring.writers.StreamingStatsWriter", "monitoring.writers.stop"),
}


def _clone_codeboarding(target: Path) -> None:
    subprocess.run(
        ["git", "clone", "--depth", "100", CODEBOARDING_REPO, str(target)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", CODEBOARDING_COMMIT],
        cwd=target,
        check=True,
        capture_output=True,
    )


@pytest.mark.slow
def test_large_repo_determinism(tmp_path: Path) -> None:
    """Test that static analysis produces deterministic results on a large repo.

    This verifies that the LSP returns consistent results across multiple runs,
    ensuring the analysis is reliable and reproducible.
    """
    if not _pyright_available():
        pytest.skip("pyright-langserver not available")

    repo = tmp_path / "codeboarding"
    _clone_codeboarding(repo)

    node_sets: list[set[str]] = []
    edge_sets: list[set[tuple[str, str]]] = []

    for run_num in range(3):
        results = StaticAnalyzer(repo).analyze()

        python_lang = next((lang for lang in results.get_languages() if lang.lower() == "python"), None)
        if python_lang is None:
            pytest.fail(f"Run {run_num}: Python language not detected")

        cfg = results.get_cfg(python_lang)
        nodes = set(cfg.nodes)
        edges = {(e.get_source(), e.get_destination()) for e in cfg.edges}

        # Verify minimum symbols resolved on first run
        if run_num == 0:
            assert len(nodes) > 10, f"First run found only {len(nodes)} nodes, expected more than 10"

        node_sets.append(nodes)
        edge_sets.append(edges)

    # Verify determinism: all runs must produce exactly the same results
    for i in range(1, len(node_sets)):
        first_nodes = node_sets[0]
        curr_nodes = node_sets[i]

        missing = first_nodes - curr_nodes
        extra = curr_nodes - first_nodes

        assert first_nodes == curr_nodes, (
            f"Run {i} produced different nodes than run 0. "
            f"Missing: {sorted(list(missing))[:5]}, Extra: {sorted(list(extra))[:5]}"
        )

    for i in range(1, len(edge_sets)):
        first_edges = edge_sets[0]
        curr_edges = edge_sets[i]

        assert first_edges == curr_edges, (
            f"Run {i} produced different edges than run 0. "
            f"Missing: {len(first_edges - curr_edges)}, Extra: {len(curr_edges - first_edges)}"
        )


@pytest.mark.slow
def test_large_repo_expected_symbols(tmp_path: Path) -> None:
    """Test that static analysis produces expected symbols on a known codebase version.

    This regression test verifies that the static analyzer correctly detects
    key classes, functions, and call relationships in the CodeBoarding repo
    at commit cc054a8. It serves as a baseline to catch regressions where
    the analyzer might miss symbols or edges that were previously detected.
    """
    if not _pyright_available():
        pytest.skip("pyright-langserver not available")

    repo = tmp_path / "codeboarding"
    _clone_codeboarding(repo)

    results = StaticAnalyzer(repo).analyze()

    python_lang = next((lang for lang in results.get_languages() if lang.lower() == "python"), None)
    assert python_lang is not None, "Python language not detected"

    cfg = results.get_cfg(python_lang)
    nodes = set(cfg.nodes)
    edges = {(e.get_source(), e.get_destination()) for e in cfg.edges}

    # Verify minimum counts for regression detection
    assert len(nodes) >= EXPECTED_MIN_NODES, (
        f"Expected at least {EXPECTED_MIN_NODES} nodes, got {len(nodes)}. "
        "This may indicate a regression in symbol detection."
    )
    assert len(edges) >= EXPECTED_MIN_EDGES, (
        f"Expected at least {EXPECTED_MIN_EDGES} edges, got {len(edges)}. "
        "This may indicate a regression in call relationship detection."
    )

    # Verify key nodes are present
    missing_nodes = EXPECTED_NODES - nodes
    assert not missing_nodes, (
        f"Missing {len(missing_nodes)} expected nodes: {sorted(missing_nodes)[:10]}..."
        if len(missing_nodes) > 10
        else f"Missing expected nodes: {sorted(missing_nodes)}"
    )

    # Verify key edges are present
    missing_edges = EXPECTED_EDGES - edges
    assert not missing_edges, (
        f"Missing {len(missing_edges)} expected edges: {sorted(missing_edges)[:5]}..."
        if len(missing_edges) > 5
        else f"Missing expected edges: {sorted(missing_edges)}"
    )
