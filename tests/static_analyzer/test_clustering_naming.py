"""Tests for partitioning by qualified name.

Each case here is a failure that was measured on a real ruler before it was fixed; the
scores are in the PR description.
"""

from static_analyzer.clustering.grouping import GroupingService
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.clustering.naming import (
    INFRASTRUCTURE,
    ComponentVocabulary,
    NamingModel,
    Unit,
    group_leaf_clusters,
    partition_by_name,
    scope_of,
    stem,
    tokenize,
    ubiquitous_words,
)


def model(components, machinery=()) -> NamingModel:
    return NamingModel(
        components=tuple(ComponentVocabulary(name, tuple(owns)) for name, owns in components),
        machinery=frozenset(machinery),
    )


class TestTokenize:
    def test_camel_case(self):
        assert tokenize("IncidentRepository") == ("Incident", "Repository")

    def test_run_of_capitals_is_one_word(self):
        assert tokenize("HTTPServerFactory") == ("HTTP", "Server", "Factory")

    def test_snake_case(self):
        assert tokenize("open_incident_handler") == ("open", "incident", "handler")

    def test_interface_prefix_is_dropped(self):
        assert tokenize("IScheduleQuery") == ("Schedule", "Query")

    def test_a_word_starting_with_i_is_not_an_interface(self):
        assert tokenize("Incident") == ("Incident",)

    def test_parameter_list_is_dropped(self):
        assert tokenize("Add(int value)") == ("Add",)

    def test_generic_arguments_are_dropped(self):
        assert tokenize("Repository<TEntity>") == ("Repository",)

    def test_generic_arity_suffix_is_dropped(self):
        assert tokenize("Repository`1") == ("Repository",)


class TestStem:
    def test_folds_an_inflection(self):
        assert stem("Ordering") == stem("Order")

    def test_folds_a_plural(self):
        assert stem("Postmortems") == stem("Postmortem")

    def test_leaves_a_short_word_alone(self):
        assert stem("Bus") == "bus"


class TestScopeOf:
    def test_build_root_is_not_a_scope(self):
        assert scope_of("src/Catalog.API/Model/CatalogItem.cs") == "Catalog.API"

    def test_a_root_level_file_has_no_scope(self):
        assert scope_of("Program.cs") == ""


class TestUbiquitousWords:
    def test_a_shared_product_name_is_ubiquitous(self):
        assert "modulify" in ubiquitous_words({"Modulify.Catalog", "Modulify.Player", "Modulify.Library"})

    def test_a_word_only_some_scopes_carry_is_not(self):
        assert "catalog" not in ubiquitous_words({"Modulify.Catalog", "Modulify.Player"})


class TestPartitionByScope:
    """Where the scopes are features, the scope is the component."""

    COMPONENTS = [("Catalog", ("Catalog",)), ("Ordering", ("Order",)), ("Payments", ("Payment",))]
    MACHINERY = ("API", "Domain", "Processor", "Item", "Dto")

    def _units(self):
        return {
            "catalog": Unit(("src/Catalog.API/CatalogItem.cs",), ("Catalog.API.CatalogItem",)),
            "order-api": Unit(("src/Ordering.API/OrderController.cs",), ("Ordering.API.OrderController",)),
            "order-domain": Unit(("src/Ordering.Domain/Order.cs",), ("Ordering.Domain.Order",)),
            "order-proc": Unit(("src/OrderProcessor/Worker.cs",), ("OrderProcessor.Worker",)),
            "payment": Unit(("src/PaymentProcessor/Worker.cs",), ("PaymentProcessor.Worker",)),
        }

    def test_scopes_sharing_their_own_word_merge(self):
        result = partition_by_name(self._units(), model(self.COMPONENTS, self.MACHINERY))
        assert result.by_structure
        assert result.assignment["order-api"] == result.assignment["order-domain"]
        assert result.assignment["order-proc"] == result.assignment["order-api"]

    def test_a_scope_with_its_own_word_stays_separate(self):
        """`PaymentProcessor` must not be absorbed into ordering."""
        result = partition_by_name(self._units(), model(self.COMPONENTS, self.MACHINERY))
        assert result.assignment["payment"] != result.assignment["order-api"]
        assert result.assignment["catalog"] != result.assignment["order-api"]

    def test_a_shared_product_prefix_does_not_collapse_the_repo(self):
        units = {
            "a": Unit(("Modulify.Catalog/Facade.cs",), ("Modulify.Catalog.Facade",)),
            "b": Unit(("Modulify.Player/Service.cs",), ("Modulify.Player.Service",)),
            "c": Unit(("Modulify.Library/Store.cs",), ("Modulify.Library.Store",)),
        }
        components = [("Catalog", ("Catalog",)), ("Player", ("Player",)), ("Library", ("Library",))]
        result = partition_by_name(units, model(components, ("Facade", "Service", "Store")))
        assert len(set(result.assignment.values())) == 3


class TestPartitionByVocabulary:
    """Where the model's own components do not name the scopes, the identifiers lead."""

    COMPONENTS = [
        ("Incidents", ("Incident", "Timeline")),
        ("Escalation", ("Escalation", "Policy")),
        ("Analytics", ("Metrics", "Reliability")),
    ]
    MACHINERY = ("Handler", "Repository", "Endpoints", "Get", "Create")

    def _partition(self, units):
        return partition_by_name(units, model(self.COMPONENTS, self.MACHINERY))

    def test_a_layered_scope_does_not_lead(self):
        """Grouping Beacon by Api/Application/Domain scores below not partitioning at all."""
        assert not model(self.COMPONENTS, self.MACHINERY).scopes_are_components(
            {"Beacon.Api", "Beacon.Application", "Beacon.Domain", "Beacon.Infrastructure"}
        )

    def test_a_feature_scope_does_lead(self):
        components = [("Catalog", ("Catalog",)), ("Ordering", ("Order",))]
        assert model(components, ("API",)).scopes_are_components({"Catalog.API", "Ordering.API", "EventBus"})

    def test_layers_are_not_used_as_components(self):
        units = {
            "a": Unit(("Beacon.Api/Endpoints/IncidentEndpoints.cs",), ("Beacon.Api.Endpoints.IncidentEndpoints",)),
            "b": Unit(
                ("Beacon.Application/Incidents/OpenIncidentHandler.cs",),
                ("Beacon.Application.Incidents.OpenIncidentHandler",),
            ),
        }
        result = self._partition(units)
        assert not result.by_structure
        assert result.assignment["a"] == "Incidents"
        assert result.assignment["b"] == "Incidents"

    def test_a_reactor_belongs_to_what_it_produces_not_what_it_consumes(self):
        """`IncidentResolvedMetricsHandler` reacts to an incident event; it is about metrics."""
        units = {
            "reactor": Unit(
                ("Beacon.Application/Analytics/IncidentResolvedMetricsHandler.cs",), ("IncidentResolvedMetricsHandler",)
            ),
        }
        assert self._partition(units).assignment["reactor"] == "Analytics"

    def test_a_unit_with_no_domain_word_is_infrastructure_and_counted(self):
        units = {
            "incident": Unit(("Beacon.Application/Incidents/OpenIncidentHandler.cs",), ("OpenIncidentHandler",)),
            "plumbing": Unit(("Beacon.Infrastructure/EfRepository.cs",), ("EfRepository",)),
        }
        result = self._partition(units)
        assert result.assignment["plumbing"] == INFRASTRUCTURE
        assert result.coverage == 0.5

    def test_machinery_never_owns_a_component(self):
        """Otherwise every Handler in the system lands in one box, which is a layer."""
        owned = model(self.COMPONENTS + [("Wrong", ("Handler",))], self.MACHINERY).owner_by_word()
        assert "handler" not in owned


class TestGroupLeafClusters:
    """The seam GroupingService consumes: sets of cluster ids."""

    def _results(self):
        return {
            "CSharp": ClusterResult(
                clusters={
                    1: {"Beacon.Application.Incidents.OpenIncidentHandler"},
                    2: {"Beacon.Api.Endpoints.IncidentEndpoints"},
                    3: {"Beacon.Application.Escalation.EscalationPlanner"},
                    4: {"Beacon.Platform.EfRepository"},
                },
                cluster_to_files={
                    1: {"Beacon.Application/Incidents/OpenIncidentHandler.cs"},
                    2: {"Beacon.Api/Endpoints/IncidentEndpoints.cs"},
                    3: {"Beacon.Application/Escalation/EscalationPlanner.cs"},
                    4: {"Beacon.Platform/EfRepository.cs"},
                },
            )
        }

    def _model(self):
        return model(
            [("Incidents", ("Incident",)), ("Escalation", ("Escalation",))],
            ("Handler", "Endpoints", "Repository", "Ef", "Planner", "Application", "Api", "Platform", "Beacon"),
        )

    def test_groups_are_exhaustive_and_disjoint(self):
        groups, _ = group_leaf_clusters(self._results(), self._model())
        members = [cid for group in groups for cid in group]
        assert sorted(members) == [1, 2, 3, 4]
        assert len(members) == len(set(members))

    def test_clusters_of_one_feature_land_together_across_layers(self):
        groups, _ = group_leaf_clusters(self._results(), self._model())
        owner = {cid: index for index, group in enumerate(groups) for cid in group}
        assert owner[1] == owner[2]
        assert owner[3] != owner[1]

    def test_coverage_reports_what_the_names_could_not_place(self):
        _, partition = group_leaf_clusters(self._results(), self._model())
        assert partition.assignment["4"] == INFRASTRUCTURE
        assert partition.coverage == 0.75

    def test_no_group_is_empty(self):
        groups, _ = group_leaf_clusters(self._results(), self._model())
        assert all(groups)


class TestGroupingServiceUsesTheModel:
    """The seam: a model present replaces the modularity peak, absent changes nothing."""

    def _results(self):
        return {
            "CSharp": ClusterResult(
                clusters={
                    1: {"Beacon.Application.Incidents.OpenIncidentHandler"},
                    2: {"Beacon.Api.Endpoints.IncidentEndpoints"},
                    3: {"Beacon.Application.Escalation.EscalationPlanner"},
                },
                cluster_to_files={
                    1: {"Beacon.Application/Incidents/OpenIncidentHandler.cs"},
                    2: {"Beacon.Api/Endpoints/IncidentEndpoints.cs"},
                    3: {"Beacon.Application/Escalation/EscalationPlanner.cs"},
                },
            )
        }

    def test_a_model_groups_by_name(self):
        naming = model(
            [("Incidents", ("Incident",)), ("Escalation", ("Escalation",))],
            ("Handler", "Endpoints", "Planner", "Application", "Api", "Beacon"),
        )
        groups, _ = GroupingService(naming).group(self._results(), {})
        owner = {cid: index for index, group in enumerate(groups) for cid in group}
        assert owner[1] == owner[2]
        assert owner[3] != owner[1]

    def test_without_a_model_nothing_changes(self):
        groups, _ = GroupingService().group(self._results(), {})
        assert sorted(cid for group in groups for cid in group) == [1, 2, 3]

    def test_sub_scopes_keep_the_graph_partitioner(self):
        """Only the top level is name-driven; a sub-scope has no scopes to read."""
        naming = model([("Incidents", ("Incident",))], ("Handler",))
        groups, _ = GroupingService(naming).group(self._results(), {}, subcomponents=True)
        assert sorted(cid for group in groups for cid in group) == [1, 2, 3]
