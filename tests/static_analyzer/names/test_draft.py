"""Drafting: the frontier grouped into components, the ladder below them, and the guard."""

import re
from dataclasses import replace

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
    LIMIT,
    OTHER_NAME,
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
    PARTNER_SHARE,
    ROLE,
    SEGMENT,
    UNMERGE,
    GroupingContext,
)
from static_analyzer.clustering.names.frontier import BOX, LOOSE
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
        assert names_of(scope) == ["Ordering.API", "Catalog.API", "WebhookClient", "Basket.API", "PaymentProcessor"]
        ordering = rule_of(scope, "1")
        assert ordering.prefixes == (("src", "OrderProcessor"), ("src", "Ordering.API"), ("src", "Ordering.Domain"))
        assert ordering.terms == ("order",)
        assert [part.name for part in ordering.parts] == ["OrderProcessor", "Ordering.API", "Ordering.Domain"]
        assert partition.size("1") == 19
        assert rule_of(scope, "5").parts == ()

    def test_ids_follow_size_then_name(self):
        scope, _ = draft_scope(ROOT_SCOPE_ID, units_from_layout(eshop(), "csharp"), ROLE_WORDS, KinshipGrouper())
        assert [rule.component_id for rule in scope.rules] == ["1", "2", "3", "4", "5"]

    def test_the_root_frontier_takes_no_guard(self):
        """A two-file directory is a box; small boxes are the grouper's to merge, not hidden."""
        layout = eshop() | project("Tiny.API", 2)
        scope, _ = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout, "csharp"), ROLE_WORDS, KinshipGrouper())
        assert "Tiny.API" in names_of(scope)

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
        assert partition.assignment["Beacon.Application/Bootstrap.cs"] == by_name["Beacon.Application (residual)"]

    def test_one_box_at_the_root_falls_through_to_its_files(self):
        layout = {
            f"converters/{fmt}_{index}.py": [
                f"converters.{fmt}_{index}.{fmt.capitalize()}Converter",
                f"converters.{fmt}_{index}.convert",
            ]
            for fmt in ("docx", "pdf", "pptx")
            for index in range(2)
        }
        scope, partition = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout), ROLE_WORDS, KinshipGrouper())
        assert scope.rung == FILES
        assert names_of(scope) == ["docx_0", "pdf_0", "pptx_0"]
        assert all(partition.size(rule.component_id) == 2 for rule in scope.rules)

    def test_what_no_rule_claims_gets_a_bucket(self):
        layout = {
            f"converters/{fmt}_{role}.py": [f"converters.{fmt}_{role}.{fmt.capitalize()}{role.capitalize()}"]
            for fmt in ("docx", "pdf")
            for role in ("reader", "writer")
        }
        layout["converters/base.py"] = ["converters.base.Converter"]
        scope, partition = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout), ROLE_WORDS, KinshipGrouper())
        loose = next(rule for rule in scope.rules if rule.is_fallback_only)
        assert partition.assignment["converters/base.py"] == loose.component_id
        assert partition.placed_by["converters/base.py"] == "fallback"

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
        assert names_of(scope) == ["Catalog.API", "Ordering.API", "Loose files in src"]
        assert partition.assignment["src/Program.cs"] == "3"

    def test_a_root_of_one_box_plus_loose_files_reads_its_words(self):
        layout = {
            f"pkg/{fmt}_{role}.py": [f"pkg.{fmt}_{role}.{fmt.capitalize()}{role.capitalize()}"]
            for fmt in ("docx", "pdf")
            for role in ("reader", "writer")
        }
        layout["setup.py"] = ["setup.main"]
        scope, _ = draft_scope(ROOT_SCOPE_ID, units_from_layout(layout), ROLE_WORDS, KinshipGrouper())
        assert scope.rung == FILES and names_of(scope) == ["docx_reader", "pdf_reader", "Loose files"]

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
        assert names_of(child) == ["Ordering.API", "Ordering.Domain", "OrderProcessor"]
        assert [rule.component_id for rule in child.rules] == ["1.1", "1.2", "1.3"]
        assert rule_of(scope_of(spec, "1"), "1.1").prefixes == (("src", "Ordering.API"),)

    def test_un_merge_keeps_the_parts_kinship_would_merge_again(self):
        child = scope_of(draft_tree(units_from_layout(eshop(), "csharp"), AffinityGrouper(), 2), "1")
        assert child.rung == UNMERGE and names_of(child) == ["Ordering.API", "Ordering.Domain", "OrderProcessor"]

    def test_un_merge_folds_the_parts_toward_the_budget(self):
        """Twelve parts one word merged at the root fold along their own links inside the box."""
        suffixes = "ABCDEFGHIJKL"
        others = "Basket Catalog Identity Payment Shipping Webhooks Search Pricing Billing Tax Loyalty Reviews Wishlist"
        assert len(others.split()) > len(suffixes), "a word half the siblings carry is ubiquitous, not kinship"
        layout: dict[str, list[str]] = {}
        for name in others.split():
            layout |= project(name, 20)
        for suffix in suffixes:
            layout |= project(f"Order{suffix}", 6)
        links = {("src/OrderA//OrderAType0.cs", f"src/Order{suffix}//Order{suffix}Type0.cs"): 3 for suffix in "JKL"}
        spec = draft_tree(units_from_layout(layout, "csharp"), AffinityGrouper(), 2, links=links)
        assert len(rule_of(scope_of(spec, ROOT_SCOPE_ID), "1").parts) == len(suffixes)
        order = scope_of(spec, "1")
        assert order.rung == UNMERGE and len(order.components) == BUDGET
        assert sorted(len(rule.prefixes) for rule in order.rules) == [1] * (BUDGET - 1) + [4]

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
            "Ordering.API",
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
                "ClientApp", count, "Models/Orders", "Services/Order", "Models/Basket", "Services/Basket", "Views"
            )
            layout |= project("Beta", 40) | project("Gamma", 40)
            return scope_of(draft_tree(units_from_layout(layout, "csharp"), AffinityGrouper(), 2), "1")

        layered = client_app(LEAF_CAP)
        assert layered.rung == LAYERS and names_of(layered) == ["Models", "Services", "Views"]
        transposed = client_app(LEAF_CAP + 1)
        assert transposed.rung == SEGMENT and transposed.axis == "transposed"
        assert {"Orders", "Basket"} <= set(names_of(transposed))

    @staticmethod
    def _module(class_name: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()

    def _flat_feature(self, *class_names: str) -> dict[str, list[str]]:
        layout = {
            f"pkg/feat/{self._module(name)}.py": [f"pkg.feat.{self._module(name)}.{name}"] for name in class_names
        }
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
        assert names_of(feature) == [LOOSE_NAME, "retry_options", "timeout_options"]
        retry = rule_of(feature, "1.2")
        assert retry.terms == ("retry",) and len(retry.prefixes) == 3
        assert ("pkg", "feat", "retry_policy.py") in retry.prefixes
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
            (f"pkg/feat/{self._module(a)}.py", f"pkg/feat/{self._module(b)}.py"): 2
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
        assert names_of(feature) == ["Other converters", "docx_converter"]
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
        for name in ("pdf_converter", "audio_converter", "image_converter"):
            links[("pkg/feat/html_converter.py", f"pkg/feat/{name}.py")] = 1
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

    def test_a_large_flat_component_groups_its_files_by_their_words(self):
        """Above the leaf cap as below it: the file names, not a separate vocabulary, draw the boxes."""

        def big_scope(extra: int):
            layout = self._flat(22) | {f"pkg/big/x{i}.py": [f"pkg.big.x{i}.f"] for i in range(extra)}
            return scope_of(draft_tree(units_from_layout(layout), KinshipGrouper(), 2), "1")

        assert 3 * 2 * 22 + 3 == LEAF_CAP
        for extra in (3, 4):
            scope = big_scope(extra)
            assert scope.rung == FILES
            assert names_of(scope) == ["docx_reader_0", "pdf_reader_0", "pptx_reader_0", LOOSE_NAME]

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
        links=_undirected(links),
        calls={(f"box:{a}", f"box:{b}"): count for (a, b), count in links.items()},
        floor=floor,
    )


def _undirected(links: dict[tuple[str, str], int]) -> dict[tuple[str, str], int]:
    """Both directions of a pair summed, the way the production context counts them."""
    summed: dict[tuple[str, str], int] = {}
    for (a, b), count in links.items():
        key = (min(f"box:{a}", f"box:{b}"), max(f"box:{a}", f"box:{b}"))
        summed[key] = summed.get(key, 0) + count
    return summed


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
        links = {("alpha", "tiny"): 2, ("alpha", "beta"): 9}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links))) == 3
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))
        assert members_of(folded) == {"alpha": ("alpha", "tiny"), "beta": ("beta",)}

    def test_a_small_candidate_that_only_calls_others_is_an_application_and_stays(self):
        """PaymentProcessor calls the bus; nothing calls it. It is a service, not the bus's helper."""
        sizes = {"bus": 5, "orders": 5, "payment": 2}
        links = {("payment", "bus"): 6, ("orders", "bus"): 6}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))) == 3

    def test_a_hub_is_nobody_s_closest_sibling(self):
        sizes = {"utils": 9, "billing": 5, "tiny": 2} | dict.fromkeys(self.BIG, 5)
        links = {("tiny", "utils"): 3, ("tiny", "billing"): 3} | {("utils", name): 10 for name in self.BIG}
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))
        assert members_of(folded)["billing"] == ("billing", "tiny")

    def test_a_hub_neither_absorbs_a_sibling_nor_folds_into_one(self):
        """An event bus every service calls is drawn as its own box, and so is the smallest service."""
        sizes = {"bus": 4, "orders": 30, "catalog": 20, "basket": 6, "payment": 3, "webhooks": 12}
        links = {(name, "bus"): 6 for name in ("orders", "catalog", "basket", "payment", "webhooks")}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))) == len(sizes)
        two_callers = {("orders", "bus"): 6, ("payment", "bus"): 6}
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, two_callers, floor=5))
        assert members_of(folded)["orders"] == ("orders", "bus"), "shared by two, the bus joins the larger caller"

    def test_a_fold_takes_the_sibling_it_links_to_most(self):
        """Fifteen links to a busy sibling outweigh three to a quiet one."""
        sizes = {"web": 20, "hybrid": 6, "components": 3, "orders": 20}
        links = {("web", "components"): 15, ("hybrid", "components"): 3, ("web", "orders"): 40}
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))
        assert members_of(folded)["web"] == ("web", "components")

    def test_one_link_is_noise(self):
        sizes = {"alpha": 5, "tiny": 2}
        links = {("tiny", "alpha"): MIN_LINKS - 1}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))) == 2

    def test_the_cap_sends_a_fold_to_the_next_sibling(self):
        sizes = {"big": 6, "mid": 3, "tiny": 2}
        links = {("big", "tiny"): 3, ("mid", "tiny"): 2}
        assert 6 + 2 > CAP_SHARE * 11 >= 3 + 2
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))
        assert members_of(folded) == {"big": ("big",), "mid": ("mid", "tiny")}

    def test_kinship_comes_first_and_a_fold_keeps_every_word(self):
        sizes = {"Ordering": 8, "OrderProcessor": 2, "Basket": 5, "tiny": 2}
        links = {("Basket", "tiny"): 2}
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=3))
        assert members_of(folded) == {"Ordering": ("Ordering", "OrderProcessor"), "Basket": ("Basket", "tiny")}
        assert next(group.terms for group in folded if group.name == "Ordering") == ("order",)


class TestPlacementByRole:
    """Small candidates are placed by what the calls say about them, before any fold."""

    def test_a_helper_called_by_one_sibling_goes_inside_it(self):
        sizes = {"cli": 20, "engine": 20, "core": 3}
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, {("cli", "core"): 4}, floor=5))
        assert members_of(folded)["cli"] == ("cli", "core")

    def test_a_hub_called_by_many_stays_whatever_its_size(self):
        sizes = {"bus": 3, "a": 20, "b": 20, "c": 20, "d": 20}
        links = {(name, "bus"): 3 for name in "abcd"}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))) == 5

    def test_a_hub_under_three_units_joins_the_loose_files(self):
        sizes = {"log": 2, "a": 20, "b": 20, "c": 20, "d": 20}
        links = {(name, "log"): 3 for name in "abcd"}
        candidates = boxes(*sizes) + [Candidate("loose:", LOOSE, "", fallback_prefixes=((),))]
        ctx = context(sizes | {"loose:": 0}, links, floor=5)
        ctx.sizes["loose:"] = 2
        groups = {group.name: group.keys for group in AffinityGrouper().group(candidates, ctx)}
        assert groups["Loose files"] == ("loose:", "box:log")

    def test_a_project_root_is_never_a_helper(self):
        sizes = {"core": 40, "extensions": 3}
        ctx = context(sizes, {("core", "extensions"): 6}, floor=5)
        assert len(AffinityGrouper().group(boxes(*sizes), replace(ctx, projects=frozenset({"box:extensions"})))) == 2

    def test_shared_by_three_stays_and_by_two_joins_the_larger(self):
        sizes = {"a": 30, "b": 20, "c": 20, "shared": 3}
        by_three = {(name, "shared"): 2 for name in "abc"}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, by_three, floor=5))) == 4
        by_two = {("a", "shared"): 2, ("b", "shared"): 2}
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, by_two, floor=5))
        assert members_of(folded)["a"] == ("a", "shared")

    def test_a_hub_calling_a_small_sibling_does_not_own_it(self):
        """The event bus dispatches to every service's handlers; a service is its subscriber, not its helper."""
        sizes = {"bus": 11, "a": 30, "b": 30, "c": 30, "d": 30, "basket": 4}
        links = {(name, "bus"): 5 for name in "abcd"} | {("bus", "basket"): 6, ("basket", "bus"): 3}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))) == len(sizes)

    def test_loose_files_tests_and_samples_own_no_helper(self):
        sizes = {"lib": 30, "samples": 20, "printer": 3}
        links = {("samples", "printer"): 9}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))) == 3

    def test_a_candidate_with_no_links_joins_the_loose_files(self):
        sizes = {"a": 30, "b": 20, "stray": 2}
        candidates = boxes(*sizes) + [Candidate("loose:", LOOSE, "", fallback_prefixes=((),))]
        ctx = context(sizes, {("a", "b"): 5}, floor=5)
        ctx.sizes["loose:"] = 1
        groups = {group.name: group.keys for group in AffinityGrouper().group(candidates, ctx)}
        assert groups["Loose files"] == ("loose:", "box:stray")

    def test_two_one_link_callers_are_noise_not_a_shared_helper(self):
        sizes = {"a": 30, "b": 20, "helper": 3}
        links = {("a", "helper"): 1, ("b", "helper"): 1}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))) == 3

    def test_a_helper_stays_when_its_owner_cannot_take_it_under_the_cap(self):
        sizes = {"big": 58, "helper": 4, "other": 38}
        links = {("big", "helper"): 6}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))) == 3

    def test_what_three_siblings_call_is_shared_whoever_they_are(self):
        """Two hubs and one service call ``data``: it is shared, not the service's helper."""
        services = tuple(f"svc{index}" for index in range(6))
        sizes = dict.fromkeys(services, 30) | {"bus": 30, "core": 30, "data": 4}
        links = {(name, hub): 5 for name in services for hub in ("bus", "core")}
        links |= {("bus", "data"): 2, ("core", "data"): 2, ("svc0", "data"): 2}
        groups = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))
        assert members_of(groups)["data"] == ("data",)
        one_service = {key: count for key, count in links.items() if key[1] != "data"} | {("svc0", "data"): 2}
        folded = AffinityGrouper().group(boxes(*sizes), context(sizes, one_service, floor=5))
        assert members_of(folded)["svc0"] == (
            "svc0",
            "data",
        ), "called by one service alone, it is that service's helper"

    def test_a_small_hub_or_application_stands_at_the_files_rung(self):
        """A one-file event bus every feature calls is a box, not a loose file."""
        layout = {f"pkg/feat/{name}.py": [f"pkg.feat.{name}.{name.capitalize()}"] for name in ("bus",)}
        for feature in ("alpha", "beta", "gamma", "delta"):
            layout |= {f"pkg/feat/{feature}_{part}.py": [f"pkg.feat.{feature}_{part}.X"] for part in ("a", "b", "c")}
        layout |= {f"pkg/other/{index}.py": [f"pkg.other.m{index}.f"] for index in range(3)}
        links = {(f"pkg/feat/{feature}_a.py", "pkg/feat/bus.py"): 3 for feature in ("alpha", "beta", "gamma", "delta")}
        scope = scope_of(draft_tree(units_from_layout(layout), AffinityGrouper(), 2, links=links), "1")
        assert scope.rung == FILES and "bus" in names_of(scope)


class TestBudgetAndLimit:
    def test_over_the_budget_a_real_component_folds_only_into_a_dominant_partner(self):
        """Webhooks must not join Catalog on the two links a shared helper brought along."""
        sizes = dict.fromkeys((f"s{i}" for i in range(9)), 20) | {"webhooks": 20, "bus": 10}
        links = {(name, "bus"): 30 for name in sizes if name != "bus"} | {("webhooks", "s0"): 2}
        groups = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))
        assert len(groups) == len(sizes), "the bus is a hub and two links out of thirty-two are not a home"

    def test_a_small_candidate_joins_only_a_home_carrying_half_of_the_links_it_could_follow(self):
        """Both call the hub most; ``helper`` sends most of the rest to one box, ``shared`` spreads it."""
        big = tuple(f"box{index}" for index in range(9))
        sizes = dict.fromkeys(big, 20) | {"core": 5, "shared": 4, "helper": 4}
        links = {(name, "core"): 10 for name in big} | {("shared", "core"): 20, ("helper", "core"): 20}
        links |= {(name, "shared"): 2 for name in big[:4]}
        links |= {("box0", "helper"): 6, ("box1", "helper"): 2, ("box2", "helper"): 2}
        groups = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))
        assert members_of(groups)["box0"] == ("box0", "helper")
        assert members_of(groups)["shared"] == ("shared",), "no box carries half of what it could follow"

    def test_two_small_candidates_join_only_on_a_real_share_of_the_links(self):
        """Kafka sends two fifths of its links to the small event bus and joins it; topology and data
        send each other a sliver beside hundreds to the hubs, and folding them would start a bag."""
        hubs = tuple(f"hub{index}" for index in range(6))
        sizes = dict.fromkeys(hubs, 30) | {"topology": 8, "data": 6, "eventbus": 9, "kafka": 5}
        links = {(a, b): 10 for a in hubs for b in hubs if a != b}
        links |= {("topology", hub): 50 for hub in hubs} | {("data", hub): 40 for hub in hubs}
        links |= {("topology", "data"): 12, ("hub0", "data"): 2, ("hub1", "data"): 2}
        links |= {("kafka", hub): 5 for hub in hubs} | {("kafka", "eventbus"): 20}
        links |= {("eventbus", hub): 3 for hub in hubs} | {("hub0", "eventbus"): 2, ("hub1", "eventbus"): 2}
        assert 12 < PARTNER_SHARE * (6 * 50 + 12) and 20 >= PARTNER_SHARE * (6 * 5 + 20)
        groups = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=10))
        assert members_of(groups)["eventbus"] == ("eventbus", "kafka")
        assert members_of(groups)["topology"] == ("topology",) and members_of(groups)["data"] == ("data",)

    def test_a_candidate_under_three_units_follows_its_links_wherever_they_lead(self):
        big = tuple(f"box{index}" for index in range(9))
        sizes = dict.fromkeys(big, 20) | {"core": 5, "shared": 2}
        links = {(name, "core"): 10 for name in big} | {(name, "shared"): 2 for name in big[:3]}
        groups = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))
        assert members_of(groups)["box0"] == ("box0", "shared")

    def test_a_consumer_takes_nothing_over_the_budget(self):
        sizes = dict.fromkeys((f"s{i}" for i in range(9)), 20) | {"samples": 20, "printer": 6}
        links = {("samples", "printer"): 12}
        groups = AffinityGrouper().group(boxes(*sizes), context(sizes, links, floor=5))
        assert len(groups) == len(sizes)

    def test_the_grouper_may_return_more_than_the_limit_and_the_ladder_pools_the_smallest(self):
        """The limit is the ladder's, not the grouper's: eighteen packages sharing no link draw as fifteen."""
        sizes = {f"pkg{i:02d}": 30 - i for i in range(18)}
        assert len(AffinityGrouper().group(boxes(*sizes), context(sizes, {}, floor=5))) == 18
        layout: dict[str, list[str]] = {}
        for index in range(18):
            layout |= project(f"Pkg{index:02d}", 30 - index)
        root = scope_of(draft_tree(units_from_layout(layout, "csharp"), AffinityGrouper(), 1), ROOT_SCOPE_ID)
        assert len(root.rules) == LIMIT
        pooled = next(rule for rule in root.rules if rule.name == OTHER_NAME)
        assert sorted(part.name for part in pooled.parts) == [f"Pkg{index}" for index in (14, 15, 16, 17)]

    def test_over_the_limit_the_pool_takes_the_smallest_hubs_too(self):
        """Sixteen packages call two three-file hubs: the pool takes the hubs and the two smallest packages."""
        layout: dict[str, list[str]] = {}
        for index in range(16):
            layout |= project(f"Pkg{index:02d}", 20 + index)
        layout |= project("Bus", 3) | project("Log", 3)
        links = {
            (f"src/Pkg{index:02d}//Pkg{index:02d}Type0.cs", f"src/{hub}//{hub}Type0.cs"): 3
            for index in range(16)
            for hub in ("Bus", "Log")
        }
        spec = draft_tree(units_from_layout(layout, "csharp"), AffinityGrouper(), 1, links=links)
        root = scope_of(spec, ROOT_SCOPE_ID)
        assert len(root.rules) == LIMIT
        pooled = next(rule for rule in root.rules if rule.name == OTHER_NAME)
        assert sorted(part.name for part in pooled.parts) == ["Bus", "Log", "Pkg00", "Pkg01"]

    def test_the_pool_leaves_room_for_the_loose_files(self):
        """Sixteen packages plus a root-level file: fourteen boxes, the pool and the loose files make fifteen."""
        layout: dict[str, list[str]] = {"src/Program.cs": ["Program"]}
        for index in range(16):
            layout |= project(f"Pkg{index:02d}", 30 + index)
        root = scope_of(draft_tree(units_from_layout(layout, "csharp"), AffinityGrouper(), 1), ROOT_SCOPE_ID)
        assert len(root.rules) == LIMIT
        assert [rule.name for rule in root.rules if rule.is_fallback_only] == ["Loose files in src"]


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
