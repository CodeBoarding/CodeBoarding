from agents.agent_responses import SourceCodeReference
from agents.component_ownership import ComponentOwnershipIndex
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult


def hierarchy() -> ClusterScopeResult:
    """Two top-level groups; the first one splits into two children at depth two."""
    child_scope = ClusterScopeResult(
        scope_id="1",
        groups=[
            ClusterGroup(group_id="1.1", cluster_ids=[1], symbol_members_by_language={"python": {"pkg.a.one"}}),
            ClusterGroup(group_id="1.2", cluster_ids=[2], symbol_members_by_language={"python": {"pkg.a.two"}}),
        ],
    )
    root = ClusterScopeResult(
        scope_id="root",
        groups=[
            ClusterGroup(
                group_id="1",
                cluster_ids=[1, 2],
                symbol_members_by_language={"python": {"pkg.a.one", "pkg.a.two"}},
                children=child_scope,
            ),
            ClusterGroup(group_id="2", cluster_ids=[3], symbol_members_by_language={"python": {"pkg.b.three"}}),
        ],
    )
    root.index_hierarchy()
    return root


def reference(qualified_name: str) -> SourceCodeReference:
    return SourceCodeReference(qualified_name=qualified_name, reference_file="pkg.py")


def test_from_clustering_hierarchy_resolves_every_depth_to_its_deepest_owner() -> None:
    index = ComponentOwnershipIndex.from_clustering_hierarchy(hierarchy())

    assert index.owner_of(reference("pkg.a.one")) == "1.1"
    assert index.owner_of(reference("pkg.a.two")) == "1.2"
    assert index.owner_of(reference("pkg.b.three")) == "2"


def test_from_clustering_hierarchy_covers_symbols_outside_any_single_scope() -> None:
    """The whole point: a scope naming 1.1 and 1.2 can still resolve an endpoint owned by 2."""
    index = ComponentOwnershipIndex.from_clustering_hierarchy(hierarchy())
    scope_only = ComponentOwnershipIndex.from_node_owners({"pkg.a.one": "1.1", "pkg.a.two": "1.2"})

    assert scope_only.owner_of(reference("pkg.b.three")) == ""
    assert index.owner_of(reference("pkg.b.three")) == "2"


def test_ancestor_groups_never_make_their_own_members_ambiguous() -> None:
    """A flat union over all depths would give pkg.a.one two owners and blank the index out."""
    index = ComponentOwnershipIndex.from_clustering_hierarchy(hierarchy())

    assert index.owner_of(reference("pkg.a.one")) == "1.1"
