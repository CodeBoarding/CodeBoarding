"""Anonymous, opt-out usage telemetry.

One singleton (``telemetry``) sends anonymous events to PostHog. Disabled with
``CODEBOARDING_TELEMETRY=false`` (or ``DO_NOT_TRACK=1``). No repo content, paths,
or API keys are ever sent. See ``service.py`` for the identity rules.
"""

from telemetry.service import ProductTelemetry, telemetry

__all__ = ["ProductTelemetry", "telemetry"]
