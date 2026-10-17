"""Mock the Lambda HTTP boundary while exercising the model client, agent and CLI."""

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agents.agent_responses import AnalysisInsights, Component
from agents.llm_config import OIDC_RELAY_PLACEHOLDER_KEY
from agents.llm_errors import LLMQuotaError, detect_quota_error, is_transient_llm_error
from agents.scope_analysis_agent import ScopeAnalysisAgent
from agents.tree_planner_agent import TreePlannerAgent
from codeboarding_cli.main import main
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


@pytest.mark.parametrize(
    "status,error,tier,expected_code",
    [
        (
            402,
            {"type": "gha_proxy", "message": "Resource exhausted: token limit reached"},
            "hosted",
            "free_quota_exhausted",
        ),
        (
            402,
            {"type": "gha_proxy", "code": "free_quota_exhausted", "message": "Allowance spent"},
            "hosted",
            "free_quota_exhausted",
        ),
        (
            402,
            {"type": "gha_proxy", "message": "Resource exhausted: token limit reached"},
            "license",
            "license_not_accepted",
        ),
        (
            402,
            {
                "type": "gha_proxy",
                "code": "free_quota_exhausted",
                "license_status": "not_accepted",
                "message": "Allowance spent",
            },
            "hosted",
            "license_not_accepted",
        ),
        (402, {"message": "Insufficient upstream credits"}, "hosted", "hosted_provider_quota_exhausted"),
        (429, {"code": "insufficient_quota", "message": "Balance empty"}, "byok", "provider_quota_exhausted"),
    ],
)
def test_lambda_refusal_reaches_cli_without_a_traceback(
    tmp_path, monkeypatch, capsys, status, error, tier, expected_code
):
    requests = []

    def lambda_response(request):
        requests.append(request)
        return httpx.Response(status, json={"error": error})

    report = tmp_path / "error.json"
    monkeypatch.setenv("CODEBOARDING_ERROR_FILE", str(report))
    monkeypatch.setenv("CODEBOARDING_LLM_TIER", tier)
    key_tail = "hosted" if tier != "byok" else "test"
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
    with httpx.Client(transport=httpx.MockTransport(lambda_response)) as client:
        model = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=SecretStr(OIDC_RELAY_PLACEHOLDER_KEY),
            base_url="https://lambda.example/v1",
            http_client=client,
            max_retries=0,
        )
        agent = ScopeAnalysisAgent(Path("/repo"), static, model)
        with (
            patch("agents.llm_errors.current_provider_key_context", return_value=("openrouter", key_tail)),
            patch(
                "codeboarding_cli.main.full_analysis.run_from_args",
                side_effect=lambda *_: agent.analyze(scope, analysis, {"1"}),
            ),
            pytest.raises(SystemExit) as exit_info,
        ):
            main(["full", "--local", str(tmp_path)])

    assert exit_info.value.code == 3
    assert json.loads(report.read_text()) == {"version": 1, "code": expected_code}
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/chat/completions"
    output = capsys.readouterr()
    assert "Traceback" not in output.err
    assert not output.out
    assert isinstance(exit_info.value.__cause__, LLMQuotaError)
    assert exit_info.value.__cause__.__cause__ is not None
    if expected_code in ("free_quota_exhausted", "license_not_accepted"):
        assert "https://codeboarding.org/pricing" in output.err
    else:
        assert "https://codeboarding.org/pricing" not in output.err


def test_planner_does_not_drop_a_terminal_quota_error():
    error = LLMQuotaError(
        "Credits exhausted", code="free_quota_exhausted", telemetry_properties={"error_type": "quota"}
    )
    with patch.object(TreePlannerAgent, "_ask", side_effect=error):
        planner = TreePlannerAgent(ChatOpenAI(model="gpt-4o-mini", api_key=SecretStr("test")), draws=3)
        with pytest.raises(LLMQuotaError) as caught:
            planner._draw("prompt", {})
    assert caught.value is error
    assert not is_transient_llm_error(error)


def test_quota_survives_a_wrapped_or_cyclic_exception():
    error = LLMQuotaError(
        "Credits exhausted", code="free_quota_exhausted", telemetry_properties={"error_type": "quota"}
    )
    wrapped = RuntimeError("planner failed")
    wrapped.__cause__ = error
    assert detect_quota_error(wrapped) is error
    wrapped.__cause__ = wrapped
    assert detect_quota_error(wrapped) is None
