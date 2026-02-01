import pytest

from codeboarding.cli.codex_trace import (
    _ensure_json_flag,
    _extract_tracked_item,
    _inject_exec_if_missing,
    _redact_command,
)
from codeboarding.cli.codex_tui_trace import _extract_paths_from_command, _extract_tracked_event, _summarize_changes


def test_ensure_json_flag_adds_when_missing():
    args = ["--sandbox", "workspace-write"]
    updated = _ensure_json_flag(args)
    assert updated[0] == "--json"
    assert "--sandbox" in updated


def test_ensure_json_flag_preserves_existing():
    args = ["--json", "--sandbox", "workspace-write"]
    updated = _ensure_json_flag(args)
    assert updated == args


def test_extract_command_item_filters_non_command():
    event = {"type": "item.started", "item": {"type": "agent_message"}}
    assert _extract_tracked_item(event) is None


def test_extract_command_item_matches_command_execution():
    event = {
        "type": "item.completed",
        "item": {"type": "command_execution", "command": "rg TODO src"},
    }
    assert _extract_tracked_item(event) == event["item"]


def test_extract_tracked_item_matches_file_change():
    event = {"type": "item.completed", "item": {"type": "file_change", "changes": []}}
    assert _extract_tracked_item(event) == event["item"]


def test_extract_tracked_item_matches_mcp_tool_call():
    event = {"type": "item.completed", "item": {"type": "mcp_tool_call", "tool": "read"}}
    assert _extract_tracked_item(event) == event["item"]


def test_extract_tracked_event_filters_non_codex_event():
    record = {"kind": "app_event", "payload": {}}
    assert _extract_tracked_event(record) is None


def test_extract_tracked_event_accepts_exec_command():
    record = {"kind": "codex_event", "payload": {"msg": {"type": "exec_command_begin"}}}
    assert _extract_tracked_event(record) == record["payload"]["msg"]


def test_extract_tracked_event_accepts_agent_message():
    record = {"kind": "codex_event", "payload": {"msg": {"type": "agent_message", "text": "hi"}}}
    assert _extract_tracked_event(record) is None


def test_summarize_changes_strips_content():
    changes = {
        "file.py": {"type": "update", "unified_diff": "secret", "move_path": "new.py"},
        "old.txt": {"type": "delete", "content": "secret"},
    }
    summarized = _summarize_changes(changes)
    assert {"path": "file.py", "kind": "update", "move_path": "new.py"} in summarized
    assert {"path": "old.txt", "kind": "delete"} in summarized


def test_extract_paths_from_command_for_sed():
    cmd = ["/bin/zsh", "-lc", "sed -n '1,200p' README.md"]
    paths = _extract_paths_from_command(cmd, "/repo")
    assert any(path.endswith("/repo/README.md") for path in paths)


def test_extract_paths_from_command_for_rg():
    cmd = ["/bin/zsh", "-lc", "rg TODO src tests"]
    paths = _extract_paths_from_command(cmd, "/repo")
    assert any(path.endswith("/repo/src") for path in paths)
    assert any(path.endswith("/repo/tests") for path in paths)


def test_redact_command_hides_sensitive_flags():
    cmd = "curl --token abc123 --password secret --api-key=xyz"
    redacted = _redact_command(cmd)
    assert "--token [REDACTED]" in redacted
    assert "--password [REDACTED]" in redacted
    assert "--api-key=[REDACTED]" in redacted


def test_inject_exec_when_missing():
    args = ["-C", ".", "List files"]
    assert _inject_exec_if_missing(args, allow_non_exec=False) == ["exec", *args]


def test_inject_exec_skips_when_present():
    args = ["exec", "-C", ".", "List files"]
    assert _inject_exec_if_missing(args, allow_non_exec=False) == args


def test_inject_exec_blocks_other_commands():
    args = ["review", "-C", "."]
    with pytest.raises(ValueError):
        _inject_exec_if_missing(args, allow_non_exec=False)


def test_inject_exec_allows_other_commands_when_flagged():
    args = ["review", "-C", "."]
    assert _inject_exec_if_missing(args, allow_non_exec=True) == args
