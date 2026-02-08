"""Tests for language_settings module."""

from static_analyzer.constants import Language
from static_analyzer.lsp_client.language_settings import get_language_settings


class TestGetLanguageSettings:
    """Tests for get_language_settings function."""

    def test_get_python_settings(self):
        """Test Python language settings are returned correctly."""
        settings = get_language_settings("python")
        assert settings is not None
        assert "python" in settings
        assert "analysis" in settings["python"]
        assert "typeCheckingMode" in settings["python"]["analysis"]
        assert settings["python"]["analysis"]["typeCheckingMode"] == "basic"

    def test_get_typescript_settings(self):
        """Test TypeScript language settings are returned correctly."""
        settings = get_language_settings("typescript")
        assert settings is not None
        assert "typescript" in settings

    def test_get_javascript_settings(self):
        """Test JavaScript language settings are returned correctly."""
        settings = get_language_settings("javascript")
        assert settings is not None
        assert "javascript" in settings

    def test_get_go_settings(self):
        """Test Go language settings are returned correctly."""
        settings = get_language_settings("go")
        assert settings is not None
        assert "gopls" in settings

    def test_get_java_settings(self):
        """Test Java language settings are returned correctly."""
        settings = get_language_settings("java")
        assert settings is not None
        assert "java" in settings

    def test_get_php_settings(self):
        """Test PHP language settings are returned correctly."""
        settings = get_language_settings("php")
        assert settings is not None
        assert "intelephense" in settings

    def test_get_unsupported_language_returns_none(self):
        """Test that unsupported languages return None."""
        settings = get_language_settings("unsupported_lang")
        assert settings is None

    def test_get_rust_settings(self):
        """Test Rust language settings - should return None (not configured)."""
        settings = get_language_settings("rust")
        assert settings is None

    def test_case_insensitive_language_id(self):
        """Test that language ID is case insensitive."""
        lower = get_language_settings("python")
        upper = get_language_settings("PYTHON")
        mixed = get_language_settings("Python")
        assert lower == upper == mixed
