"""Tests for the Rust language adapter."""

from pathlib import Path
from unittest.mock import patch

import pytest

from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.rust_adapter import RustAdapter, _normalize_parent


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

    def test_wait_for_workspace_ready_is_true(self):
        """Rust must opt into the workspace-ready wait so phase 2 references work.

        Without this, ``StaticAnalyzer.start_clients`` skips the
        ``wait_for_server_ready`` call and ``textDocument/references``
        queries race the cargo metadata load. Regression guard.
        """
        assert RustAdapter().wait_for_workspace_ready is True

    def test_references_per_query_timeout_is_nonzero(self):
        """A non-zero value gates the Phase-1.5 warmup probe in CallGraphBuilder."""
        assert RustAdapter().references_per_query_timeout > 0

    def test_extra_client_capabilities_advertises_server_status(self):
        """rust-analyzer requires this capability to send quiescent notifications.

        Verified by direct probing — without ``serverStatusNotification: true``
        in the initialize request's experimental block, rust-analyzer never
        emits ``experimental/serverStatus`` and ``wait_for_server_ready``
        blocks until timeout.
        """
        caps = RustAdapter().extra_client_capabilities
        assert caps == {"experimental": {"serverStatusNotification": True}}


class TestGetLspCommandCargoCheck:
    """``get_lsp_command`` must reject hosts without a Rust toolchain.

    rust-analyzer needs ``cargo metadata`` to index any Cargo workspace, so
    a missing ``cargo`` binary produces a silently broken analysis (zero
    references, zero edges). We surface that as a clear RuntimeError at
    LSP-launch instead, mirroring how ``JavaAdapter`` enforces a JDK.
    """

    def test_raises_when_cargo_missing(self, tmp_path: Path) -> None:
        with patch("static_analyzer.engine.adapters.rust_adapter.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match=r"cargo not found.*rustup\.rs"):
                RustAdapter().get_lsp_command(tmp_path)

    def test_returns_command_when_cargo_present(self, tmp_path: Path) -> None:
        with patch(
            "static_analyzer.engine.adapters.rust_adapter.shutil.which",
            return_value="/usr/local/bin/cargo",
        ):
            cmd = RustAdapter().get_lsp_command(tmp_path)
        # The base resolver consults ``get_config('lsp_servers')`` first
        # and falls back to ``self.lsp_command`` (`["rust-analyzer"]`) if
        # the registry has no entry. Either way the command must invoke
        # ``rust-analyzer``.
        assert cmd
        assert any("rust-analyzer" in part for part in cmd)


class TestLspInitOptions:
    """rust-analyzer initialization options."""

    def test_enables_build_scripts_and_proc_macros(self):
        options = RustAdapter().get_lsp_init_options()
        assert options["cargo"]["buildScripts"]["enable"] is True
        assert options["procMacro"]["enable"] is True

    def test_disables_check_on_save(self):
        # cargo check during indexing significantly slows rust-analyzer
        # and we don't consume its diagnostics for static analysis.
        assert RustAdapter().get_lsp_init_options()["checkOnSave"] is False

    def test_enables_all_cargo_targets(self):
        assert RustAdapter().get_lsp_init_options()["cargo"]["allTargets"] is True


class TestBuildQualifiedName:
    """Tests for qualified name building, especially mod.rs / lib.rs / main.rs collapsing."""

    def setup_method(self):
        self.adapter = RustAdapter()
        self.root = Path("/project")

    def test_top_level_function_in_main_rs_collapses(self):
        """main.rs is the binary crate root, so its stem should not appear."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/main.rs"),
            symbol_name="main",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "src.main"

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

    def test_lib_rs_collapses_as_crate_root(self):
        """src/lib.rs is the library crate root; ``lib`` stem should be dropped."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/lib.rs"),
            symbol_name="init",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "src.init"

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

    def test_lib_rs_at_project_root_falls_back_to_stem(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/lib.rs"),
            symbol_name="init",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "lib.init"

    def test_main_rs_at_project_root_falls_back_to_stem(self):
        """A bare main.rs at the project root is pathological — fall back to stem.

        Symmetric with the mod.rs and lib.rs cases above; covers the
        ``parts`` empty branch in ``build_qualified_name`` for completeness.
        """
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/main.rs"),
            symbol_name="main",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "main.main"

    def test_method_in_lib_rs_impl_block(self):
        """impl blocks inside lib.rs collapse the lib stem just like mod.rs."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/lib.rs"),
            symbol_name="register",
            symbol_kind=6,
            parent_chain=[("Crate", 23)],
            project_root=self.root,
        )
        assert result == "src.Crate.register"

    def test_function_named_main_in_non_main_rs(self):
        """A function literally named ``main`` in a regular module file is unambiguous.

        Guards against any future refactor that strips ``main`` from symbol
        names instead of just from file stems.
        """
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/utils.rs"),
            symbol_name="main",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "src.utils.main"


class TestNormalizeParent:
    """Cleanup of rust-analyzer parent names so impl blocks contribute the type only."""

    def test_inherent_impl_strips_keyword(self):
        assert _normalize_parent("impl Entity") == "Entity"

    def test_trait_impl_keeps_implementing_type(self):
        """``impl Speaker for Cat`` is a trait impl on Cat — the type we want is Cat."""
        assert _normalize_parent("impl Speaker for Cat") == "Cat"

    def test_generic_type_params_are_stripped(self):
        """Generic params would otherwise leak ``<T>`` into qualified names."""
        assert _normalize_parent("impl Repository<T>") == "Repository"
        assert _normalize_parent("impl Iterator for Vec<u8>") == "Vec"

    def test_non_impl_names_pass_through(self):
        """Module / struct parents (not impl headers) are returned unchanged."""
        assert _normalize_parent("models") == "models"
        assert _normalize_parent("InnerStruct") == "InnerStruct"

    def test_impl_keyword_alone_returns_stripped(self):
        """Defensive: a malformed ``impl `` with no body falls back to the stripped input."""
        # ``"impl "`` strips to ``"impl"`` which no longer starts with ``"impl "``
        # (note the trailing space), so the early-return path applies.
        assert _normalize_parent("impl ") == "impl"


class TestBuildQualifiedNameWithImplBlocks:
    """build_qualified_name should produce clean names for symbols inside impl blocks."""

    def setup_method(self):
        self.adapter = RustAdapter()
        self.root = Path("/project")

    def test_inherent_impl_method(self):
        """``impl Entity { fn get_id() }`` -> ``models.base.Entity.get_id``."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/models/base.rs"),
            symbol_name="get_id",
            symbol_kind=6,
            parent_chain=[("impl Entity", 5)],
            project_root=self.root,
        )
        assert result == "src.models.base.Entity.get_id"

    def test_trait_impl_method(self):
        """``impl Speaker for Cat { fn speak() }`` -> ``models.entities.Cat.speak``."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/models/entities.rs"),
            symbol_name="speak",
            symbol_kind=6,
            parent_chain=[("impl Speaker for Cat", 5)],
            project_root=self.root,
        )
        assert result == "src.models.entities.Cat.speak"

    def test_generic_impl_method(self):
        """Generic parameters in impl headers are stripped, keeping the bare type."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/src/store.rs"),
            symbol_name="add",
            symbol_kind=6,
            parent_chain=[("impl Repository<T>", 5)],
            project_root=self.root,
        )
        assert result == "src.store.Repository.add"


class TestBuildReferenceKey:
    """Reference key should preserve original casing (inherited from base)."""

    def test_preserves_snake_case(self):
        adapter = RustAdapter()
        assert adapter.build_reference_key("src.models.user.find_by_id") == "src.models.user.find_by_id"

    def test_preserves_pascal_case(self):
        adapter = RustAdapter()
        assert adapter.build_reference_key("src.models.user.UserConfig") == "src.models.user.UserConfig"
