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
