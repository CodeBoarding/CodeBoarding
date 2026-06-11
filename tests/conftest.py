import os

import pytest


os.environ["CODEBOARDING_SOURCE"] = "tests"


@pytest.fixture(autouse=True)
def label_test_telemetry(monkeypatch):
    monkeypatch.setenv("CODEBOARDING_SOURCE", "tests")
