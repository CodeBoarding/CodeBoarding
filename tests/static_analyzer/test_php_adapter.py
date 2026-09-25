from static_analyzer.engine.adapters.php_adapter import PHPAdapter


def test_does_not_wait_for_workspace_index() -> None:
    adapter = PHPAdapter()

    assert adapter.wait_for_workspace_ready is False
    assert adapter.probe_before_open is True
    assert adapter.interleave_did_open_with_symbols is True
