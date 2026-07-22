from static_analyzer.engine.adapters.php_adapter import PHPAdapter


def test_does_not_wait_for_workspace_index() -> None:
    adapter = PHPAdapter()

    assert adapter.wait_for_workspace_ready is False
    assert adapter.probe_before_open is True
    assert adapter.interleave_did_open_with_symbols is True


def test_non_positive_reference_settings_use_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CODEBOARDING_PHP_REFERENCES_BATCH_SIZE", "-1")
    monkeypatch.setenv("CODEBOARDING_PHP_REFERENCES_QUERY_TIMEOUT", "0")

    adapter = PHPAdapter()

    assert adapter.references_batch_size == 10
    assert adapter.references_per_query_timeout == 10
