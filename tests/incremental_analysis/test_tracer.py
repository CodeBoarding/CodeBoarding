from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.change_status import ChangeStatus
from diagram_analysis.incremental_models import TraceStopReason
from diagram_analysis.incremental_tracer import build_trace_plan, run_trace
from diagram_analysis.incremental_updater import FileDelta, IncrementalDelta, MethodChange
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


def _make_node(name: str, file_path: str, start_line: int = 1, end_line: int = 4) -> Node:
    return Node(name, NodeType.FUNCTION, file_path, start_line, end_line)


def test_build_trace_plan_marks_disconnected_added_files(tmp_path: Path):
    source = tmp_path / "src" / "new_module.py"
    source.parent.mkdir(parents=True)
    source.write_text("def new_func():\n    return 1\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/new_module.py",
                file_status=ChangeStatus.ADDED,
                added_methods=[
                    MethodChange(
                        qualified_name="new_module.new_func",
                        file_path="src/new_module.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.ADDED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ]
    )

    trace_plan = build_trace_plan(delta, {}, tmp_path, "HEAD")

    assert trace_plan.disconnected_files == ["src/new_module.py"]
    assert len(trace_plan.groups) == 1
    assert trace_plan.groups[0].methods[0].qualified_name == "new_module.new_func"


def test_run_trace_marks_syntax_error_files_non_traceable(tmp_path: Path):
    broken = tmp_path / "src" / "broken.py"
    broken.parent.mkdir(parents=True)
    broken.write_text("def broken(:\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/broken.py",
                file_status=ChangeStatus.MODIFIED,
                modified_methods=[
                    MethodChange(
                        qualified_name="broken.func",
                        file_path="src/broken.py",
                        start_line=1,
                        end_line=1,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ]
    )

    result = run_trace(delta, {}, MagicMock(), tmp_path, "HEAD", MagicMock())

    assert result.non_traceable_files == ["src/broken.py"]
    assert result.stop_reason == TraceStopReason.UNCERTAIN


def test_run_trace_tracks_visited_methods(tmp_path: Path):
    source = tmp_path / "src" / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("def seed():\n    return helper()\n\ndef helper():\n    return 1\n")

    cfg = CallGraph(language="python")
    cfg.add_node(_make_node("mod.seed", "src/mod.py", 1, 2))
    cfg.add_node(_make_node("mod.helper", "src/mod.py", 4, 5))
    cfg.add_edge("mod.seed", "mod.helper")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/mod.py",
                file_status=ChangeStatus.MODIFIED,
                modified_methods=[
                    MethodChange(
                        qualified_name="mod.seed",
                        file_path="src/mod.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ]
    )

    static_analysis = MagicMock()
    static_analysis.get_languages.return_value = ["python"]
    static_analysis.get_reference.side_effect = lambda _language, qualified_name: {
        "mod.seed": _make_node("mod.seed", str(source), 1, 2),
        "mod.helper": _make_node("mod.helper", str(source), 4, 5),
    }[qualified_name]
    static_analysis.get_loose_reference.return_value = (None, None)

    class _Extractor:
        def __init__(self):
            self._calls = 0

        def invoke(self, _payload, config=None):
            self._calls += 1
            if self._calls == 1:
                return {
                    "responses": [
                        {
                            "status": "continue",
                            "impacted_methods": ["mod.seed"],
                            "next_methods_to_fetch": ["mod.helper"],
                            "unresolved_frontier": [],
                            "reason": "Need helper body",
                            "semantic_impact_summary": "",
                            "confidence": 0.8,
                        }
                    ]
                }
            return {
                "responses": [
                    {
                        "status": "stop_material_semantic_impact_closure_reached",
                        "impacted_methods": ["mod.seed", "mod.helper"],
                        "next_methods_to_fetch": [],
                        "unresolved_frontier": [],
                        "reason": "Closure reached",
                        "semantic_impact_summary": "The request flow changed.",
                        "confidence": 0.9,
                    }
                ]
            }

    with patch("diagram_analysis.incremental_tracer.create_extractor", return_value=_Extractor()):
        result = run_trace(delta, {"python": cfg}, static_analysis, tmp_path, "HEAD", MagicMock())

    assert result.stop_reason == TraceStopReason.CLOSURE_REACHED
    assert "mod.seed" in result.visited_methods
    assert "mod.helper" in result.visited_methods
    assert set(result.impacted_methods) == {"mod.seed", "mod.helper"}
