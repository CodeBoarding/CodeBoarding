from static_analyzer.engine.adapters.php_adapter import PHPAdapter


def test_waits_for_workspace_index() -> None:
    assert PHPAdapter().wait_for_workspace_ready is True
