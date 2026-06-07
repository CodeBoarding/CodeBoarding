import logging
import os

from telemetry.device_id import generate_device_id

logger = logging.getLogger(__name__)

# Public PostHog project key (safe to ship; it is write-only ingest).
POSTHOG_PROJECT_API_KEY = os.getenv("CODEBOARDING_POSTHOG_KEY", "phc_BQWpoXuPYQhW7mPWQcRv4yzSfuoAmh48EmXuUpeXPUB2")
POSTHOG_HOST = os.getenv("CODEBOARDING_POSTHOG_HOST", "https://us.i.posthog.com")


def _telemetry_disabled() -> bool:
    if os.getenv("DO_NOT_TRACK", "").strip().lower() in ("1", "true", "yes"):
        return True
    return os.getenv("CODEBOARDING_TELEMETRY", "true").strip().lower() == "false"


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
            from posthog import Posthog  # type: ignore[import-not-found]

            self._client = Posthog(
                project_api_key=POSTHOG_PROJECT_API_KEY,
                host=POSTHOG_HOST,
                disable_geoip=True,
            )
            # Silence the SDK's own logging unless we're debugging.
            logging.getLogger("posthog").setLevel(logging.CRITICAL)
        except Exception as e:  # SDK missing or init failed -> no-op
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
            # "vscode" when invoked by the extension, "oss" for the OSS CLI
            # (main.py), "core" for any other embedding.
            source = os.getenv("CODEBOARDING_SOURCE", "core")
            self._client.capture(
                distinct_id=self.user_id,
                event=event,
                properties={"source": source, **(properties or {})},
            )
        except Exception as e:
            logger.debug("Telemetry capture failed: %s", e)

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception:
                pass


telemetry = ProductTelemetry()
