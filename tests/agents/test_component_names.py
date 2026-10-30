import pytest

from agents.component_names import (
    humanize,
    identifier_path_names,
    is_natural_name,
    placeholder_name,
    unique_name,
)

IDENTIFIERS = [
    "billing_jobs",
    "action_scripts",
    "action_support_scripts",
    "monitoring_and_telemetry",
    "repository-and-git-utilities",
    "static-analysis-engine",
    "PetTypeRepository",
    "ClientApp",
    "Catalog.API",
    "[owner]",
    "owner",
    "js",
]

NATURAL_NAMES = [
    "GitHub Action Automation Scripts",
    "RabbitMQ Event Bus",
    "DynamoDB Session Store",
    "WebSocket JSON-RPC Server",
    "CI/CD Automation & Multi-Format Reporter",
    ".NET SDK Lifecycle & Environment Resolver",
    "Next.js Frontend",
    "gRPC Service",
    "Owner",
    "Basket gRPC Service",
]


class TestNaturalNames:
    @pytest.mark.parametrize("name", IDENTIFIERS)
    def test_an_identifier_is_not_a_name(self, name):
        assert not is_natural_name(name)

    @pytest.mark.parametrize("name", NATURAL_NAMES)
    def test_natural_text_is(self, name):
        assert is_natural_name(name)

    def test_a_placeholder_is_not_a_name(self):
        assert not is_natural_name(placeholder_name("1.2"))

    def test_a_re_cased_identifier_directory_is_not_a_name(self):
        paths = identifier_path_names(["billing_jobs/handler.py", "owner/Owner.java", "src/EventBus/Bus.cs"])

        assert not is_natural_name("Billing Jobs", paths)
        assert not is_natural_name("Event Bus", paths)
        assert is_natural_name("Owner", paths)
        assert is_natural_name("Invoice Generation Jobs", paths)


class TestHumanize:
    @pytest.mark.parametrize(
        ("name", "words"),
        [
            ("billing_jobs", "Billing Jobs"),
            ("monitoring_and_telemetry", "Monitoring and Telemetry"),
            ("order-store", "Order Store"),
            ("PetTypeRepository", "Pet Type Repository"),
            ("Ordering.API", "Ordering API"),
            ("owner", "Owner"),
            ("order_service Handlers", "Order Service Handlers"),
        ],
    )
    def test_writes_an_identifier_as_words(self, name, words):
        assert humanize(name) == words

    @pytest.mark.parametrize("name", [*NATURAL_NAMES, placeholder_name("3")])
    def test_leaves_natural_text_and_placeholders_alone(self, name):
        assert humanize(name) == name


class TestUniqueName:
    def test_a_free_name_is_kept_and_reserved(self):
        taken: set[str] = set()

        assert unique_name("Storage", "2", taken) == "Storage"
        assert taken == {"Storage"}

    def test_a_taken_name_gets_the_group_id(self):
        assert unique_name("Storage", "2", {"Storage"}) == "Storage 2"

    def test_keeps_suffixing_until_the_name_is_free(self):
        assert unique_name("Storage", "3", {"Storage", "Storage 3"}) == "Storage 3 2"
