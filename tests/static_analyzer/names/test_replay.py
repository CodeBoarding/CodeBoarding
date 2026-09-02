"""Replay is a pure function of a unit's names and the rules: nothing else may move a unit."""

from static_analyzer.clustering.names import ROLE_WORDS, ComponentRule, ScopeSpec, replay
from static_analyzer.clustering.names.replay import FALLBACK, PREFIX, TERM
from static_analyzer.clustering.names.spec import UNPLACED
from tests.static_analyzer.names.conftest import unit


def scope(*rules: ComponentRule) -> ScopeSpec:
    return ScopeSpec("root", list(rules))


CATALOG = ComponentRule("1", "Catalog", prefixes=(("Catalog",),), terms=("catalog",))
ORDER = ComponentRule("2", "Order", prefixes=(("Ordering",), ("OrderProcessor",)), terms=("order",))
LOOSE = ComponentRule("3", "Loose files", fallback_prefixes=((),))


class TestPrefixes:
    def test_the_longest_matching_prefix_wins(self):
        deep = ComponentRule("9", "Catalog items", prefixes=(("Catalog", "API", "Model"),))
        result = replay(
            [unit("f", "Catalog.API.Model.CatalogItem"), unit("g", "Catalog.API.Apis.CatalogApi")],
            scope(CATALOG, deep),
            ROLE_WORDS,
        )
        assert result.assignment == {"f": "9", "g": "1"}
        assert set(result.placed_by.values()) == {PREFIX}

    def test_a_prefix_never_matches_a_partial_segment(self):
        result = replay([unit("f", "Catalogue.X")], scope(CATALOG), ROLE_WORDS)
        assert result.unplaced[0].unit_id == "f"


class TestTerms:
    def test_a_unit_with_no_prefix_votes_with_its_words(self):
        result = replay(
            [unit("f", "Shared.OrderTotals", "Shared.OrderTotals.Sum()")], scope(CATALOG, ORDER), ROLE_WORDS
        )
        assert result.assignment == {"f": "2"}
        assert result.placed_by["f"] == TERM

    def test_the_head_noun_weighs_most(self):
        """``IncidentResolvedMetricsHandler`` is about metrics and reacts to an incident."""
        incident = ComponentRule("1", "Incidents", terms=("incident",))
        metrics = ComponentRule("2", "Analytics", terms=("metric",))
        result = replay([unit("f", "App.IncidentResolvedMetricsHandler")], scope(incident, metrics), ROLE_WORDS)
        assert result.assignment == {"f": "2"}

    def test_every_segment_of_every_name_votes(self):
        catalog = ComponentRule("1", "Catalog", terms=("catalog",))
        basket = ComponentRule("2", "Basket", terms=("basket",))
        names = ["Web.Client", "Web.Client.GetBasket()", "Web.Client.AddToBasket()", "Web.Client.ListCatalog()"]
        result = replay([unit("f", *names)], scope(catalog, basket), ROLE_WORDS)
        assert result.assignment == {"f": "2"}

    def test_role_words_never_vote(self):
        service = ComponentRule("1", "Services", terms=("service",))
        result = replay([unit("f", "X.OrderService")], scope(service, ORDER), ROLE_WORDS)
        assert result.assignment == {"f": "2"}

    def test_a_term_owned_twice_belongs_to_the_first_rule(self):
        first = ComponentRule("1", "A", terms=("order",))
        second = ComponentRule("2", "B", terms=("order",))
        assert replay([unit("f", "X.Order")], scope(first, second), ROLE_WORDS).assignment == {"f": "1"}

    def test_a_tied_vote_goes_to_the_rule_that_comes_first_not_the_lower_id(self):
        zebra = ComponentRule("9", "Zebra", terms=("zebra",))
        apple = ComponentRule("1", "Apple", terms=("apple",))
        result = replay([unit("f", "X.AppleZebra", "X.ZebraApple")], scope(zebra, apple), ROLE_WORDS)
        assert result.assignment == {"f": "9"}


class TestFallbackAndUnplaced:
    def test_fallback_comes_after_terms(self):
        result = replay([unit("f", "Shared.OrderTotals"), unit("g", "Shared.Misc")], scope(ORDER, LOOSE), ROLE_WORDS)
        assert result.assignment == {"f": "2", "g": "3"}
        assert result.placed_by["g"] == FALLBACK

    def test_unplaced_units_land_in_the_bucket_and_are_still_reported(self):
        bucket = ComponentRule("4", "Unassigned", kind=UNPLACED)
        result = replay([unit("f", "Shipping.Api.Ship")], scope(CATALOG, ORDER, bucket), ROLE_WORDS)
        assert result.assignment == {"f": "4"}
        assert [u.unit_id for u in result.unplaced] == ["f"]
        assert result.placed_by["f"] == UNPLACED

    def test_without_a_bucket_an_unplaced_unit_is_only_reported(self):
        result = replay([unit("f", "Shipping.Api.Ship")], scope(CATALOG, ORDER), ROLE_WORDS)
        assert result.assignment == {}
        assert [u.unit_id for u in result.unplaced] == ["f"]

    def test_unplaced_units_are_grouped_where_they_leave_the_known_tree(self):
        units = [unit("f", "Shipping.Api.Ship"), unit("g", "Shipping.Domain.Parcel"), unit("h", "Loose")]
        result = replay(units, scope(CATALOG, ORDER), ROLE_WORDS)
        assert {key: [u.unit_id for u in members] for key, members in result.new_scopes.items()} == {
            ("Shipping",): ["f", "g"],
            (): ["h"],
        }

    def test_members_follow_rule_order_and_include_empty_rules(self):
        result = replay([unit("f", "Ordering.Api.X")], scope(CATALOG, ORDER), ROLE_WORDS)
        assert list(result.members) == ["1", "2"]
        assert result.size("1") == 0 and result.size("2") == 1


class TestNewScopes:
    def test_units_a_loose_rule_catches_still_surface_where_they_diverge(self):
        """A root with loose files must not hide a new top-level directory behind 'Loose files'."""
        shipping = [unit(f"s{i}", f"Shipping.API.Ship{i}") for i in range(3)]
        result = replay([unit("p", "Program"), *shipping], scope(CATALOG, ORDER, LOOSE), ROLE_WORDS)
        assert all(result.assignment[u.unit_id] == "3" for u in shipping)
        assert [u.unit_id for u in result.new_scopes[("Shipping",)]] == ["s0", "s1", "s2"]
        assert [u.unit_id for u in result.new_scopes[()]] == ["p"]

    def test_a_unit_a_word_claims_outside_every_prefix_is_still_a_new_scope_candidate(self):
        """A new directory is a new directory, whatever its names happen to say."""
        result = replay([unit("s0", "Shipping.OrderShipment.Run()")], scope(ORDER), ROLE_WORDS)
        assert result.assignment["s0"] == "2"
        assert [u.unit_id for u in result.new_scopes[("Shipping",)]] == ["s0"]

    def test_a_unit_a_prefix_claims_is_not_a_new_scope(self):
        result = replay(
            [unit("f", "Catalog.API.Item"), unit("g", "OrderProcessor.Totals")],
            scope(CATALOG, ORDER, LOOSE),
            ROLE_WORDS,
        )
        assert result.new_scopes == {}

    def test_divergence_is_measured_against_owned_prefixes_only(self):
        """A fallback prefix must not hide the directory a new unit diverges at."""
        residual = ComponentRule("4", "Application (residual)", fallback_prefixes=(("Beacon", "Application"),))
        feature = ComponentRule(
            "5", "Incidents", prefixes=(("Beacon", "Application", "Incidents"),), terms=("incident",)
        )
        result = replay([unit("f", "Beacon.Application.Billing.Invoice")], scope(residual, feature), ROLE_WORDS)
        assert result.assignment == {"f": "4"}
        assert list(result.new_scopes) == [("Beacon", "Application", "Billing")]


class TestPurity:
    def test_a_new_sibling_moves_nothing(self):
        before = [unit("a", "Catalog.API.Item"), unit("b", "Ordering.API.Order")]
        old = replay(before, scope(CATALOG, ORDER), ROLE_WORDS).assignment
        after = before + [unit("c", "Shipping.API.Ship"), unit("d", "Catalog.API.Brand")]
        new = replay(after, scope(CATALOG, ORDER), ROLE_WORDS).assignment
        assert {k: new[k] for k in old} == old
