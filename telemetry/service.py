import logging
import os

from posthog import Posthog

from telemetry.device_id import generate_device_id

logger = logging.getLogger(__name__)

# Public PostHog project key (safe to ship; it is write-only ingest).
POSTHOG_PROJECT_API_KEY = os.getenv("CODEBOARDING_POSTHOG_KEY", "phc_BQWpoXuPYQhW7mPWQcRv4yzSfuoAmh48EmXuUpeXPUB2")
POSTHOG_HOST = os.getenv("CODEBOARDING_POSTHOG_HOST", "https://us.i.posthog.com")


#: Sources that are NOT a person using the product. They still emit — an eval
#: run is real signal about LSP quality and analysis outcomes, and dropping it
#: would lose that — but every product metric has to be able to exclude them.
#:
#: Declared here, once, and surfaced as the derived ``internal`` property, rather
#: than left for each dashboard to maintain as a list of source values. A filter
#: written as ``source not in ('tests', 'evals')`` is correct until the next
#: internal source is added and then silently is not, which is exactly how
#: automated traffic comes to be counted as usage.
INTERNAL_SOURCES = frozenset({"tests", "evals"})


def _telemetry_disabled() -> bool:
    if os.getenv("DO_NOT_TRACK", "").strip().lower() in ("1", "true", "yes"):
        return True
    return os.getenv("CODEBOARDING_TELEMETRY", "true").strip().lower() == "false"


def _org() -> str:
    """The account a run's repository belongs to, or '' when none is named.

    Why: ``CODEBOARDING_ORG`` when a caller knows the owner, else the owner half
    of ``GITHUB_REPOSITORY``; the repository's own name is never read.
    """
    owner = os.getenv("CODEBOARDING_ORG", "").strip() or os.getenv("GITHUB_REPOSITORY", "").strip().partition("/")[0]
    return owner.lower()


#: Properties describing the process rather than the event. A payload that could
#: set one could hide automated traffic inside usage, or claim an owner it does
#: not have — so they are dropped from caller properties before the origin is
#: applied. Listed rather than derived from ``_origin()``: ``org`` is absent
#: there when no owner is known, which is exactly the case a caller could fill.
ORIGIN_KEYS = frozenset({"source", "internal", "org"})


def _origin() -> dict[str, object]:
    """Who invoked this run, whether that is product usage, and which deployment.

    Why: ``internal`` is derived from ``source`` so the two cannot disagree, and
    ``org`` is omitted rather than blank so an absent key reads as unknown.
    """
    source = os.getenv("CODEBOARDING_SOURCE", "core")
    origin: dict[str, object] = {"source": source, "internal": source in INTERNAL_SOURCES}
    if org := _org():
        origin["org"] = org
    return origin


def _with_origin(properties: dict | None) -> dict:
    """Caller properties, unable to claim any of :data:`ORIGIN_KEYS`."""
    return {k: v for k, v in (properties or {}).items() if k not in ORIGIN_KEYS} | _origin()


class ProductTelemetry:
    """Singleton wrapper around the PostHog SDK. All failures are swallowed."""

    _instance: "ProductTelemetry | None" = None

    def __new__(cls) -> "ProductTelemetry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._client = None
        self._user_id: str | None = None

        if _telemetry_disabled() or not POSTHOG_PROJECT_API_KEY:
            return

        try:
            self._client = Posthog(
                project_api_key=POSTHOG_PROJECT_API_KEY,
                host=POSTHOG_HOST,
                disable_geoip=True,
            )
            # Silence the SDK's own logging unless we're debugging.
            logging.getLogger("posthog").setLevel(logging.CRITICAL)
        except Exception as e:  # init failed -> no-op
            logger.debug("Telemetry disabled (init failed): %s", e)
            self._client = None

    @property
    def user_id(self) -> str:
        if self._user_id is not None:
            return self._user_id

        env_id = os.getenv("CODEBOARDING_TELEMETRY_USER_ID", "").strip()
        self._user_id = env_id or generate_device_id()
        return self._user_id

    def capture(self, event: str, properties: dict | None = None) -> None:
        if self._client is None:
            return
        try:
            self._client.capture(
                distinct_id=self.user_id,
                event=event,
                properties=_with_origin(properties),
            )
        except Exception as e:
            logger.debug("Telemetry capture failed: %s", e)

    def capture_exception(self, exc: BaseException, *, properties: dict | None = None) -> None:
        """Forward to PostHog's built-in ``$exception`` error tracking."""
        if self._client is None:
            return
        try:
            self._client.capture_exception(
                exc,
                distinct_id=self.user_id,
                properties=_with_origin(properties),
            )
        except Exception as e:
            logger.debug("Telemetry capture_exception failed: %s", e)

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception:
                pass


telemetry = ProductTelemetry()
