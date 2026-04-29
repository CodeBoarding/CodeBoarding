from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileEntry,
    FileMethodGroup,
    MethodEntry,
    Relation,
    assign_component_ids,
)
from agents.change_status import ChangeStatus
from diagram_analysis.incremental_updater import FileDelta, IncrementalDelta, MethodChange
from diagram_analysis.incremental_updater import apply_method_delta, prune_empty_components


def _method(qname: str, start: int = 1, end: int = 2) -> MethodEntry:
    return MethodEntry(qualified_name=qname, start_line=start, end_line=end, node_type="FUNCTION")


def _component(name: str, file_methods: list[FileMethodGroup]) -> Component:
    return Component(name=name, description=f"{name} desc", key_entities=[], file_methods=file_methods)


def _file_entries(*groups: FileMethodGroup) -> dict[str, FileEntry]:
    return {g.file_path: FileEntry(methods=list(g.methods)) for g in groups}


def test_prune_drops_leaf_component_with_no_methods():
    a = _component("Alpha", [FileMethodGroup(file_path="a.py", methods=[_method("a.foo")])])
    b = _component("Beta", [FileMethodGroup(file_path="b.py", methods=[])])
    rel = Relation(src_name="Alpha", dst_name="Beta", relation="uses")
    analysis = AnalysisInsights(
        description="test",
        components=[a, b],
        components_relations=[rel],
        files=_file_entries(*a.file_methods, *b.file_methods),
    )
    assign_component_ids(analysis)

    removed = prune_empty_components(analysis, sub_analyses={})

    assert removed == {b.component_id}
    assert [c.name for c in analysis.components] == ["Alpha"]
    assert analysis.components_relations == []


def test_prune_preserves_component_with_at_least_one_method():
    a = _component(
        "Alpha",
        [
            FileMethodGroup(file_path="a.py", methods=[_method("a.foo")]),
            FileMethodGroup(file_path="b.py", methods=[]),
        ],
    )
    analysis = AnalysisInsights(description="test", components=[a], components_relations=[])
    assign_component_ids(analysis)

    removed = prune_empty_components(analysis, sub_analyses={})

    assert removed == set()
    assert len(analysis.components) == 1


def test_prune_keeps_survivor_component_ids_stable():
    a = _component("Alpha", [FileMethodGroup(file_path="a.py", methods=[_method("a.foo")])])
    b = _component("Beta", [FileMethodGroup(file_path="b.py", methods=[])])
    c = _component("Gamma", [FileMethodGroup(file_path="c.py", methods=[_method("c.qux")])])
    analysis = AnalysisInsights(description="t", components=[a, b, c], components_relations=[])
    assign_component_ids(analysis)
    alpha_id, _, gamma_id = a.component_id, b.component_id, c.component_id
    assert (alpha_id, gamma_id) == ("1", "3")

    prune_empty_components(analysis, sub_analyses={})

    assert [c.component_id for c in analysis.components] == [alpha_id, gamma_id]


def test_prune_cascades_to_descendants_in_sub_analyses():
    parent = _component("Parent", [FileMethodGroup(file_path="x.py", methods=[])])
    sibling = _component("Sibling", [FileMethodGroup(file_path="y.py", methods=[_method("y.foo")])])
    root = AnalysisInsights(description="r", components=[parent, sibling], components_relations=[])
    assign_component_ids(root)

    child = _component("Child", [FileMethodGroup(file_path="x_inner.py", methods=[])])
    sub = AnalysisInsights(description="s", components=[child], components_relations=[])
    assign_component_ids(sub, parent_id=parent.component_id)
    sub_analyses = {parent.component_id: sub}

    removed = prune_empty_components(root, sub_analyses)

    assert parent.component_id in removed
    assert child.component_id in removed
    assert parent.component_id not in sub_analyses
    assert [c.name for c in root.components] == ["Sibling"]


def test_prune_does_not_remove_parent_when_child_is_non_empty():
    parent = _component("Parent", [FileMethodGroup(file_path="p.py", methods=[])])
    root = AnalysisInsights(description="r", components=[parent], components_relations=[])
    assign_component_ids(root)

    child = _component("Child", [FileMethodGroup(file_path="c.py", methods=[_method("c.foo")])])
    sub = AnalysisInsights(description="s", components=[child], components_relations=[])
    assign_component_ids(sub, parent_id=parent.component_id)
    sub_analyses = {parent.component_id: sub}

    removed = prune_empty_components(root, sub_analyses)

    assert removed == set()
    assert parent.component_id in sub_analyses
    assert [c.name for c in root.components] == ["Parent"]


def test_prune_drops_relations_referencing_removed_component_in_subs():
    parent = _component("Parent", [FileMethodGroup(file_path="x.py", methods=[_method("x.foo")])])
    other = _component("Other", [FileMethodGroup(file_path="y.py", methods=[_method("y.foo")])])
    root = AnalysisInsights(description="r", components=[parent, other], components_relations=[])
    assign_component_ids(root)

    dead = _component("Dead", [FileMethodGroup(file_path="d.py", methods=[])])
    alive = _component("Alive", [FileMethodGroup(file_path="a.py", methods=[_method("a.foo")])])
    rel = Relation(src_name="Alive", dst_name="Dead", relation="calls")
    sub = AnalysisInsights(description="s", components=[dead, alive], components_relations=[rel])
    assign_component_ids(sub, parent_id=parent.component_id)
    sub.components_relations[0].src_id = alive.component_id
    sub.components_relations[0].dst_id = dead.component_id

    removed = prune_empty_components(root, sub_analyses={parent.component_id: sub})

    assert dead.component_id in removed
    assert sub.components_relations == []
    assert [c.name for c in sub.components] == ["Alive"]


def test_apply_method_delta_does_not_prune_component_after_deleting_all_files():
    only = FileMethodGroup(file_path="leaf/only.py", methods=[_method("leaf.only.fn", 1, 2)])
    keep = FileMethodGroup(file_path="keep.py", methods=[_method("keep.fn", 1, 2)])
    leaf = _component("Leaf", [only])
    survivor = _component("Survivor", [keep])
    rel = Relation(src_name="Survivor", dst_name="Leaf", relation="depends on")
    analysis = AnalysisInsights(
        description="t",
        components=[leaf, survivor],
        components_relations=[rel],
        files=_file_entries(only, keep),
    )
    assign_component_ids(analysis)
    rel.src_id = survivor.component_id
    rel.dst_id = leaf.component_id

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="leaf/only.py",
                file_status=ChangeStatus.DELETED,
                component_id=leaf.component_id,
                deleted_methods=[
                    MethodChange(
                        qualified_name="leaf.only.fn",
                        file_path="leaf/only.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.DELETED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ]
    )

    apply_method_delta(analysis, sub_analyses={}, delta=delta)

    assert [c.name for c in analysis.components] == ["Leaf", "Survivor"]
    assert all(not g.methods for g in leaf.file_methods)
    assert analysis.components_relations == [rel]
    assert "leaf/only.py" not in analysis.files

    prune_empty_components(analysis, sub_analyses={})
    assert [c.name for c in analysis.components] == ["Survivor"]
    assert analysis.components_relations == []


def test_prune_no_op_when_nothing_empty():
    a = _component("Alpha", [FileMethodGroup(file_path="a.py", methods=[_method("a.foo")])])
    analysis = AnalysisInsights(description="t", components=[a], components_relations=[])
    assign_component_ids(analysis)

    removed = prune_empty_components(analysis, sub_analyses={})

    assert removed == set()
