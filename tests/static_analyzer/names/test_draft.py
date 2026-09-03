"""Drafting: the frontier grouped into components, the ladder below them, and the guard."""

import pytest

from clustering_ids import ROOT_SCOPE_ID
from static_analyzer.clustering.names import (
    CandidateGroup,
    KinshipGrouper,
    ROLE_WORDS,
    draft_scope,
    draft_tree,
    replay,
)
from static_analyzer.clustering.names.draft import (
    FRONTIER,
    GUARD_SHARE,
    LEAF,
    LEAF_CAP,
    MIN_UNITS,
    SEGMENT,
    UNMERGE,
    VOCABULARY,
)
from static_analyzer.clustering.names.spec import UNPLACED
from tests.static_analyzer.names.conftest import rule_of, scope_of, units_from_layout


def project(name: str, count: int, *subdirs: str) -> dict[str, list[str]]:
    layout: dict[str, list[str]] = {}
    stem = name.split(".")[0]
    for index in range(count):
        sub = subdirs[index % len(subdirs)] if subdirs else ""
        prefix = f"{name}.{sub}" if sub else name
        layout[f"src/{name}/{sub}/{stem}Type{index}.cs"] = [
            f"{prefix}.{stem}Type{index}",
            f"{prefix}.{stem}Type{index}.Run()",
        ]
    return layout


def eshop() -> dict[str, list[str]]:
    return (
        project("Ordering.API", 12, "Apis", "Application")
        | project("Ordering.Domain", 4)
        | project("OrderProcessor", 3)
        | project("Catalog.API", 8, "Model", "Apis")
        | project("Basket.API", 4, "Model")
        | project("Webhooks.API", 5)
        | project("WebhookClient", 3)
        | project("PaymentProcessor", 3)
    )


def names_of(scope) -> list[str]:
    return [rule.name for rule in scope.rules]


class TestRootDraft:
    def test_kinship_groups_scopes_sharing_their_word(self):
        scope, partition = draft_scope(
            ROOT_SCOPE_ID, units_from_layout(eshop(), "csharp"), ROLE_WORDS, KinshipGrouper()
        )
        assert scope.rung == FRONTIER and scope.axis == "structural"
        assert names_of(scope) == ["Ordering", "Catalog", "Webhooks", "Basket", "PaymentProcessor"]
        ordering = rule_of(scope, "1")
        assert ordering.prefixes == (("OrderProcessor",), ("Ordering",))
        assert ordering.terms == ("order",)
        assert [part.name for part in ordering.parts] == ["OrderProcessor", "Ordering"]
        assert partition.size("1") == 19
        assert rule_of(scope, "5").parts == ()

    def test_ids_follow_size_then_name(self):
        scope, _ = draft_scope(ROOT_SCOPE_ID, units_from_layout(eshop(), "csharp"), ROLE_WORDS, KinshipGrouper())
        assert [rule.component_id for rule in scope.rules] == ["1", "2", "3", "4", "5"]

    def test_the_root_frontier_takes_no_guard(self):
        """A two-file directory is a box; small boxes are the grouper's to merge, not hidden."""
        layout = eshop() | project("Tiny.API", 2)
        scope, _ = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout, "csharp"), ROLE_WORDS, KinshipGrouper())
        assert "Tiny" in names_of(scope)

    def test_a_transposed_root_places_loose_units_by_their_words(self):
        layout: dict[str, list[str]] = {}
        for feature in ("Incidents", "Metrics", "Teams"):
            for layer in ("Application", "Domain", "Infrastructure"):
                for index in range(2):
                    layout[f"Beacon.{layer}/{feature}/{feature}{index}.cs"] = [
                        f"Beacon.{layer}.{feature}.{feature}Thing{index}"
                    ]
        layout["Beacon.Application/IncidentResolvedMetricsHandler.cs"] = [
            "Beacon.Application.IncidentResolvedMetricsHandler",
            "Beacon.Application.IncidentResolvedMetricsHandler.Handle()",
        ]
        layout["Beacon.Application/Bootstrap.cs"] = [
            "Beacon.Application.Bootstrap",
            "Beacon.Application.Bootstrap.Run()",
        ]
        scope, partition = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout, "csharp"), ROLE_WORDS, KinshipGrouper())
        assert scope.axis == "transposed"
        by_name = {rule.name: rule.component_id for rule in scope.rules}
        assert partition.assignment["Beacon.Application/IncidentResolvedMetricsHandler.cs"] == by_name["Metrics"]
        assert partition.assignment["Beacon.Application/Bootstrap.cs"] == by_name["Application (residual)"]

    def test_one_box_at_the_root_falls_through_to_the_words(self):
        layout = {
            f"converters/{fmt}_{index}.py": [
                f"converters.{fmt}_{index}.{fmt.capitalize()}Converter",
                f"converters.{fmt}_{index}.convert",
            ]
            for fmt in ("docx", "pdf", "pptx")
            for index in range(2)
        }
        scope, partition = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout), ROLE_WORDS, KinshipGrouper())
        assert scope.rung == VOCABULARY
        assert names_of(scope) == ["Docx", "Pdf", "Pptx"]
        assert all(partition.size(rule.component_id) == 2 for rule in scope.rules)

    def test_what_no_rule_claims_gets_a_bucket(self):
        layout = {
            f"converters/{fmt}_{role}.py": [f"converters.{fmt}_{role}.{fmt.capitalize()}{role.capitalize()}"]
            for fmt in ("docx", "pdf")
            for role in ("reader", "writer")
        }
        layout["converters/base.py"] = ["converters.base.Converter"]
        scope, partition = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout), ROLE_WORDS, KinshipGrouper())
        bucket = scope.unplaced_rule
        assert bucket is not None and bucket.kind == UNPLACED
        assert partition.assignment["converters/base.py"] == bucket.component_id
        assert [unit.unit_id for unit in partition.unplaced] == ["converters/base.py"]

    def test_a_root_nothing_splits_is_one_box_never_a_refusal(self):
        layout = {f"{d}/x.py": [f"{d}.x.run"] for d in ("alpha", "beta", "gamma")}
        scope, partition = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout), ROLE_WORDS, KinshipGrouper())
        assert names_of(scope) == ["All files"] and scope.rung == FRONTIER
        assert partition.size("1") == 3

    def test_loose_files_stay_their_own_small_box(self):
        """A last-resort rule is neither counted by the guard nor absorbed by a neighbour."""
        layout = project("Ordering.API", 40) | project("Catalog.API", 40)
        layout["src/Program.cs"] = ["Program", "Program.Main()"]
        scope, partition = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout, "csharp"), ROLE_WORDS, KinshipGrouper())
        assert names_of(scope) == ["Catalog", "Ordering", "Loose files"]
        assert partition.assignment["src/Program.cs"] == "3"

    def test_a_root_of_one_box_plus_loose_files_reads_its_words(self):
        layout = {
            f"pkg/{fmt}_{role}.py": [f"pkg.{fmt}_{role}.{fmt.capitalize()}{role.capitalize()}"]
            for fmt in ("docx", "pdf")
            for role in ("reader", "writer")
        }
        layout["setup.py"] = ["setup.main"]
        scope, _ = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout), ROLE_WORDS, KinshipGrouper())
        assert scope.rung == VOCABULARY

    def test_a_machinery_word_from_the_planner_is_a_role_word(self):
        layout = {
            f"Beacon.{layer}/Endpoints/E{i}.cs": [f"Beacon.{layer}.Endpoints.E{i}"]
            for layer in ("Api", "Application")
            for i in range(2)
        }
        layout |= {
            f"Beacon.{layer}/Teams/T{i}.cs": [f"Beacon.{layer}.Teams.T{i}"]
            for layer in ("Api", "Application", "Domain")
            for i in range(2)
        }
        layout |= {
            f"Beacon.{layer}/Alerts/A{i}.cs": [f"Beacon.{layer}.Alerts.A{i}"]
            for layer in ("Api", "Domain")
            for i in range(2)
        }
        plain = draft_tree(units_from_layout(layout, "csharp"), KinshipGrouper(), 1)
        tailed = draft_tree(units_from_layout(layout, "csharp"), KinshipGrouper(), 1, machinery=("Endpoints",))
        assert "Endpoints" in names_of(scope_of(plain, ROOT_SCOPE_ID))
        assert "Endpoints" not in names_of(scope_of(tailed, ROOT_SCOPE_ID))
        assert tailed.machinery == frozenset({"Endpoints"})

    def test_drafting_is_replaying(self):
        units = units_from_layout(eshop(), "csharp")
        scope, partition = draft_scope(ROOT_SCOPE_ID, units, ROLE_WORDS, KinshipGrouper())
        assert replay(units, scope, ROLE_WORDS).assignment == partition.assignment


class TestLadder:
    def test_a_grouped_component_un_merges_into_its_parts(self):
        spec = draft_tree(units_from_layout(eshop(), "csharp"), KinshipGrouper(), 2)
        child = scope_of(spec, "1")
        assert child.rung == UNMERGE
        assert names_of(child) == ["Ordering", "OrderProcessor"]
        assert [rule.component_id for rule in child.rules] == ["1.1", "1.2"]
        assert rule_of(scope_of(spec, "1"), "1.1").prefixes == (("Ordering",),)

    def test_a_cohesive_component_is_a_leaf_that_says_why(self):
        spec = draft_tree(units_from_layout(eshop(), "csharp"), KinshipGrouper(), 2)
        catalog = scope_of(spec, "2")
        assert catalog.is_leaf and catalog.rung == LEAF
        assert catalog.leaf_reason.startswith("cohesive: 8 units")

    def test_the_guard_absorbs_a_part_too_small_to_stand(self):
        """Two files against fifty-eight: below max(2, 5%), so the un-merge does not fire."""
        layout = project("Ordering.API", 58) | project("OrderProcessor", 2) | project("Catalog.API", 30)
        layout |= project("Basket.API", 20) | project("Identity.API", 20)
        spec = draft_tree(units_from_layout(layout, "csharp"), KinshipGrouper(), 2)
        assert [part.name for part in rule_of(scope_of(spec, ROOT_SCOPE_ID), "1").parts] == [
            "OrderProcessor",
            "Ordering",
        ]
        assert scope_of(spec, "1").is_leaf

    def test_a_large_component_reads_its_own_frontier(self):
        layout = {
            f"pkg/kubelet/{sub}/{sub}_{index}.go": [f"pkg.kubelet.{sub}.{sub}_{index}.Run"]
            for sub in ("images", "volumes", "network", "runtime")
            for index in range(40)
        }
        for sibling in ("proxy", "scheduler", "controller", "apis", "registry"):
            layout |= {
                f"pkg/{sibling}/{sibling}_{index}.go": [f"pkg.{sibling}.{sibling}_{index}.Run"] for index in range(120)
            }
        spec = draft_tree(units_from_layout(layout, "go"), KinshipGrouper(), 2)
        kubelet = next(scope for scope in spec.scopes.values() if scope.rung == SEGMENT)
        assert sorted(names_of(kubelet)) == ["images", "network", "runtime", "volumes"]
        assert rule_of(scope_of(spec, ROOT_SCOPE_ID), kubelet.scope_id).name == "kubelet"

    def _flat(self, per_word: int) -> dict[str, list[str]]:
        layout = {
            f"pkg/big/{fmt}_{part}_{index}.py": [f"pkg.big.{fmt}_{part}_{index}.{fmt.capitalize()}Codec"]
            for fmt in ("docx", "pdf", "pptx")
            for part in ("reader", "writer")
            for index in range(per_word)
        }
        layout |= {f"pkg/other/{index}.py": [f"pkg.other.m{index}.f"] for index in range(3)}
        return layout

    def test_a_large_flat_component_reads_its_words(self):
        spec = draft_tree(units_from_layout(self._flat(23)), KinshipGrouper(), 2)
        big = scope_of(spec, "1")
        assert big.rung == VOCABULARY
        assert names_of(big) == ["Docx", "Pdf", "Pptx"]

    def test_the_leaf_cap_is_a_boundary(self):
        """135 units is a leaf; 136 reads its words."""

        def big_scope(extra: int):
            layout = self._flat(22) | {f"pkg/big/x{i}.py": [f"pkg.big.x{i}.f"] for i in range(extra)}
            return scope_of(draft_tree(units_from_layout(layout), KinshipGrouper(), 2), "1")

        assert 3 * 2 * 22 + 3 == LEAF_CAP
        at_cap = big_scope(3)
        assert at_cap.is_leaf and at_cap.leaf_reason.startswith(f"cohesive: {LEAF_CAP} units")
        assert big_scope(4).rung == VOCABULARY

    def test_a_large_component_nothing_splits_is_an_exhausted_leaf(self):
        layout = {f"pkg/big/m{i}.py": [f"pkg.big.m{i}.Thing"] for i in range(LEAF_CAP + 5)}
        layout |= {f"pkg/other/{index}.py": [f"pkg.other.m{index}.f"] for index in range(3)}
        big = scope_of(draft_tree(units_from_layout(layout), KinshipGrouper(), 2), "1")
        assert big.is_leaf and big.leaf_reason.startswith("exhausted:")

    def test_depth_cap_leaves_deeper_scopes_undrafted(self):
        units = units_from_layout(eshop(), "csharp")
        shallow = draft_tree(units, KinshipGrouper(), 1)
        assert list(shallow.scopes) == [ROOT_SCOPE_ID]
        deep = draft_tree(units, KinshipGrouper(), 2)
        assert set(deep.scopes) == {ROOT_SCOPE_ID, "1", "2", "3", "4", "5"}

    def test_max_depth_below_one_is_rejected(self):
        with pytest.raises(ValueError):
            draft_tree(units_from_layout(eshop(), "csharp"), KinshipGrouper(), 0)


class TestGrouperContract:
    def test_a_grouping_must_cover_every_candidate_once(self):
        class Dropping:
            name = "dropping"

            def group(self, candidates, context):
                return [CandidateGroup(candidate.label, (candidate.key,)) for candidate in candidates[1:]]

        with pytest.raises(ValueError, match="missing"):
            draft_scope(ROOT_SCOPE_ID, units_from_layout(eshop(), "csharp"), ROLE_WORDS, Dropping())

    def test_a_grouper_may_merge_across_words(self):
        """The planner's kind of answer: scopes sharing no word become one component."""

        class Themes:
            name = "themes"

            def group(self, candidates, context):
                keys = tuple(candidate.key for candidate in candidates)
                return [CandidateGroup("Everything", keys, ("shop",))]

        scope, partition = draft_scope(ROOT_SCOPE_ID, units_from_layout(eshop(), "csharp"), ROLE_WORDS, Themes())
        assert names_of(scope) == ["Everything"]
        assert partition.size("1") == len(units_from_layout(eshop(), "csharp"))
        assert "shop" in rule_of(scope, "1").terms

    def test_the_spec_records_which_grouper_drew_it(self):
        assert draft_tree(units_from_layout(eshop(), "csharp"), KinshipGrouper(), 1).grouper == "kinship"


class TestDeterminism:
    def test_the_same_names_draft_the_same_tree(self):
        units = units_from_layout(eshop(), "csharp")
        assert (
            draft_tree(units, KinshipGrouper(), 3).to_dict()
            == draft_tree(list(reversed(units)), KinshipGrouper(), 3).to_dict()
        )
