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


def _repo_owner() -> str:
    """The account a run's repository belongs to, when the environment names one.

    Only the owner segment is read, never the repository name: the owner is what
    separates one deployment's runs from another's, and the repository name adds
    nothing to that while saying considerably more about the code.

    ``CODEBOARDING_ORG`` is the explicit form, set by an embedding that already
    knows the owner. ``GITHUB_REPOSITORY`` is the CI form — Actions always sets
    it to ``owner/name``. Neither is set for a plain local run, which therefore
    reports no owner at all. Lower-cased because owners are case-insensitive on
    GitHub, and a grouping that splits ``Acme`` from ``acme`` is a wrong one.
    """
    explicit = os.getenv("CODEBOARDING_ORG", "").strip()
    owner = explicit or os.getenv("GITHUB_REPOSITORY", "").strip().partition("/")[0]
    return owner.lower()


def _origin() -> dict[str, object]:
    """Who invoked this run, and whether that is a person using the product.

    ``source`` is the existing discriminator: "vscode" when invoked by the
    extension, "oss" for the OSS CLI, "github_action" in CI, "tests" for the
    engine's own suite, "evals" for the benchmark harness, "core" for any other
    embedding. ``internal`` is derived from it so the two can never disagree.

    ``org`` is the repository owner from :func:`_repo_owner`, and is omitted
    entirely when the environment names none — an absent key reads as "unknown"
    in a query, which an empty string does not.
    """
    source = os.getenv("CODEBOARDING_SOURCE", "core")
    origin: dict[str, object] = {"source": source, "internal": source in INTERNAL_SOURCES}

    owner = _repo_owner()
    if owner:
        origin["org"] = owner
    return origin


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
                # Origin LAST, so it wins. Who invoked the run is a property of
                # the process, not a field an event model gets to claim — and
                # `internal` is the one property a product metric filters on, so
                # a payload that could overwrite it is a payload that could hide
                # automated traffic inside usage.
                properties={**(properties or {}), **_origin()},
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
                # Origin LAST, so it wins. Who invoked the run is a property of
                # the process, not a field an event model gets to claim — and
                # `internal` is the one property a product metric filters on, so
                # a payload that could overwrite it is a payload that could hide
                # automated traffic inside usage.
                properties={**(properties or {}), **_origin()},
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
