from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    SourceCodeReference,
    assign_component_ids,
)
from agents.change_status import ChangeStatus
from diagram_analysis.checkpoints import build_file_component_index
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.incremental_models import IncrementalSummaryKind
from diagram_analysis.incremental_types import FileDelta, IncrementalDelta, MethodChange
from diagram_analysis.incremental_updater import IncrementalUpdater, apply_delta
from repo_utils.change_detector import ChangeSet, ChangeType, DetectedChange


def _make_analysis(file_path: str, method: MethodEntry) -> AnalysisInsights:
    file_entry = FileEntry(file_status=ChangeStatus.UNCHANGED, methods=[method.model_copy(deep=True)])
    file_group = FileMethodGroup(
        file_path=file_path,
        file_status=ChangeStatus.UNCHANGED,
        methods=[method.model_copy(deep=True)],
    )
    component = Component(
        name="Core",
        description="Core component",
        key_entities=[
            SourceCodeReference(
                qualified_name=method.qualified_name,
                reference_file=file_path,
                reference_start_line=method.start_line,
                reference_end_line=method.end_line,
            )
        ],
        file_methods=[file_group],
    )
    analysis = AnalysisInsights(
        description="rename test",
        components=[component],
        components_relations=[],
        files={file_path: file_entry},
    )
    assign_component_ids(analysis)
    return analysis


def _make_generator(tmp_path):
    return DiagramGenerator(
        repo_location=tmp_path,
        temp_folder=tmp_path / "temp",
        repo_name="test-repo",
        output_dir=tmp_path / ".codeboarding",
        depth_level=2,
        run_id="test-run",
        log_path=str(tmp_path / "log"),
    )


@pytest.mark.parametrize(
    ("old_path", "new_path", "old_qname", "new_qname"),
    [
        ("src/python_mod.py", "src/python_mod_renamed.py", "src.python_mod.alpha", "src.python_mod_renamed.alpha"),
        ("src/widget.ts", "src/widget_renamed.ts", "src.widget.render", "src.widget_renamed.render"),
        ("pkg/worker.go", "pkg/worker_renamed.go", "pkg.worker.Run", "pkg.worker_renamed.Run"),
        ("app/Service.php", "app/RenamedService.php", "app.Service.handle", "app.RenamedService.handle"),
        ("src/Main.java", "src/MainRenamed.java", "src.Main.run", "src.MainRenamed.run"),
    ],
)
def test_pure_file_rename_is_deterministic_across_supported_languages(old_path, new_path, old_qname, new_qname):
    old_method = MethodEntry(qualified_name=old_qname, start_line=3, end_line=8, node_type="FUNCTION")
    new_method = MethodEntry(qualified_name=new_qname, start_line=3, end_line=8, node_type="FUNCTION")
    analysis = _make_analysis(old_path, old_method)
    file_component_index = build_file_component_index(analysis)

    def resolver(file_path: str) -> list[MethodEntry]:
        if file_path == new_path:
            return [new_method.model_copy(deep=True)]
        return []

    changes = ChangeSet(
        changes=[DetectedChange(ChangeType.RENAMED, file_path=new_path, old_path=old_path, similarity=100)],
    )
    updater = IncrementalUpdater(analysis, file_component_index, symbol_resolver=resolver, repo_dir=Path("."))

    delta = updater.compute_delta([], [], [], changes)

    assert delta.is_purely_additive
    assert len(delta.file_deltas) == 1
    rename_delta = delta.file_deltas[0]
    assert rename_delta.file_status == ChangeStatus.RENAMED
    assert rename_delta.old_file_path == old_path
    assert rename_delta.file_path == new_path
    assert rename_delta.renamed_qualified_names == {old_qname: new_qname}
    assert file_component_index.get_component_for_file(old_path) is None
    assert file_component_index.get_component_for_file(new_path) == analysis.components[0].component_id

    apply_delta(analysis, {}, delta)

    assert old_path not in analysis.files
    assert analysis.files[new_path].file_status == ChangeStatus.RENAMED
    assert [method.qualified_name for method in analysis.files[new_path].methods] == [new_qname]
    assert analysis.components[0].file_methods[0].file_path == new_path
    assert [method.qualified_name for method in analysis.components[0].file_methods[0].methods] == [new_qname]
    assert analysis.components[0].key_entities[0].reference_file == new_path
    assert analysis.components[0].key_entities[0].qualified_name == new_qname


def test_pure_file_rename_falls_back_to_previous_methods_when_static_analysis_has_no_match():
    old_path = "src/legacy.py"
    new_path = "src/legacy_renamed.py"
    old_method = MethodEntry(qualified_name="src.legacy.alpha", start_line=1, end_line=4, node_type="FUNCTION")
    analysis = _make_analysis(old_path, old_method)
    file_component_index = build_file_component_index(analysis)
    changes = ChangeSet(
        changes=[DetectedChange(ChangeType.RENAMED, file_path=new_path, old_path=old_path, similarity=100)],
    )
    updater = IncrementalUpdater(analysis, file_component_index, symbol_resolver=lambda _: [], repo_dir=Path("."))

    delta = updater.compute_delta([], [], [], changes)
    apply_delta(analysis, {}, delta)

    assert old_path not in analysis.files
    assert analysis.files[new_path].file_status == ChangeStatus.RENAMED
    assert [method.qualified_name for method in analysis.files[new_path].methods] == ["src.legacy.alpha"]
    assert analysis.components[0].key_entities[0].reference_file == new_path


def test_rename_for_file_outside_analysis_is_ignored():
    old_method = MethodEntry(qualified_name="src.core.alpha", start_line=1, end_line=2, node_type="FUNCTION")
    analysis = _make_analysis("src/core.py", old_method)
    file_component_index = build_file_component_index(analysis)
    changes = ChangeSet(
        changes=[
            DetectedChange(
                ChangeType.RENAMED,
                file_path="docs/renamed.md",
                old_path="docs/original.md",
                similarity=100,
            )
        ],
    )
    updater = IncrementalUpdater(analysis, file_component_index, symbol_resolver=lambda _: [], repo_dir=Path("."))

    delta = updater.compute_delta([], [], [], changes)

    assert delta.file_deltas == []
    assert file_component_index.get_component_for_file("src/core.py") == analysis.components[0].component_id


def test_rename_delta_coexists_with_other_changed_files():
    rename_old = MethodEntry(qualified_name="src.alpha.helper", start_line=1, end_line=2, node_type="FUNCTION")
    rename_new = MethodEntry(qualified_name="src.alpha_v2.helper", start_line=1, end_line=2, node_type="FUNCTION")
    other_old = MethodEntry(qualified_name="src.beta.work", start_line=1, end_line=2, node_type="FUNCTION")
    other_new = MethodEntry(qualified_name="src.beta.work", start_line=1, end_line=3, node_type="FUNCTION")
    analysis = AnalysisInsights(
        description="rename and modify",
        components=[],
        components_relations=[],
        files={
            "src/alpha.py": FileEntry(file_status=ChangeStatus.UNCHANGED, methods=[rename_old]),
            "src/beta.py": FileEntry(file_status=ChangeStatus.UNCHANGED, methods=[other_old]),
        },
    )
    component = Component(
        name="Core",
        description="Core component",
        key_entities=[],
        file_methods=[
            FileMethodGroup(file_path="src/alpha.py", file_status=ChangeStatus.UNCHANGED, methods=[rename_old]),
            FileMethodGroup(file_path="src/beta.py", file_status=ChangeStatus.UNCHANGED, methods=[other_old]),
        ],
    )
    analysis.components = [component]
    assign_component_ids(analysis)
    file_component_index = build_file_component_index(analysis)

    def resolver(file_path: str) -> list[MethodEntry]:
        if file_path == "src/alpha_v2.py":
            return [rename_new.model_copy(deep=True)]
        if file_path == "src/beta.py":
            return [other_new.model_copy(deep=True)]
        return []

    changes = ChangeSet(
        changes=[
            DetectedChange(ChangeType.RENAMED, file_path="src/alpha_v2.py", old_path="src/alpha.py", similarity=100),
            DetectedChange(ChangeType.MODIFIED, file_path="src/beta.py"),
        ],
    )
    updater = IncrementalUpdater(analysis, file_component_index, symbol_resolver=resolver, repo_dir=Path("."))

    delta = updater.compute_delta([], ["src/beta.py"], [], changes)

    assert {file_delta.file_path for file_delta in delta.file_deltas} == {"src/alpha_v2.py", "src/beta.py"}
    rename_delta = next(file_delta for file_delta in delta.file_deltas if file_delta.file_path == "src/alpha_v2.py")
    assert rename_delta.file_status == ChangeStatus.RENAMED


def test_generator_applies_rename_delta_before_saving_and_skips_llm_for_pure_rename(tmp_path):
    gen = _make_generator(tmp_path)
    output_dir = tmp_path / ".codeboarding"
    output_dir.mkdir(parents=True, exist_ok=True)
    gen.static_analysis = Mock()

    old_path = "src/core.py"
    new_path = "src/core_renamed.py"
    old_method = MethodEntry(qualified_name="src.core.alpha", start_line=1, end_line=3, node_type="FUNCTION")
    new_method = MethodEntry(qualified_name="src.core_renamed.alpha", start_line=1, end_line=3, node_type="FUNCTION")
    root_analysis = _make_analysis(old_path, old_method)
    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path=new_path,
                old_file_path=old_path,
                file_status=ChangeStatus.RENAMED,
                component_id=root_analysis.components[0].component_id,
                renamed_qualified_names={old_method.qualified_name: new_method.qualified_name},
                is_reset=True,
                reset_methods=[
                    MethodChange(
                        qualified_name=new_method.qualified_name,
                        file_path=new_path,
                        start_line=new_method.start_line,
                        end_line=new_method.end_line,
                        change_type=ChangeStatus.UNCHANGED,
                        node_type=new_method.node_type,
                    )
                ],
            )
        ]
    )
    checkpoint = Mock(checkpoint_ref="refs/codeboarding/checkpoints/latest")
    changes = Mock(parsed_diff=None, deleted_files=[])
    saved_path = output_dir / "analysis.json"

    with (
        patch.object(
            gen,
            "_load_incremental_baseline",
            return_value=(checkpoint, root_analysis, {}, Mock(), {}),
        ),
        patch("diagram_analysis.diagram_generator.detect_changes", return_value=changes),
        patch.object(gen, "_compute_incremental_delta", return_value=delta),
        patch("diagram_analysis.diagram_generator.initialize_llms") as mock_initialize_llms,
        patch.object(gen, "_run_semantic_trace") as mock_run_semantic_trace,
        patch.object(gen, "_save_incremental_result", return_value=saved_path) as mock_save_result,
    ):
        result = gen.generate_analysis_incremental()

    mock_initialize_llms.assert_not_called()
    mock_run_semantic_trace.assert_not_called()
    mock_save_result.assert_called_once()
    assert old_path not in root_analysis.files
    assert root_analysis.files[new_path].file_status == ChangeStatus.RENAMED
    assert root_analysis.components[0].file_methods[0].file_path == new_path
    assert result == saved_path
    assert gen.last_incremental_summary is not None
    assert gen.last_incremental_summary.kind == IncrementalSummaryKind.RENAME_ONLY
    assert gen.last_incremental_summary.used_llm is False
