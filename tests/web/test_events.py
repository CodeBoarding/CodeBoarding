import asyncio
import json
import logging

from codeboarding_web.events import EventBus, TraceLogHandler, format_sse


def test_format_sse():
    out = format_sse("run_end", {"run_id": "r1"})
    assert out == 'event: run_end\ndata: {"run_id": "r1"}\n\n'


def test_publish_threadsafe_reaches_subscriber():
    async def scenario():
        loop = asyncio.get_running_loop()
        bus = EventBus(loop)
        q = bus.subscribe()
        bus.publish_threadsafe("step_start", {"step": "x"})
        return await asyncio.wait_for(q.get(), timeout=1.0)

    event = asyncio.run(scenario())
    assert event == {"event": "step_start", "data": {"step": "x"}}


def test_trace_handler_publishes_parsed_records():
    async def scenario():
        loop = asyncio.get_running_loop()
        bus = EventBus(loop)
        q = bus.subscribe()
        handler = TraceLogHandler(bus)
        traces_logger = logging.getLogger("traces")
        original_level = traces_logger.level
        original_propagate = traces_logger.propagate
        traces_logger.setLevel(logging.DEBUG)
        traces_logger.addHandler(handler)
        traces_logger.propagate = False
        try:
            traces_logger.info(json.dumps({"event": "phase_change", "step": "code_generation"}))
            return await asyncio.wait_for(q.get(), timeout=1.0)
        finally:
            traces_logger.removeHandler(handler)
            traces_logger.setLevel(original_level)
            traces_logger.propagate = original_propagate

    event = asyncio.run(scenario())
    assert event["event"] == "phase_change"
    assert event["data"]["step"] == "code_generation"
