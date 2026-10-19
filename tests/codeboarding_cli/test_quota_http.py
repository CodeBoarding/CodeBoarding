"""Exercise the deployed Lambda response through the model client, scope analysis and CLI."""

import logging
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agents.agent_responses import AnalysisInsights, Component
from agents.scope_analysis_agent import ScopeAnalysisAgent
from codeboarding_cli.main import main
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.exceptions import ScopeSemanticsError
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


@pytest.mark.parametrize(
    "status,error,codeboarding_quota",
    [
        (402, {"type": "gha_proxy", "message": "Resource exhausted: token limit reached"}, True),
        (402, {"message": "Insufficient upstream credits"}, False),
        (402, {"type": "provider", "message": "Resource exhausted: token limit reached"}, False),
        (429, {"code": "insufficient_quota", "message": "Balance empty"}, False),
    ],
)
def test_lambda_refusal_reaches_cli_without_a_traceback(tmp_path, capsys, caplog, status, error, codeboarding_quota):
    requests = []

    def lambda_response(request):
        requests.append(request)
        return httpx.Response(status, json={"error": error})

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
        with (
            caplog.at_level(logging.ERROR, logger="codeboarding_cli.main"),
            patch(
                "codeboarding_cli.main.full_analysis.run_from_args",
                side_effect=lambda *_: generator._enrich_scope(scope, analysis, {"1"}),
            ),
            pytest.raises(SystemExit) as exit_info,
        ):
            main(["full", "--local", str(tmp_path)])

    assert exit_info.value.code == 3
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/chat/completions"
    output = capsys.readouterr()
    assert "Traceback" not in output.err
    assert not output.out
    assert isinstance(exit_info.value.__cause__, ScopeSemanticsError)
    assert exit_info.value.__cause__.__cause__ is not None
    assert all(record.exc_info is None for record in caplog.records if record.name == "codeboarding_cli.main")
    if codeboarding_quota:
        assert "https://codeboarding.org/pricing" in caplog.text
        assert "Your free CodeBoarding quota is exhausted" in caplog.text
        assert "or wait for your weekly reset" in caplog.text
    else:
        assert "https://codeboarding.org/pricing" not in caplog.text
        assert "LLM provider's token or credit quota is exhausted" in caplog.text
