"""Tests for the Rust language adapter."""

from pathlib import Path

from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.rust_adapter import RustAdapter


class TestRustAdapterProperties:
    """Basic adapter property tests."""

    def test_language(self):
        assert RustAdapter().language == "Rust"

    def test_file_extensions(self):
        assert RustAdapter().file_extensions == (".rs",)

    def test_lsp_command(self):
        assert RustAdapter().lsp_command == ["rust-analyzer"]

    def test_language_id(self):
        assert RustAdapter().language_id == "rust"

    def test_registry_returns_rust_adapter(self):
        """Adapter registry key 'Rust' resolves to a RustAdapter instance."""
        adapter = get_adapter("Rust")
        assert isinstance(adapter, RustAdapter)


class TestBuildQualifiedName:
    """Tests for qualified name building, especially mod.rs collapsing."""

    def setup_method(self):
        self.adapter = RustAdapter()
        self.root = Path("/project")

    def test_top_level_function(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/main.rs"),
            symbol_name="main",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "src.main.main"

    def test_nested_module_function(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/models/user.rs"),
            symbol_name="new",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "src.models.user.new"

    def test_mod_rs_collapses_into_parent(self):
        """mod.rs is the module entry point — 'mod' should not appear in the qualified name."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/models/mod.rs"),
            symbol_name="ModelError",
            symbol_kind=5,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "src.models.ModelError"

    def test_method_in_impl_block(self):
        """Methods inside impl blocks have the struct as parent."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/models/user.rs"),
            symbol_name="validate",
            symbol_kind=6,
            parent_chain=[("User", 23)],  # 23 = Struct
            project_root=self.root,
        )
        assert result == "src.models.user.User.validate"

    def test_method_in_mod_rs_impl_block(self):
        """Methods in impl blocks inside mod.rs should collapse the 'mod' stem."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/handlers/mod.rs"),
            symbol_name="handle_request",
            symbol_kind=6,
            parent_chain=[("Router", 23)],
            project_root=self.root,
        )
        assert result == "src.handlers.Router.handle_request"

    def test_deeply_nested_module(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/api/v2/routes.rs"),
            symbol_name="list_users",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "src.api.v2.routes.list_users"

    def test_lib_rs_at_root(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/lib.rs"),
            symbol_name="init",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "src.lib.init"

    def test_mod_rs_at_project_root_falls_back_to_stem(self):
        """A mod.rs directly in the project root is pathological but should not crash."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/mod.rs"),
            symbol_name="Setup",
            symbol_kind=5,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "mod.Setup"


class TestBuildReferenceKey:
    """Reference key should preserve original casing."""

    def test_preserves_snake_case(self):
        adapter = RustAdapter()
        assert adapter.build_reference_key("src.models.user.find_by_id") == "src.models.user.find_by_id"

    def test_preserves_pascal_case(self):
        adapter = RustAdapter()
        assert adapter.build_reference_key("src.models.user.UserConfig") == "src.models.user.UserConfig"
