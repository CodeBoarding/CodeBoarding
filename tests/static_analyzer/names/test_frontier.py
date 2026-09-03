"""The walk over synthetic layouts shaped like the rulers it was measured on."""

from static_analyzer.clustering.names import ROLE_WORDS, Trie, walk
from static_analyzer.clustering.names.frontier import BOX, FEATURE, LOOSE, NEARLY_ALL, RESIDUAL, ROLE_SHARE, SHARE
from tests.static_analyzer.names.conftest import units_from_layout


def keys(frontier) -> list[str]:
    return [candidate.key for candidate in frontier.candidates]


def project(name: str, count: int, *subdirs: str) -> dict[str, list[str]]:
    """A C#-shaped project: ``count`` single-type files spread over ``subdirs``."""
    layout: dict[str, list[str]] = {}
    for index in range(count):
        sub = subdirs[index % len(subdirs)] if subdirs else ""
        prefix = f"{name}.{sub}" if sub else name
        layout[f"src/{name}/{sub}/{name.split('.')[0]}Type{index}.cs"] = [
            f"{prefix}.{name.split('.')[0]}Type{index}",
            f"{prefix}.{name.split('.')[0]}Type{index}.Run()",
        ]
    return layout


class TestFeatureShapedRoot:
    def test_each_feature_directory_is_a_box(self):
        layout = (
            project("Catalog.API", 6, "Model", "Apis") | project("Basket.API", 4, "Model") | project("Identity.API", 5)
        )
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        assert frontier.axis == "structural"
        assert sorted(keys(frontier)) == ["box:Basket", "box:Catalog", "box:Identity"]

    def test_a_dotted_directory_is_one_scope_with_role_children(self):
        """``Ordering.API`` and ``Ordering.Domain`` nest under ``Ordering``, which is never split."""
        layout = (
            project("Ordering.API", 8, "Apis", "Application")
            | project("Ordering.Domain", 4)
            | project("Catalog.API", 4)
        )
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        assert sorted(keys(frontier)) == ["box:Catalog", "box:Ordering"]

    def test_a_dominant_feature_directory_is_opened(self):
        layout = {
            f"django/contrib/{app}/{mod}.py": [f"django.contrib.{app}.{mod}.f"]
            for app in ("admin", "auth", "gis")
            for mod in ("a", "b", "c")
        }
        layout |= {
            f"django/{pkg}/{mod}.py": [f"django.{pkg}.{mod}.f"] for pkg in ("forms", "views") for mod in ("a", "b", "c")
        }
        frontier = walk(Trie(units_from_layout(layout)), ROLE_WORDS)
        assert "opened django.contrib (9 units)" in frontier.notes
        assert sorted(keys(frontier)) == [
            "box:django.contrib.admin",
            "box:django.contrib.auth",
            "box:django.contrib.gis",
            "box:django.forms",
            "box:django.views",
        ]

    def test_a_layout_word_is_stepped_through(self):
        layout = project("Catalog.API", 3) | project("Basket.API", 3)
        layout = {f"src/{path}": [f"src.{name}" for name in names] for path, names in layout.items()}
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        assert sorted(keys(frontier)) == ["box:src.Basket", "box:src.Catalog"]

    def test_a_child_holding_nearly_everything_is_stepped_through_whatever_its_name(self):
        layout = {
            f"app/{feature}/{mod}.py": [f"app.{feature}.{mod}.f"]
            for feature in ("billing", "catalog", "search")
            for mod in ("a", "b", "c")
        }
        layout["tools/x.py"] = ["tools.x.f"]
        frontier = walk(Trie(units_from_layout(layout)), ROLE_WORDS)
        assert "box:app" not in keys(frontier)
        assert {"box:app.billing", "box:app.catalog", "box:app.search"} <= set(keys(frontier))

    def test_a_role_named_child_holding_a_share_is_a_box_not_a_way_in(self):
        """eShop's ClientApp is a box even though its children are layers with recurring features."""
        layout = project(
            "ClientApp", 30, "Models.Orders", "Services.Order", "Models.Basket", "Services.Basket", "Views"
        )
        layout |= project("Ordering.API", 20, "Apis") | project("Basket.API", 20, "Apis")
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        assert sorted(keys(frontier)) == ["box:Basket", "box:ClientApp", "box:Ordering"]

    def test_one_unit_directories_and_root_files_are_loose(self):
        layout = {
            "pkg/a/x.py": ["pkg.a.x.f"],
            "pkg/b/y.py": ["pkg.b.y.f", "pkg.b.y.g"],
            "pkg/b/z.py": ["pkg.b.z.f"],
            "setup.py": ["setup.main"],
        }
        frontier = walk(Trie(units_from_layout(layout)), ROLE_WORDS)
        loose = {candidate.key for candidate in frontier.candidates if candidate.kind == LOOSE}
        assert loose == {"loose:", "loose:pkg"}
        assert "box:pkg.b" in keys(frontier)
        assert "box:pkg.a" not in keys(frontier)


class TestThresholds:
    def test_a_feature_child_is_opened_at_the_share_and_boxed_just_below_it(self):
        def layout(dominant: int) -> dict[str, list[str]]:
            out = {
                f"top/big/{sub}/{i}.py": [f"top.big.{sub}.m{i}.f"]
                for sub in ("alpha", "beta")
                for i in range(dominant // 2)
            }
            out |= {f"top/other{j}/{i}.py": [f"top.other{j}.m{i}.f"] for j in range(4) for i in range(2)}
            return out

        total = 8
        opened = walk(Trie(units_from_layout(layout(round(SHARE * (total + 8) / (1 - SHARE)) + 1))), ROLE_WORDS)
        assert any(note.startswith("opened top.big") for note in opened.notes)
        boxed = walk(Trie(units_from_layout(layout(2))), ROLE_WORDS)
        assert "box:top.big" in keys(boxed)

    def test_a_node_is_layered_at_the_role_share_and_not_below_it(self):
        def layout(role_units: int, feature_units: int) -> dict[str, list[str]]:
            out = {
                f"X/{layer}/Thing/{i}.cs": [f"X.{layer}.Thing.T{i}"]
                for layer in ("Api", "Domain", "Infrastructure")
                for i in range(role_units)
            }
            out |= {f"X/Billing/{i}.cs": [f"X.Billing.B{i}"] for i in range(feature_units)}
            return out

        layered = walk(Trie(units_from_layout(layout(2, 4), "csharp")), ROLE_WORDS)
        assert any("role-named children" in note for note in layered.notes)
        plain = walk(Trie(units_from_layout(layout(2, 5), "csharp")), ROLE_WORDS)
        assert not any("role-named children" in note for note in plain.notes)
        assert 6 / 10 >= ROLE_SHARE > 6 / 11

    def test_a_child_is_stepped_through_at_nearly_all_and_boxed_below_it(self):
        def layout(app_units: int) -> dict[str, list[str]]:
            out = {f"app/{f}/{i}.py": [f"app.{f}.m{i}.f"] for f in ("billing", "search") for i in range(app_units // 2)}
            out |= {f"tools/t{i}.py": [f"tools.t{i}.f"] for i in range(2)}
            return out

        assert "box:app" not in keys(walk(Trie(units_from_layout(layout(8))), ROLE_WORDS))
        assert "box:app" in keys(walk(Trie(units_from_layout(layout(6))), ROLE_WORDS))
        assert 8 / 10 >= NEARLY_ALL > 6 / 8

    def test_a_nearly_all_child_is_stepped_through_before_its_role_named_siblings_can_layer_the_node(self):
        layout = {f"app/{f}/{m}.py": [f"app.{f}.{m}.run"] for f in ("billing", "catalog", "search") for m in "abcdef"}
        layout |= {f"tests/t{i}.py": [f"tests.t{i}.f"] for i in range(2)}
        layout |= {f"docs/d{i}.py": [f"docs.d{i}.f"] for i in range(2)}
        frontier = walk(Trie(units_from_layout(layout)), ROLE_WORDS)
        assert {"box:app.billing", "box:app.catalog", "box:app.search", "box:tests", "box:docs"} <= set(keys(frontier))


class TestLayeredRoot:
    LAYERS = ("Application", "Domain", "Infrastructure", "Contracts")

    def _beacon(self) -> dict[str, list[str]]:
        layout: dict[str, list[str]] = {}
        for feature in ("Incidents", "Escalation", "Teams"):
            for layer in self.LAYERS[:3]:
                for index in range(2):
                    layout[f"Beacon.{layer}/{feature}/{feature}{index}.cs"] = [
                        f"Beacon.{layer}.{feature}.{feature}Thing{index}"
                    ]
        layout["Beacon.Contracts/Dtos/TeamDto.cs"] = ["Beacon.Contracts.Dtos.TeamDto"]
        layout["Beacon.Application/IncidentResolvedMetricsHandler.cs"] = [
            "Beacon.Application.IncidentResolvedMetricsHandler",
            "Beacon.Application.IncidentResolvedMetricsHandler.Handle()",
        ]
        return layout

    def test_recurring_features_transpose_the_layers(self):
        frontier = walk(Trie(units_from_layout(self._beacon(), "csharp")), ROLE_WORDS)
        assert frontier.axis == "transposed"
        features = {candidate.label: candidate for candidate in frontier.candidates if candidate.kind == FEATURE}
        assert set(features) == {"Incidents", "Escalation", "Teams"}
        assert features["Incidents"].terms == ("incident",)
        assert ("Beacon", "Domain", "Incidents") in features["Incidents"].prefixes
        residuals = [candidate.key for candidate in frontier.candidates if candidate.kind == RESIDUAL]
        assert residuals == [
            f"residual:Beacon.{layer}" for layer in ("Application", "Contracts", "Domain", "Infrastructure")
        ]

    def test_features_recur_through_role_named_directories(self):
        layout: dict[str, list[str]] = {}
        for layer, role in (("Application", "Handlers"), ("Infrastructure", "Repositories"), ("Domain", "Entities")):
            for feature in ("Orders", "Customers"):
                for index in range(2):
                    layout[f"Shop.{layer}/{role}/{feature}/{feature}{index}.cs"] = [
                        f"Shop.{layer}.{role}.{feature}.{feature}Thing{index}"
                    ]
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        features = {candidate.label: candidate for candidate in frontier.candidates if candidate.kind == FEATURE}
        assert set(features) == {"Orders", "Customers"}
        assert ("Shop", "Application", "Handlers", "Orders") in features["Orders"].prefixes

    def test_a_product_name_on_every_feature_directory_is_not_the_feature(self):
        layout: dict[str, list[str]] = {}
        for layer in self.LAYERS[:3]:
            for feature in ("BeaconIncidents", "BeaconEscalation", "BeaconTeams"):
                for index in range(2):
                    layout[f"Beacon.{layer}/{feature}/{feature}{index}.cs"] = [f"Beacon.{layer}.{feature}.Thing{index}"]
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        assert frontier.axis == "transposed"
        features = sorted(candidate.terms[0] for candidate in frontier.candidates if candidate.kind == FEATURE)
        assert features == ["escalation", "incident", "team"]

    def test_a_feature_under_one_layer_only_is_not_a_feature(self):
        """Two same-stem directories under one layer are not a grid."""
        layout = self._beacon()
        layout["Beacon.Domain/Alerts/Alert.cs"] = ["Beacon.Domain.Alerts.Alert"]
        layout["Beacon.Domain/AlertRules/AlertRule.cs"] = ["Beacon.Domain.AlertRules.AlertRule"]
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        assert not any(candidate.label == "Alerts" for candidate in frontier.candidates)

    def test_two_layered_children_sharing_a_feature_word_get_distinct_candidates(self):
        layout: dict[str, list[str]] = {}
        for project in ("Orders", "Billing"):
            for layer in ("Application", "Domain", "Infrastructure"):
                for feature in ("Customers", "Payments"):
                    for i in range(2):
                        layout[f"{project}/{layer}/{feature}/{i}.cs"] = [f"{project}.{layer}.{feature}.T{i}"]
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        features = sorted(candidate.key for candidate in frontier.candidates if candidate.kind == FEATURE)
        assert features == [
            "feature:Billing:customer",
            "feature:Billing:payment",
            "feature:Orders:customer",
            "feature:Orders:payment",
        ]

    def test_the_walk_does_not_depend_on_unit_order(self):
        units = units_from_layout(self._beacon(), "csharp")
        forward = walk(Trie(units), ROLE_WORDS)
        backward = walk(Trie(list(reversed(units))), ROLE_WORDS)
        assert forward.candidates == backward.candidates

    def test_layers_without_a_grid_are_the_boxes(self):
        layout = {
            f"Beacon.{layer}/{layer}Thing{i}.cs": [f"Beacon.{layer}.{layer}Thing{i}"]
            for layer in self.LAYERS
            for i in range(3)
        }
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        assert keys(frontier) == [f"box:Beacon.{layer}" for layer in sorted(self.LAYERS)]
        assert frontier.axis == "structural"

    def test_a_grid_is_one_box_when_the_walk_may_not_transpose(self):
        frontier = walk(Trie(units_from_layout(self._beacon(), "csharp")), ROLE_WORDS, transpose=False)
        assert keys(frontier) == ["box:Beacon"]
        assert any(note.endswith("kept as one box") for note in frontier.notes)

    def test_the_shallowest_feature_directory_keys_its_subtree(self):
        layout = self._beacon()
        layout["Beacon.Domain/Incidents/Metrics/IncidentMetric.cs"] = ["Beacon.Domain.Incidents.Metrics.IncidentMetric"]
        layout["Beacon.Application/Metrics/MetricRollup.cs"] = ["Beacon.Application.Metrics.MetricRollup"]
        layout["Beacon.Infrastructure/Metrics/MetricStore.cs"] = ["Beacon.Infrastructure.Metrics.MetricStore"]
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        features = {candidate.label: candidate for candidate in frontier.candidates if candidate.kind == FEATURE}
        assert ("Beacon", "Domain", "Incidents", "Metrics") not in features["Metrics"].prefixes
        assert ("Beacon", "Application", "Metrics") in features["Metrics"].prefixes


class TestTheWalkEmitsRulesNotAssignments:
    def test_every_candidate_is_a_rule_over_prefixes_or_words(self):
        layout = project("Catalog.API", 3) | project("Basket.API", 3)
        frontier = walk(Trie(units_from_layout(layout, "csharp")), ROLE_WORDS)
        for candidate in frontier.candidates:
            assert candidate.kind in (BOX, FEATURE, LOOSE, RESIDUAL)
            assert candidate.prefixes or candidate.fallback_prefixes or candidate.terms
