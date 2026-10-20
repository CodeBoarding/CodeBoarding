"""Exercise the deployed Lambda response through the model client, scope analysis and CLI."""

import logging
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agents.agent_responses import AnalysisInsights, Component
from agents.llm_errors import LLMQueryError
from agents.scope_analysis_agent import ScopeAnalysisAgent
from agents.tree_planner_agent import BUDGET, TreePlannerAgent
from codeboarding_cli.main import main
from diagram_analysis.diagram_generator import DiagramGenerator
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult
from static_analyzer.clustering.names import ROLE_WORDS, GroupingContext
from static_analyzer.clustering.names.frontier import BOX, Candidate
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


@pytest.mark.parametrize("stage,remote", [("scope", False), ("planner", True)])
def test_quota_logs_trace_then_guidance(tmp_path, capsys, caplog, stage, remote):
    error = {"type": "gha_proxy", "message": "Resource exhausted: token limit reached"}
    requests = []

    def lambda_response(request):
        requests.append(request)
        return httpx.Response(402, json={"error": error})

    graph = CallGraph(language="python")
    graph.add_node(Node("pkg.run", NodeType.FUNCTION, "pkg.py", 1, 3))
    static = StaticAnalysisResults()
    static.add_cfg(Language.PYTHON, graph)
    scope = ClusterScopeResult(
        scope_id="root",
        graphs_by_language={"python": graph},
        groups=[ClusterGroup(group_id="1", cluster_ids=[1], symbol_members_by_language={"python": {"pkg.run"}})],
    )
    analysis = AnalysisInsights(
        description="",
        components=[Component(name="Component 1", description="", key_entities=[], component_id="1")],
        components_relations=[],
    )
    generator = DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path / "temp",
        repo_name="test",
        output_dir=tmp_path / "out",
        depth_cap=2,
        run_id="test",
        log_path="test",
    )
    with httpx.Client(transport=httpx.MockTransport(lambda_response)) as client:
        model = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=SecretStr("test"),
            base_url="https://lambda.example/v1",
            http_client=client,
            max_retries=0,
        )
        generator.scope_analysis_agent = ScopeAnalysisAgent(Path("/repo"), static, model)
        planner = TreePlannerAgent(model, draws=1)
        candidates = [Candidate(f"box:Feature{i}", BOX, f"Feature{i}") for i in range(BUDGET + 1)]
        context = GroupingContext(
            "root", ROLE_WORDS, 300, "frontier", sizes={candidate.key: 30 for candidate in candidates}
        )

        def run(*args, **kwargs):
            if stage == "planner":
                return planner.group(candidates, context)
            return generator._enrich_scope(scope, analysis, {"1"})

        target = "_process_one_remote" if remote else "_run_local"
        with (
            caplog.at_level(logging.ERROR, logger="codeboarding_cli.main"),
            patch.object(Path, "cwd", return_value=tmp_path),
            patch("codeboarding_cli.commands.full_analysis.bootstrap_environment"),
            patch(f"codeboarding_cli.commands.full_analysis.{target}", side_effect=run),
            closing(logging.FileHandler(tmp_path / "quota.log")) as log_file,
            patch.object(logging.getLogger("codeboarding_cli.main"), "handlers", [log_file]),
            pytest.raises(SystemExit) as exit_info,
        ):
            main(
                ["full", "https://github.com/a/one", "https://github.com/a/two"]
                if remote
                else ["full", "--local", str(tmp_path)]
            )

    assert exit_info.value.code == 3
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/chat/completions"
    output = capsys.readouterr()
    assert not output.out
    assert isinstance(exit_info.value.__cause__, LLMQueryError)
    assert exit_info.value.__cause__.__cause__ is not None
    diagnostic = (tmp_path / "quota.log").read_text()
    assert "Traceback (most recent call last)" in diagnostic
    assert "LLMQueryError" in diagnostic
    assert error["message"] in diagnostic
    assert diagnostic.index("Traceback") < diagnostic.rindex("CodeBoarding: ")
    records = [record for record in caplog.records if record.name == "codeboarding_cli.main"]
    assert records[-2].exc_info is not None
    assert records[-1].exc_info is None
    assert records[-1].getMessage().startswith("CodeBoarding: ")
    assert "https://codeboarding.org/pricing" in records[-1].getMessage()
    assert "or wait for your weekly reset" in records[-1].getMessage()
    assert "CodeBoarding: CodeBoarding" not in records[-1].getMessage()
    assert records[-1].getMessage() == f"CodeBoarding: {exit_info.value.__cause__}"
