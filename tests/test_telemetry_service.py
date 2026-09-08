"""What every event carries about who produced it.

``source`` has always said which invoker a run came from. What it could not say
is whether that invoker is a *person using the product*: a dashboard measuring
usage had to spell out ``source not in ('tests', 'evals')`` and was silently
wrong from the moment another internal source was added. ``internal`` is derived
from the source in one place so that filter is one condition and stays correct.

These exercise ``ProductTelemetry`` itself rather than ``telemetry.events``,
which stubs ``capture`` out — the stamping happens below that stub, so it is
invisible to every test in ``test_telemetry_events.py``.
"""

from types import SimpleNamespace

import pytest

from telemetry.service import INTERNAL_SOURCES, ProductTelemetry


@pytest.fixture
def client(monkeypatch):
    """A recorder in place of the PostHog SDK, on the live singleton."""
    seen = SimpleNamespace(captures=[], exceptions=[])
    service = ProductTelemetry()
    monkeypatch.setattr(
        service,
        "_client",
        SimpleNamespace(
            capture=lambda **kw: seen.captures.append(kw),
            capture_exception=lambda exc, **kw: seen.exceptions.append((exc, kw)),
            flush=lambda: None,
        ),
    )
    seen.service = service
    return seen


def test_a_product_run_is_not_marked_internal(client, monkeypatch):
    monkeypatch.setenv("CODEBOARDING_SOURCE", "oss")

    client.service.capture("analysis_started", {"command": "run_full"})

    props = client.captures[0]["properties"]
    assert props["source"] == "oss"
    assert props["internal"] is False


@pytest.mark.parametrize("source", sorted(INTERNAL_SOURCES))
def test_every_internal_source_is_marked_internal(client, monkeypatch, source):
    """The benchmark harness sets ``evals``; the engine's own suite sets ``tests``.
    Neither is a person using the product, and neither may be counted as one."""
    monkeypatch.setenv("CODEBOARDING_SOURCE", source)

    client.service.capture("analysis_completed", {"status": "success"})

    props = client.captures[0]["properties"]
    assert props["source"] == source
    assert props["internal"] is True


def test_an_unknown_source_defaults_to_product_traffic(client, monkeypatch):
    """A source nobody declared is an embedding of the library, which IS usage.
    Defaulting the other way would quietly drop a real integration's runs."""
    monkeypatch.setenv("CODEBOARDING_SOURCE", "some-new-wrapper")

    client.service.capture("analysis_started", {})

    assert client.captures[0]["properties"]["internal"] is False


def test_the_origin_travels_on_exceptions_too(client, monkeypatch):
    """``$exception`` is an event like any other, and error-rate dashboards are
    exactly where a run of the benchmark's deliberate failure cases would hurt."""
    monkeypatch.setenv("CODEBOARDING_SOURCE", "evals")
    exc = RuntimeError("boom")

    client.service.capture_exception(exc, properties={"command": "run_incremental"})

    _, kwargs = client.exceptions[0]
    assert kwargs["properties"]["internal"] is True
    assert kwargs["properties"]["command"] == "run_incremental"


def test_a_caller_property_never_overwrites_the_origin(client, monkeypatch):
    """The origin merges LAST, so a caller cannot claim it.

    It used to go first, which left an event model free to overwrite `source`
    simply by having a field of that name. No schema does today — but `internal`
    is the one property a product metric filters on, so a payload that could
    overwrite it is a payload that could hide automated traffic inside usage, and
    that is not a thing to leave resting on nobody having added the field yet."""
    monkeypatch.setenv("CODEBOARDING_SOURCE", "evals")

    client.service.capture("repo_scanned", {"source": "oss", "internal": False})

    props = client.captures[0]["properties"]
    assert props["source"] == "evals"
    assert props["internal"] is True


def test_ci_runs_report_the_repository_owner(client, monkeypatch):
    monkeypatch.delenv("CODEBOARDING_ORG", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Acme-Corp/widgets")

    client.service.capture("analysis_started", {})

    assert client.captures[0]["properties"]["org"] == "acme-corp"


def test_the_same_owner_always_reads_the_same_way(client, monkeypatch):
    """The owner is the join key across surfaces and across runs, so casing and
    the two ways of naming one have to agree on a single value."""
    monkeypatch.delenv("CODEBOARDING_ORG", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Acme/widgets")
    client.service.capture("analysis_started", {})

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("CODEBOARDING_ORG", "acme")
    client.service.capture("analysis_started", {})

    assert client.captures[0]["properties"]["org"] == client.captures[1]["properties"]["org"]


def test_only_the_owner_is_taken_from_the_repository_slug(client, monkeypatch):
    """Why: the name is the half that says something about the code."""
    monkeypatch.delenv("CODEBOARDING_ORG", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/secret-prototype")

    client.service.capture("analysis_started", {})

    props = client.captures[0]["properties"]
    assert props["org"] == "acme"
    assert "secret-prototype" not in str(props)


def test_an_embedding_can_name_the_owner_itself(client, monkeypatch):
    """The VSCode extension resolves the owner from the workspace's git remote
    and sets ``CODEBOARDING_ORG``, which has to win: a run driven by the editor
    inside a CI-like environment belongs to the workspace, not the runner."""
    monkeypatch.setenv("CODEBOARDING_ORG", "Acme")
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone-else/runner")

    client.service.capture("analysis_started", {})

    assert client.captures[0]["properties"]["org"] == "acme"


def test_a_local_run_reports_no_owner_at_all(client, monkeypatch):
    """Nothing infers an owner. With neither variable set the key is absent
    rather than empty, so a query can tell "no owner" from "owner is blank"."""
    monkeypatch.delenv("CODEBOARDING_ORG", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    client.service.capture("analysis_started", {})

    assert "org" not in client.captures[0]["properties"]


def test_a_caller_property_never_overwrites_the_owner(client, monkeypatch):
    """``org`` merges with the rest of the origin, after the payload, for the
    same reason ``internal`` does: it is a property of the process."""
    monkeypatch.delenv("CODEBOARDING_ORG", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/widgets")

    client.service.capture("repo_scanned", {"org": "somebody-else"})

    assert client.captures[0]["properties"]["org"] == "acme"


def test_a_caller_cannot_supply_an_owner_when_there_is_none(client, monkeypatch):
    """Merging the origin last stops a payload *overwriting* an owner, but not
    inventing one: with no owner resolved the key is absent, so a caller's value
    would have survived into it and attributed the run to an account it has
    nothing to do with. The reserved keys come off the payload first."""
    monkeypatch.delenv("CODEBOARDING_ORG", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    client.service.capture("repo_scanned", {"org": "somebody-else", "total_loc": 10})

    props = client.captures[0]["properties"]
    assert "org" not in props
    assert props["total_loc"] == 10


def test_a_caller_cannot_supply_an_owner_on_exceptions_either(client, monkeypatch):
    monkeypatch.delenv("CODEBOARDING_ORG", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    client.service.capture_exception(RuntimeError("boom"), properties={"org": "somebody-else"})

    _, kwargs = client.exceptions[0]
    assert "org" not in kwargs["properties"]


def test_the_owner_travels_on_exceptions_too(client, monkeypatch):
    """Why: exceptions take a different code path, and error-rate dashboards are
    where knowing whether one deployment or all of them is hitting a bug pays."""
    monkeypatch.delenv("CODEBOARDING_ORG", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/widgets")

    client.service.capture_exception(RuntimeError("boom"), properties={"command": "run_full"})

    _, kwargs = client.exceptions[0]
    assert kwargs["properties"]["org"] == "acme"
