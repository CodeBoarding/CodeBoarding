"""In-process event fan-in (worker thread -> asyncio queues) + SSE framing."""

import asyncio
import json
import logging


def format_sse(event: str, data: dict) -> str:
    """Encode one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class EventBus:
    """Fan-in from a worker thread to any number of asyncio subscribers."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop
        self._subscribers: set[asyncio.Queue] = set()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Rebind to the loop that will actually serve requests."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        """Register a new subscriber queue and return it."""
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        self._subscribers.discard(q)

    def publish_threadsafe(self, event: str, data: dict) -> None:
        """Publish from any thread; marshals onto the event loop. No-op if loop not yet bound."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._publish, event, data)

    def _publish(self, event: str, data: dict) -> None:
        message = {"event": event, "data": data}
        for q in list(self._subscribers):
            q.put_nowait(message)


class TraceLogHandler(logging.Handler):
    """Forward JSON ``traces`` logger records onto an :class:`EventBus`."""

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        """Parse JSON message and publish to the bus; silently drop non-JSON records."""
        try:
            payload = json.loads(record.getMessage())
        except (ValueError, TypeError):
            return
        event = payload.get("event", "trace")
        self._bus.publish_threadsafe(event, payload)
