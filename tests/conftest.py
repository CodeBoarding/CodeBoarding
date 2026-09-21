import os

import pytest


# The unit suite must not report to the product's PostHog project. Its runs are
# not usage, and — because ``track_analysis`` captures in a ``finally`` before
# the caller sees the exception — every test that asserts a raise ships a
# ``$exception``. Three of the four most frequent exceptions on the project were
# a single such test each, which is how a deliberate assertion comes to outrank
# the errors real users hit.
#
# ``setdefault``, not assignment: exporting ``DO_NOT_TRACK=0`` still turns the
# reporting back on for a run that means to exercise it. ``CODEBOARDING_SOURCE``
# stays set so anything that does report from here is still labelled internal.
os.environ.setdefault("DO_NOT_TRACK", "1")
os.environ["CODEBOARDING_SOURCE"] = "tests"


@pytest.fixture(autouse=True)
def label_test_telemetry(monkeypatch):
    monkeypatch.setenv("CODEBOARDING_SOURCE", "tests")
