"""Tests for the C# language adapter."""

from pathlib import Path

from static_analyzer.engine.adapters.csharp_adapter import CSharpAdapter
from static_analyzer.constants import NodeType


class TestCSharpAdapterProperties:
    """Tests for basic adapter properties."""

    def test_language(self):
        adapter = CSharpAdapter()
        assert adapter.language == "CSharp"

    def test_file_extensions(self):
        adapter = CSharpAdapter()
        assert adapter.file_extensions == (".cs",)

    def test_lsp_command(self):
        adapter = CSharpAdapter()
        assert adapter.lsp_command == ["csharp-ls"]

    def test_language_id(self):
        adapter = CSharpAdapter()
        assert adapter.language_id == "csharp"

    def test_config_key_defaults_to_language_id(self):
        adapter = CSharpAdapter()
        assert adapter.config_key == "csharp"


class TestBuildQualifiedName:
    """Tests for C#-specific qualified name construction."""

    def setup_method(self):
        self.adapter = CSharpAdapter()
        self.root = Path("/repo")

    def test_simple_symbol(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/Program.cs"),
            symbol_name="Main",
            symbol_kind=NodeType.FUNCTION,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "Program.Main"

    def test_nested_directory(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/Services/Auth/AuthService.cs"),
            symbol_name="Login",
            symbol_kind=NodeType.METHOD,
            parent_chain=[("AuthService", NodeType.CLASS)],
            project_root=self.root,
        )
        # AuthService matches filename stem -> deduplicated
        assert result == "Services.Auth.AuthService.Login"

    def test_deduplicates_filename_class(self):
        """When the first parent matches the filename, it should be stripped."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/Models/User.cs"),
            symbol_name="Name",
            symbol_kind=NodeType.PROPERTY,
            parent_chain=[("User", NodeType.CLASS)],
            project_root=self.root,
        )
        # User (parent) == User (file stem) -> deduplicated
        assert result == "Models.User.Name"

    def test_no_deduplication_when_different(self):
        """When the first parent differs from filename, keep all parents."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/Helpers.cs"),
            symbol_name="Validate",
            symbol_kind=NodeType.METHOD,
            parent_chain=[("StringHelper", NodeType.CLASS)],
            project_root=self.root,
        )
        # StringHelper != Helpers -> no deduplication
        assert result == "Helpers.StringHelper.Validate"

    def test_deeply_nested_parents(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/Controllers/UserController.cs"),
            symbol_name="GetById",
            symbol_kind=NodeType.METHOD,
            parent_chain=[
                ("UserController", NodeType.CLASS),
                ("InnerClass", NodeType.CLASS),
            ],
            project_root=self.root,
        )
        # UserController matches file stem -> stripped, InnerClass kept
        assert result == "Controllers.UserController.InnerClass.GetById"


class TestExtractPackage:
    """Tests for namespace/package extraction."""

    def test_deep_qualified_name(self):
        adapter = CSharpAdapter()
        assert adapter.extract_package("Services.Auth.AuthService.Login") == "Services.Auth"

    def test_shallow_qualified_name(self):
        adapter = CSharpAdapter()
        assert adapter.extract_package("Models.User") == "Models"

    def test_single_component(self):
        adapter = CSharpAdapter()
        assert adapter.extract_package("Program") == "Program"


class TestLspConfiguration:
    """Tests for csharp-ls LSP configuration."""

    def test_init_options_log_level(self):
        adapter = CSharpAdapter()
        opts = adapter.get_lsp_init_options()
        assert opts["csharp"]["logLevel"] == "warning"

    def test_workspace_settings(self):
        adapter = CSharpAdapter()
        settings = adapter.get_workspace_settings()
        assert settings is not None
        assert settings["csharp"]["logLevel"] == "warning"

    def test_default_timeout_higher_than_base(self):
        adapter = CSharpAdapter()
        assert adapter.get_lsp_default_timeout() > 60

    def test_probe_timeout_minimum_exceeds_default(self):
        adapter = CSharpAdapter()
        assert adapter.get_probe_timeout_minimum() > 300


class TestLspEnv:
    """Tests for DOTNET_ROOT resolution."""

    def test_returns_empty_when_dotnet_root_set(self, monkeypatch):
        monkeypatch.setenv("DOTNET_ROOT", "/usr/share/dotnet")
        adapter = CSharpAdapter()
        assert adapter.get_lsp_env() == {}

    def test_resolves_dotnet_root_from_path(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DOTNET_ROOT", raising=False)
        # Simulate Homebrew layout: bin/dotnet -> Cellar/.../libexec/dotnet
        libexec = tmp_path / "opt" / "dotnet" / "libexec"
        libexec.mkdir(parents=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        dotnet_bin = bin_dir / "dotnet"
        dotnet_bin.symlink_to(libexec / "dotnet")
        (libexec / "dotnet").touch()
        monkeypatch.setattr("shutil.which", lambda _: str(dotnet_bin))
        adapter = CSharpAdapter()
        env = adapter.get_lsp_env()
        assert env.get("DOTNET_ROOT") == str(libexec)

    def test_returns_empty_when_dotnet_not_found(self, monkeypatch):
        monkeypatch.delenv("DOTNET_ROOT", raising=False)
        monkeypatch.setattr("shutil.which", lambda _: None)
        adapter = CSharpAdapter()
        assert adapter.get_lsp_env() == {}


class TestReferenceTracking:
    """Tests for symbol filtering behavior."""

    def test_namespace_is_reference_worthy(self):
        adapter = CSharpAdapter()
        assert adapter.is_reference_worthy(NodeType.NAMESPACE) is True

    def test_class_is_reference_worthy(self):
        adapter = CSharpAdapter()
        assert adapter.is_reference_worthy(NodeType.CLASS) is True

    def test_method_is_reference_worthy(self):
        adapter = CSharpAdapter()
        assert adapter.is_reference_worthy(NodeType.METHOD) is True
