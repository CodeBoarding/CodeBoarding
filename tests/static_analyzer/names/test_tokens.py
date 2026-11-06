from static_analyzer.clustering.names.tokens import (
    ROLE_WORDS,
    distinctive_word,
    is_role_named,
    segments,
    stem,
    tokenize,
    ubiquitous_words,
)


class TestTokenize:
    def test_camel_case(self):
        assert tokenize("IncidentRepository") == ("Incident", "Repository")

    def test_run_of_capitals_is_one_word(self):
        assert tokenize("HTTPServerFactory") == ("HTTP", "Server", "Factory")

    def test_snake_and_kebab_case(self):
        assert tokenize("open_incident-handler") == ("open", "incident", "handler")

    def test_interface_prefix_is_dropped(self):
        assert tokenize("IScheduleQuery") == ("Schedule", "Query")

    def test_a_word_starting_with_i_is_not_an_interface(self):
        assert tokenize("Incident") == ("Incident",)
        assert tokenize("IO") == ("IO",)
        assert tokenize("IOException") == ("IO", "Exception")
        assert tokenize("IDGenerator") == ("ID", "Generator")

    def test_bare_numbers_are_not_words(self):
        assert tokenize("docx_0") == ("docx",)
        assert tokenize("v1alpha1") == ("v1alpha1",)

    def test_parameter_list_generics_and_arity_are_dropped(self):
        assert tokenize("Add(int value)") == ("Add",)
        assert tokenize("Repository<TEntity>") == ("Repository",)
        assert tokenize("Handler<Dictionary<string, Order>>") == ("Handler",)
        assert tokenize("Repository`1") == ("Repository",)


class TestStem:
    def test_folds_inflections_onto_one_word(self):
        assert stem("Ordering") == stem("Orders") == stem("Order")
        assert stem("Postmortems") == stem("Postmortem")
        assert stem("Queries") == stem("Query")

    def test_keeps_the_e_of_a_word_ending_in_es(self):
        """``Services`` must meet ``Service``; stripping ``es`` blindly left ``servic``."""
        assert stem("Services") == stem("Service")
        assert stem("Types") == stem("Type")
        assert stem("Classes") == stem("Class")

    def test_leaves_a_short_word_alone(self):
        assert stem("Bus") == "bus"

    def test_is_idempotent(self):
        """A plural of an -ing word must reach the same stem as the -ing word, or role words miss."""
        assert stem("Mappings") == stem("Mapping") == stem(stem("Mappings"))
        assert stem("Settings") == stem("Setting")


class TestSegments:
    def test_parameter_lists_and_generics_stay_whole(self):
        assert segments("Basket.API.Grpc.BasketService.Get(Dictionary<string, Foo.Bar> a)", ".") == [
            "Basket",
            "API",
            "Grpc",
            "BasketService",
            "Get(Dictionary<string, Foo.Bar> a)",
        ]

    def test_empty_parts_are_dropped(self):
        assert segments("a..b", ".") == ["a", "b"]


class TestRoleWords:
    def test_layers_are_role_named(self):
        assert is_role_named("Application", ROLE_WORDS)
        assert is_role_named("ViewModels", ROLE_WORDS)
        assert is_role_named("IntegrationEvents", ROLE_WORDS)

    def test_a_feature_is_not(self):
        assert not is_role_named("Ordering", ROLE_WORDS)
        assert not is_role_named("Catalog.API", ROLE_WORDS)

    def test_a_ubiquitous_word_does_not_count_against_a_role_name(self):
        """``Beacon.Api`` is a layer once every sibling carries ``Beacon``."""
        assert is_role_named("Beacon.Api", ROLE_WORDS, frozenset({"beacon"}))
        assert not is_role_named("Beacon.Api", ROLE_WORDS)


class TestUbiquitousWords:
    def test_a_product_name_most_siblings_carry(self):
        assert "modulify" in ubiquitous_words(["Modulify.Catalog", "Modulify.Player", "Modulify.Library"])

    def test_frequency_not_intersection(self):
        """One odd sibling must not keep the product name alive as everyone's distinctive word."""
        assert "polly" in ubiquitous_words(["Polly.Core", "Polly.Extensions", "Polly.Testing", "Docs"])

    def test_a_word_only_some_siblings_carry_is_not(self):
        assert "catalog" not in ubiquitous_words(["Modulify.Catalog", "Modulify.Player"])

    def test_two_siblings_sharing_a_word_out_of_many_is_not(self):
        assert "app" not in ubiquitous_words(["ClientApp", "WebApp", "Catalog", "Basket", "Identity", "Ordering"])

    def test_two_of_three_siblings_sharing_a_word_is_kinship_not_ubiquity(self):
        assert "order" not in ubiquitous_words(["Ordering", "OrderProcessor", "Catalog"])
        assert "polly" in ubiquitous_words(["Polly.Core", "Polly.Extensions", "Polly.Testing"])


class TestDistinctiveWord:
    def test_skips_role_and_ubiquitous_words(self):
        assert distinctive_word("Ordering.API", ROLE_WORDS) == "order"
        assert distinctive_word("OrderProcessor", ROLE_WORDS) == "order"
        assert distinctive_word("Modulify.Catalog", ROLE_WORDS, frozenset({"modulify"})) == "catalog"

    def test_a_name_made_of_role_words_has_none(self):
        assert distinctive_word("WebApp", ROLE_WORDS) == ""
