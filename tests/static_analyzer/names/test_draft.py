"""Drafting: the frontier grouped into components, the ladder below them, and the guard."""

import pytest

from clustering_ids import ROOT_SCOPE_ID
from static_analyzer.clustering.names import (
    AffinityGrouper,
    Candidate,
    CandidateGroup,
    KinshipGrouper,
    ROLE_WORDS,
    draft_scope,
    draft_tree,
    replay,
)
from static_analyzer.clustering.names.draft import (
    BUDGET,
    CAP_SHARE,
    FILES,
    FRONTIER,
    GUARD_SHARE,
    ISLAND,
    LAYERS,
    LEAF,
    LEAF_CAP,
    LEAF_UNITS,
    LOOSE_NAME,
    MIN_LINKS,
    MIN_UNITS,
    ROLE,
    SEGMENT,
    UNMERGE,
    VOCABULARY,
    GroupingContext,
)
from static_analyzer.clustering.names.frontier import BOX
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

    def test_a_layered_root_without_a_grid_draws_its_layers_before_reading_words(self):
        """serilog's shape: every top-level directory is role-named and no feature recurs."""
        layout = project("Serilog", 40, "Core", "Events", "Configuration", "Parsing")
        scope, _ = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout, "csharp"), ROLE_WORDS, AffinityGrouper())
        assert scope.rung == FRONTIER
        assert sorted(names_of(scope)) == ["Configuration", "Core", "Events", "Parsing"]

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

    def test_a_small_component_is_a_leaf_that_says_why(self):
        spec = draft_tree(units_from_layout(eshop(), "csharp"), KinshipGrouper(), 2)
        basket = scope_of(spec, "4")
        assert basket.is_leaf and basket.rung == LEAF
        assert basket.leaf_reason.startswith(f"small: 4 units, at most {LEAF_UNITS}")

    def test_a_component_above_the_leaf_units_reads_its_own_sub_tree(self):
        catalog = scope_of(draft_tree(units_from_layout(eshop(), "csharp"), KinshipGrouper(), 2), "2")
        assert catalog.rung == SEGMENT and names_of(catalog) == ["Apis", "Model"]

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

    def test_a_weak_rule_with_no_sibling_stands_as_its_own_box(self):
        """Two files under a floor of three, linked to nothing: drawn, not folded into the largest."""
        layout = project("Big", 60, "One", "Two") | project("Mid", 6) | project("Tiny", 2)
        layout |= project("Beta", 100) | project("Gamma", 100)
        spec = draft_tree(units_from_layout(layout, "csharp"), AffinityGrouper(), 2)
        assert names_of(scope_of(spec, ROOT_SCOPE_ID)) == ["Beta", "Gamma", "Big", "Mid", "Tiny"]
        big = scope_of(spec, "3")
        assert big.rung == SEGMENT and names_of(big) == ["One", "Two"]

    def test_nesting_starts_above_the_leaf_units(self):
        def component(count: int):
            layout = project("Alpha", count, "One", "Two") | project("Beta", 30) | project("Gamma", 30)
            spec = draft_tree(units_from_layout(layout, "csharp"), AffinityGrouper(), 2)
            alpha = next(rule for rule in scope_of(spec, ROOT_SCOPE_ID).rules if rule.name == "Alpha")
            return scope_of(spec, alpha.component_id)

        nested = component(LEAF_UNITS + 1)
        assert nested.rung == SEGMENT and names_of(nested) == ["One", "Two"]
        leaf = component(LEAF_UNITS)
        assert leaf.is_leaf and leaf.leaf_reason.startswith(f"small: {LEAF_UNITS} units, at most {LEAF_UNITS}")

    def test_a_layered_component_without_a_grid_draws_its_layers(self):
        layout = project("Ordering", 60, "API", "Domain", "Infrastructure") | project("Beta", 40) | project("Gamma", 40)
        ordering = scope_of(draft_tree(units_from_layout(layout, "csharp"), AffinityGrouper(), 2), "1")
        assert ordering.rung == SEGMENT
        assert names_of(ordering) == ["API", "Domain", "Infrastructure"]

    def test_a_layered_component_with_a_grid_draws_its_layers_below_the_cap_and_transposes_above_it(self):
        """A client app organised by layers over features: the layers are its boxes while it reads whole."""

        def client_app(count: int):
            layout = project(
                "ClientApp", count, "Models.Orders", "Services.Order", "Models.Basket", "Services.Basket", "Views"
            )
            layout |= project("Beta", 40) | project("Gamma", 40)
            return scope_of(draft_tree(units_from_layout(layout, "csharp"), AffinityGrouper(), 2), "1")

        layered = client_app(LEAF_CAP)
        assert layered.rung == LAYERS and names_of(layered) == ["Models", "Services", "Views"]
        transposed = client_app(LEAF_CAP + 1)
        assert transposed.rung == SEGMENT and transposed.axis == "transposed"
        assert {"Orders", "Basket"} <= set(names_of(transposed))

    def _flat_feature(self, *class_names: str) -> dict[str, list[str]]:
        layout = {f"pkg/feat/{name.lower()}.py": [f"pkg.feat.{name.lower()}.{name}"] for name in class_names}
        return layout | {f"pkg/other/{index}.py": [f"pkg.other.m{index}.f"] for index in range(3)}

    def test_a_flat_component_groups_its_files_by_their_words(self):
        """What no directory separates, the file names do; what shares no word is loose, never a one-file box."""
        layout = self._flat_feature(
            "RetryPolicy",
            "RetryBuilder",
            "RetryOptions",
            "TimeoutPolicy",
            "TimeoutBuilder",
            "TimeoutOptions",
            "Hedging",
            "Fallback",
            "Misc",
            "Other",
        )
        units = units_from_layout(layout)
        feature = scope_of(draft_tree(units, KinshipGrouper(), 2), "1")
        assert feature.rung == FILES
        assert names_of(feature) == [LOOSE_NAME, "RetryBuilder", "TimeoutBuilder"]
        retry = rule_of(feature, "1.2")
        assert retry.terms == ("retry",) and len(retry.prefixes) == 3
        assert ("pkg", "feat", "retrypolicy", "RetryPolicy") in retry.prefixes
        loose = rule_of(feature, "1.1")
        assert loose.is_fallback_only and loose.fallback_prefixes == (("pkg", "feat"),)
        placed = replay(units, feature, ROLE_WORDS)
        assert placed.size("1.1") == 4 and placed.size("1.2") == 3 and placed.size("1.3") == 3

    def test_a_file_added_to_a_flat_component_follows_its_word(self):
        layout = self._flat_feature("RetryPolicy", "RetryBuilder", "RetryOptions", "TimeoutPolicy", "TimeoutBuilder")
        layout |= self._flat_feature("TimeoutOptions", "Hedging", "Fallback", "Misc", "Other")
        feature = scope_of(draft_tree(units_from_layout(layout), KinshipGrouper(), 2), "1")
        added = units_from_layout({"pkg/feat/retry_extra.py": ["pkg.feat.retry_extra.RetryExtra"]})
        placed = replay(added, feature, ROLE_WORDS)
        assert placed.assignment == {"pkg/feat/retry_extra.py": "1.2"} and placed.placed_by == {
            "pkg/feat/retry_extra.py": "term"
        }

    def test_inside_a_feature_the_roles_are_the_boxes(self):
        """Nine files sharing no word: their head words, a role word each, draw the boxes; one owns its word."""
        layout = self._flat_feature(
            "AlphaStrategy",
            "BetaStrategy",
            "GammaStrategy",
            "DeltaOptions",
            "EpsOptions",
            "ZetaOptions",
            "EtaHandler",
            "ThetaHandler",
            "Iota",
        )
        feature = scope_of(draft_tree(units_from_layout(layout), KinshipGrouper(), 2), "1")
        assert feature.rung == ROLE
        assert names_of(feature) == ["Option", "Strategy", "Handler", LOOSE_NAME]
        assert rule_of(feature, "1.2").terms == ("strategy",)
        added = units_from_layout({"pkg/feat/kappa_strategy.py": ["pkg.feat.kappa_strategy.KappaStrategy"]})
        assert replay(added, feature, ROLE_WORDS).assignment == {"pkg/feat/kappa_strategy.py": "1.2"}

    def _fan(self, *members: str) -> tuple[list, dict]:
        """A fan of converters: the HTML-based ones call the HTML converter, the rest call nobody."""
        layout = self._flat_feature(*members)
        html = {
            name for name in members if name in ("HtmlConverter", "DocxConverter", "EpubConverter", "PptxConverter")
        }
        links = {
            (f"pkg/feat/{a.lower()}.py", f"pkg/feat/{b.lower()}.py"): 2
            for a in sorted(html)
            for b in sorted(html)
            if a < b
        }
        return units_from_layout(layout), links

    def test_a_family_that_talks_to_nobody_else_is_an_island_against_the_rest(self):
        units, links = self._fan(
            "HtmlConverter",
            "DocxConverter",
            "EpubConverter",
            "PptxConverter",
            "PdfConverter",
            "AudioConverter",
            "ImageConverter",
            "ZipConverter",
            "CsvConverter",
        )
        feature = scope_of(draft_tree(units, AffinityGrouper(), 3, links=links), "1")
        assert feature.rung == ISLAND
        assert names_of(feature) == ["Other converters", "DocxConverter"]
        rest, family = feature.rules
        assert len(rest.prefixes) == 5 and rest.fallback_prefixes == (("pkg", "feat"),)
        assert len(family.prefixes) == 4
        placed = replay(units, feature, ROLE_WORDS)
        assert placed.size("1.1") == 5 and placed.size("1.2") == 4
        assert scope_of(draft_tree(units, AffinityGrouper(), 3, links=links), "1.1").is_leaf

    def test_a_family_the_rest_calls_is_a_hub_cut_and_stays_whole(self):
        units, links = self._fan(
            "HtmlConverter",
            "DocxConverter",
            "EpubConverter",
            "PptxConverter",
            "PdfConverter",
            "AudioConverter",
            "ImageConverter",
            "ZipConverter",
            "CsvConverter",
        )
        for name in ("pdfconverter", "audioconverter", "imageconverter"):
            links[("pkg/feat/htmlconverter.py", f"pkg/feat/{name}.py")] = 1
        feature = scope_of(draft_tree(units, AffinityGrouper(), 2, links=links), "1")
        assert feature.is_leaf and "island" in feature.leaf_reason

    def test_a_family_too_small_for_its_scope_is_not_an_island(self):
        units, links = self._fan(
            "HtmlConverter",
            "DocxConverter",
            "PdfConverter",
            "AudioConverter",
            "ImageConverter",
            "ZipConverter",
            "CsvConverter",
            "TextConverter",
            "RtfConverter",
            "XmlConverter",
        )
        feature = scope_of(draft_tree(units, AffinityGrouper(), 2, links=links), "1")
        assert feature.is_leaf

    def test_the_ladder_stops_at_the_leaf_units(self):
        layout = self._flat_feature("AlphaStrategy", "BetaStrategy", "GammaStrategy", "DeltaOptions", "EpsOptions")
        layout |= self._flat_feature("ZetaOptions", "EtaHandler")
        feature = scope_of(draft_tree(units_from_layout(layout), KinshipGrouper(), 2), "1")
        assert feature.is_leaf and feature.leaf_reason.startswith(f"small: {LEAF_UNITS} units")

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
        assert at_cap.rung == FILES and names_of(at_cap) == ["DocxCodec", "PdfCodec", "PptxCodec", LOOSE_NAME]
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
        assert draft_tree(units_from_layout(eshop(), "csharp"), AffinityGrouper(), 1).grouper == "affinity"


def boxes(*names: str) -> list[Candidate]:
    return [Candidate(f"box:{name}", BOX, name, prefixes=((name,),)) for name in names]


def context(
    sizes: dict[str, int], links: dict[tuple[str, str], int], *, floor: int = MIN_UNITS, unit_count: int = 0
) -> GroupingContext:
    return GroupingContext(
        ROOT_SCOPE_ID,
        ROLE_WORDS,
        unit_count or sum(sizes.values()),
        FRONTIER,
        sizes={f"box:{name}": size for name, size in sizes.items()},
        links={tuple(sorted((f"box:{a}", f"box:{b}"))): count for (a, b), count in links.items()},  # type: ignore[misc]
        floor=floor,
    )


def members_of(groups: list[CandidateGroup]) -> dict[str, tuple[str, ...]]:
    return {group.name: tuple(key.removeprefix("box:") for key in group.keys) for group in groups}


class TestAffinityGrouper:
    BIG = tuple(f"box{index}" for index in range(10))

    def test_over_budget_the_smallest_joins_its_closest_sibling_until_nothing_affine_is_left(self):
        sizes = dict.fromkeys(self.BIG, 5) | {"tiny": 2, "small": 3}
        links = {("tiny", "box0"): 3, ("small", "box1"): 3}
        groups = AffinityGrouper().group(boxes(*sizes), context(sizes, links))
        assert len(groups) == BUDGET + 1, "ten big boxes share no link: the fold stops short of the budget"
        assert members_of(groups)["box0"] == ("box0", "tiny")
        assert members_of(groups)["box1"] == ("box1", "small")

    def test_within_budget_only_a_candidate_below_the_floor_folds(self):
        sizes = {"alpha": 5, "beta": 5, "tiny": 2}
        links = {("tiny", "alpha"): 2, ("alpha", "beta"): 9}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links))) == 3
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))
        assert members_of(folded) == {"alpha": ("alpha", "tiny"), "beta": ("beta",)}

    def test_a_hub_is_nobody_s_closest_sibling(self):
        sizes = {"utils": 9, "billing": 5, "tiny": 2} | dict.fromkeys(self.BIG, 5)
        links = {("tiny", "utils"): 3, ("tiny", "billing"): 3} | {("utils", name): 10 for name in self.BIG}
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))
        assert members_of(folded)["billing"] == ("billing", "tiny")

    def test_one_link_is_noise(self):
        sizes = {"alpha": 5, "tiny": 2}
        links = {("tiny", "alpha"): MIN_LINKS - 1}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))) == 2

    def test_the_cap_sends_a_fold_to_the_next_sibling(self):
        sizes = {"big": 6, "mid": 3, "tiny": 2}
        links = {("tiny", "big"): 3, ("tiny", "mid"): 2}
        assert 6 + 2 > CAP_SHARE * 11 >= 3 + 2
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))
        assert members_of(folded) == {"big": ("big",), "mid": ("mid", "tiny")}

    def test_kinship_comes_first_and_a_fold_keeps_every_word(self):
        sizes = {"Ordering": 8, "OrderProcessor": 2, "Basket": 5, "tiny": 2}
        links = {("tiny", "Basket"): 2}
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))
        assert members_of(folded) == {"Ordering": ("Ordering", "OrderProcessor"), "Basket": ("Basket", "tiny")}
        assert next(group.terms for group in folded if group.name == "Ordering") == ("order",)


class TestGroupingContractErrors:
    def test_an_empty_group_is_refused_like_a_missing_key(self):
        class Empty:
            name = "empty"

            def group(self, candidates, context):
                return [CandidateGroup("all", tuple(c.key for c in candidates)), CandidateGroup("none", ())]

        with pytest.raises(ValueError, match="empty=\\['none'\\]"):
            draft_tree(units_from_layout(eshop(), "csharp"), Empty(), 1)


class TestDeterminism:
    def test_the_same_names_draft_the_same_tree(self):
        units = units_from_layout(eshop(), "csharp")
        assert (
            draft_tree(units, KinshipGrouper(), 3).to_dict()
            == draft_tree(list(reversed(units)), KinshipGrouper(), 3).to_dict()
        )

    def test_the_same_names_and_links_fold_the_same_tree(self):
        layout = eshop() | project("Tiny.API", 2)
        units = units_from_layout(layout, "csharp")
        paths = sorted(layout)
        links = {(paths[index], paths[-1 - index]): 2 + index % 3 for index in range(len(paths) // 2)}
        forward = draft_tree(units, AffinityGrouper(), 3, links=links).to_dict()
        backward = draft_tree(list(reversed(units)), AffinityGrouper(), 3, links=dict(reversed(links.items())))
        assert forward == backward.to_dict()
