from clustering_ids import ROOT_SCOPE_ID
from static_analyzer.clustering.names import ComponentRule, KinshipGrouper, ScopeSpec, TreeSpec, draft_tree
from static_analyzer.clustering.names.spec import SPEC_VERSION, UNPLACED
from tests.static_analyzer.names.conftest import units_from_layout


def layout() -> dict[str, list[str]]:
    return {
        f"src/{name}/{name}{i}.cs": [f"{name}.{name}{i}", f"{name}.{name}{i}.Run()"]
        for name in ("Ordering", "OrderProcessor", "Catalog", "Basket")
        for i in range(3)
    }


class TestTreeSpecRoundTrip:
    def test_a_drafted_tree_survives_json(self):
        spec = draft_tree(units_from_layout(layout(), "csharp"), KinshipGrouper(), 2, machinery=("Handler",))
        raw = spec.to_dict()
        assert raw["version"] == SPEC_VERSION and raw["grouper"] == "kinship" and raw["machinery"] == ["Handler"]
        assert TreeSpec.from_dict(raw).to_dict() == raw

    def test_parts_prefixes_and_terms_are_preserved(self):
        rule = ComponentRule(
            "1",
            "Ordering",
            prefixes=(("Ordering",), ("OrderProcessor",)),
            terms=("order",),
            fallback_prefixes=((),),
            parts=(ComponentRule("", "Ordering", prefixes=(("Ordering",),)), ComponentRule("", "OrderProcessor")),
            origin="grouped",
        )
        assert ComponentRule.from_dict(rule.to_dict()) == rule

    def test_a_leaf_scope_keeps_its_reason(self):
        scope = ScopeSpec("3", rung="leaf", leaf_reason="cohesive: 4 units")
        assert ScopeSpec.from_dict("3", scope.to_dict()) == scope
        assert scope.is_leaf


class TestTreeSpecReroot:
    def test_an_absorbed_child_hands_its_rules_to_its_parent_and_moves_every_id_up(self):
        spec = TreeSpec(
            scopes={
                ROOT_SCOPE_ID: ScopeSpec(ROOT_SCOPE_ID, [ComponentRule("1", "Only", prefixes=(("a",),))]),
                "1": ScopeSpec("1", [ComponentRule("1.1", "A"), ComponentRule("1.2", "B")], rung="segment"),
                "1.1": ScopeSpec("1.1", [ComponentRule("1.1.1", "AA"), ComponentRule("1.1.2", "AB")]),
                "1.2": ScopeSpec("1.2", rung="leaf", leaf_reason="cohesive"),
            }
        )
        spec.reroot(["1"])
        assert set(spec.scopes) == {ROOT_SCOPE_ID, "1", "2"}
        root = spec.scopes[ROOT_SCOPE_ID]
        assert [rule.component_id for rule in root.rules] == ["1", "2"]
        assert root.rung == "segment"
        assert spec.scopes["1"].scope_id == "1"
        assert [rule.component_id for rule in spec.scopes["1"].rules] == ["1.1", "1.2"]
        assert spec.scopes["2"].is_leaf

    def test_a_retired_sibling_of_the_absorbed_child_is_dropped(self):
        spec = TreeSpec(
            scopes={
                ROOT_SCOPE_ID: ScopeSpec(ROOT_SCOPE_ID, [ComponentRule("1", "Only")]),
                "1": ScopeSpec("1", [ComponentRule("1.1", "A")], last_id=2),
                "1.1": ScopeSpec("1.1", [ComponentRule("1.1.1", "AA"), ComponentRule("1.1.2", "AB")]),
                "1.2": ScopeSpec("1.2", rung="leaf", leaf_reason="retired"),
                "2": ScopeSpec("2", rung="leaf"),
            }
        )
        spec.reroot(["1.1"])
        assert set(spec.scopes) == {ROOT_SCOPE_ID, "1", "2"}
        assert [rule.component_id for rule in spec.scopes["1"].rules] == ["1.1", "1.2"]

    def test_a_child_never_drafted_changes_nothing(self):
        spec = TreeSpec(scopes={ROOT_SCOPE_ID: ScopeSpec(ROOT_SCOPE_ID, [ComponentRule("1", "Only")])})
        spec.reroot(["1"])
        assert [rule.component_id for rule in spec.scopes[ROOT_SCOPE_ID].rules] == ["1"]


class TestScopeSpec:
    def test_a_retired_id_is_never_reissued_even_after_a_round_trip(self):
        scope = ScopeSpec("root", [ComponentRule("1", "A"), ComponentRule("2", "B"), ComponentRule("3", "C")])
        assert scope.next_id() == "4"
        scope.rules = scope.rules[:2]
        scope = ScopeSpec.from_dict("root", scope.to_dict())
        assert scope.next_id() == "5"

    def test_next_id_is_fresh_never_a_refilled_gap(self):
        scope = ScopeSpec("2", [ComponentRule("2.1", "a"), ComponentRule("2.3", "b")])
        assert scope.next_id() == "2.4"
        assert scope.next_id(taken={"2.7"}) == "2.8"
        assert ScopeSpec(ROOT_SCOPE_ID).next_id() == "1"

    def test_scopes_serialise_in_tree_order(self):
        spec = TreeSpec(scopes={s: ScopeSpec(s) for s in ("1.1", "10", "2", "root", "1")})
        assert list(spec.to_dict()["scopes"]) == ["root", "1", "2", "10", "1.1"]

    def test_components_exclude_the_bucket(self):
        scope = ScopeSpec("root", [ComponentRule("1", "a"), ComponentRule("2", "Unassigned", kind=UNPLACED)])
        assert [rule.component_id for rule in scope.components] == ["1"]
        assert scope.unplaced_rule is not None and scope.unplaced_rule.component_id == "2"
        assert scope.rule("9") is None

    def test_a_rule_without_prefix_or_word_is_fallback_only(self):
        assert ComponentRule("1", "Loose files", fallback_prefixes=((),)).is_fallback_only
        assert not ComponentRule("2", "Catalog", prefixes=(("Catalog",),)).is_fallback_only
