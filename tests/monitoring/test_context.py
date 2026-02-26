import tempfile

from monitoring.context import current_step, monitor_execution


def test_monitor_context_exposes_end_step_and_resets_current_step():
    token = current_step.set("outside")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with monitor_execution(enabled=True, output_dir=tmpdir) as mon:
                assert hasattr(mon, "end_step")
                mon.step("phase_a")
                assert current_step.get() == "phase_a"
                mon.end_step()
                assert current_step.get() == "outside"

            assert current_step.get() == "outside"
    finally:
        current_step.reset(token)


def test_monitor_execution_resets_unclosed_step_on_exit():
    token = current_step.set("outside")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with monitor_execution(enabled=True, output_dir=tmpdir) as mon:
                mon.step("phase_a")
                assert current_step.get() == "phase_a"

            assert current_step.get() == "outside"
    finally:
        current_step.reset(token)


def test_monitor_context_supports_nested_steps():
    token = current_step.set("outside")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with monitor_execution(enabled=True, output_dir=tmpdir) as mon:
                mon.step("phase_a")
                mon.step("phase_b")
                assert current_step.get() == "phase_b"
                mon.end_step()
                assert current_step.get() == "phase_a"

            assert current_step.get() == "outside"
    finally:
        current_step.reset(token)
