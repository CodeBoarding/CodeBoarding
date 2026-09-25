import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.llm_errors import EXIT_AUTH_ERROR, EXIT_QUOTA_EXHAUSTED, LLMAuthError, LLMQuotaError
from main import main


@pytest.fixture
def stub_run_incremental(tmp_path: Path):
    """Patch the chain so ``run_from_args`` reaches ``run_incremental`` without running it.

    Detection is fully git-free and internal: the CLI passes only paths and run
    context — no changed-file set, no git refs, no source hash.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.bootstrap_environment"))
        rc = stack.enter_context(patch("codeboarding_cli.commands.incremental_analysis.RunContext"))
        rc.resolve.return_value.run_id = "rid"
        rc.resolve.return_value.log_path = "logs/run.log"
        ri = stack.enter_context(
            patch(
                "codeboarding_cli.commands.incremental_analysis.run_incremental",
                return_value=tmp_path / "analysis.json",
            )
        )
        yield ri


def test_incremental_calls_run_incremental_with_paths_only(tmp_path: Path, stub_run_incremental) -> None:
    ri = stub_run_incremental

    main(["incremental", "--local", str(tmp_path)])

    ri.assert_called_once()
    run_paths = ri.call_args.args[0]
    assert run_paths.repo_path == tmp_path
    assert run_paths.output_dir == tmp_path / ".codeboarding"


def _quota_error() -> LLMQuotaError:
    return LLMQuotaError(
        "The openai LLM provider refused the request because the token or credit quota is exhausted (HTTP 402).",
        provider="openai",
        status_code=402,
        provider_message="Resource exhausted: token limit reached",
        telemetry_properties={"error_type": "quota"},
    )


def test_incremental_quota_exhaustion_exits_3_without_asking_for_a_full_run(
    tmp_path: Path, stub_run_incremental, capsys
) -> None:
    stub_run_incremental.side_effect = _quota_error()

    with pytest.raises(SystemExit) as caught:
        main(["incremental", "--local", str(tmp_path)])

    assert caught.value.code == EXIT_QUOTA_EXHAUSTED == 3
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "mode": "incremental",
        "error": str(stub_run_incremental.side_effect),
        "kind": "llm_quota_exhausted",
        "statusCode": 402,
        "provider": "openai",
        "requiresFullAnalysis": False,
    }
    assert "quota" in captured.err
    assert "Traceback" not in captured.err


def test_incremental_auth_failure_exits_2_without_asking_for_a_full_run(
    tmp_path: Path, stub_run_incremental, capsys
) -> None:
    stub_run_incremental.side_effect = LLMAuthError(
        "Your openai API key was rejected (HTTP 401).",
        provider="openai",
        key_tail="a8dd",
        telemetry_properties={"error_type": "auth", "error_status_code": 401},
    )

    with pytest.raises(SystemExit) as caught:
        main(["incremental", "--local", str(tmp_path)])

    assert caught.value.code == EXIT_AUTH_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "llm_auth"
    assert payload["statusCode"] == 401
    assert payload["requiresFullAnalysis"] is False


def test_incremental_other_failures_still_ask_for_a_full_run(tmp_path: Path, stub_run_incremental, capsys) -> None:
    stub_run_incremental.side_effect = RuntimeError("scope invariant broke")

    main(["incremental", "--local", str(tmp_path)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["requiresFullAnalysis"] is True
    assert "kind" not in payload
