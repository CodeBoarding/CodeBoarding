"""Tests for the Rust language adapter."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from static_analyzer.constants import Language
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.rust_adapter import RustAdapter, _normalize_parent


class TestRustAdapterProperties:
    """Basic adapter property tests."""

    def test_language(self):
        assert RustAdapter().language == "Rust"

    def test_file_extensions(self):
        assert RustAdapter().file_extensions == (".rs",)

    def test_language_enum(self):
        assert RustAdapter().language_enum is Language.RUST

    def test_lsp_command(self):
        assert RustAdapter().lsp_command == ["rust-analyzer"]

    def test_language_id(self):
        assert RustAdapter().language_id == "rust"

    def test_registry_returns_rust_adapter(self):
        """Adapter registry key 'Rust' resolves to a RustAdapter instance."""
        adapter = get_adapter("Rust")
        assert isinstance(adapter, RustAdapter)

    def test_wait_for_workspace_ready_is_true(self):
        """Rust opts into the workspace-ready wait so Phase 2 references queries
        run after rust-analyzer has loaded the Cargo workspace.
        """
        assert RustAdapter().wait_for_workspace_ready is True

    def test_references_per_query_timeout_is_nonzero(self):
        """A non-zero value gates the Phase-1.5 warmup probe in CallGraphBuilder."""
        assert RustAdapter().references_per_query_timeout > 0

    def test_extra_client_capabilities_advertises_server_status(self):
        """rust-analyzer only emits ``experimental/serverStatus`` notifications
        when the client advertises this capability in the initialize request.
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
        # Selective: only ``cargo`` is missing; other binaries pass through
        # to the real ``shutil.which`` so the base resolver works normally.
        real_which = shutil.which

        def selective(name: str) -> str | None:
            if name == "cargo":
                return None
            return real_which(name)

        with patch("static_analyzer.engine.adapters.rust_adapter.shutil.which", side_effect=selective):
            with pytest.raises(RuntimeError, match=r"cargo not found.*rustup\.rs"):
                RustAdapter().get_lsp_command(tmp_path)

    def test_returns_command_when_cargo_present(self, tmp_path: Path) -> None:
        # Selective patch: ``cargo`` resolves to a fake path, every other
        # binary lookup (including the ``rust-analyzer`` lookup performed
        # by ``resolve_config_from_path`` in the base resolver) falls
        # through to the real ``shutil.which``. A blanket patch would be
        # global (``shutil.which`` is a module attribute), making the
        # resolver believe rust-analyzer also lives at the fake cargo
        # path and producing a misleading absolute command.
        real_which = shutil.which

        def selective(name: str) -> str | None:
            if name == "cargo":
                return "/usr/local/bin/cargo"
            return real_which(name)

        with patch("static_analyzer.engine.adapters.rust_adapter.shutil.which", side_effect=selective):
            cmd = RustAdapter().get_lsp_command(tmp_path)
        # The base resolver returns whatever ``build_config`` produces:
        # an absolute ``rust-analyzer`` path if installed, otherwise the
        # bare ``["rust-analyzer"]`` from ``VSCODE_CONFIG``. Both contain
        # the substring ``rust-analyzer``.
        assert cmd
        assert any("rust-analyzer" in part for part in cmd)


class TestLspInitOptions:
    """rust-analyzer initialization options."""

    def test_enables_build_scripts_and_proc_macros(self):
        options = RustAdapter().get_lsp_init_options()
        assert options["cargo"]["buildScripts"]["enable"] is True
        assert options["procMacro"]["enable"] is True

    def test_enables_check_on_save_for_diagnostics(self):
        # cargo check is what surfaces unused_imports / unused_variables /
        # dead_code diagnostics — without it the unused-code health check
        # has nothing to categorize for Rust.
        options = RustAdapter().get_lsp_init_options()
        assert options["checkOnSave"] is True
        assert options["check"]["command"] == "check"

    def test_enables_all_cargo_targets(self):
        assert RustAdapter().get_lsp_init_options()["cargo"]["allTargets"] is True


class TestWaitForDiagnostics:
    """Rust reuses ``wait_for_server_ready`` after resetting the ready signal,
    relying on rust-analyzer's ``experimental/serverStatus.quiescent`` flag.
    Falls back to debouncing if no signal arrives quickly."""

    def test_uses_ready_signal_when_available(self):
        """When rust-analyzer transitions to quiescent within 10s, the
        signal-based path is taken (no debounce fallback)."""
        adapter = RustAdapter()
        events: list = []

        class FakeClient:
            def reset_ready_signal(self):
                events.append("reset")

            def wait_for_server_ready(self, timeout: int = 300) -> bool:
                events.append("wait")
                return True  # signal arrived

            def wait_for_diagnostics_quiesce(self, idle_seconds, max_wait):
                events.append("quiesce")

        adapter.wait_for_diagnostics(FakeClient())  # type: ignore[arg-type]
        assert "reset" in events
        assert "wait" in events
        assert "quiesce" not in events, "should NOT fall back to debounce when signal fires"

    def test_falls_back_to_debounce_when_no_signal(self):
        """When rust-analyzer stays quiescent (no transition), falls back
        to debouncing instead of blocking 120s."""
        adapter = RustAdapter()
        events: list = []

        class FakeClient:
            def reset_ready_signal(self):
                events.append("reset")

            def wait_for_server_ready(self, timeout: int = 300) -> bool:
                events.append("wait")
                return False  # timed out

            def wait_for_diagnostics_quiesce(self, idle_seconds, max_wait):
                events.append("quiesce")

        adapter.wait_for_diagnostics(FakeClient())  # type: ignore[arg-type]
        assert "reset" in events
        assert "quiesce" in events, "should fall back to debounce when no signal arrives"


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
        """A malformed ``impl `` with no body returns the stripped token."""
        assert _normalize_parent("impl ") == "impl"

    def test_impl_with_inherent_generics(self):
        """``impl<T> Foo<T>`` — impl-level generics declared before the type."""
        assert _normalize_parent("impl<T> Repository<T>") == "Repository"
        assert _normalize_parent("impl<T, U> Map<T, U>") == "Map"

    def test_impl_with_generic_bounds(self):
        """Trait bounds in the impl-level generics block must be skipped wholesale."""
        assert _normalize_parent("impl<T: Clone> Vec<T>") == "Vec"
        assert _normalize_parent("impl<T: Clone + Send> Repository<T>") == "Repository"

    def test_impl_with_nested_generics_in_bounds(self):
        """Impl-level generics may themselves contain nested generic types."""
        assert _normalize_parent("impl<T: Iterator<Item = u8>> Foo<T>") == "Foo"

    def test_generic_trait_impl(self):
        """``impl<T> Trait for Type<T>`` — the implementing type comes after ``for``."""
        assert _normalize_parent("impl<T> Iterator for MyIter<T>") == "MyIter"

    def test_impl_with_lifetime(self):
        """Lifetime parameters in the impl block also need to be skipped."""
        assert _normalize_parent("impl<'a> Borrow<str> for Owned") == "Owned"

    def test_impl_with_where_clause(self):
        """``where`` clauses are stripped along with type-level generics."""
        assert _normalize_parent("impl<T> Foo where T: Clone") == "Foo"
        assert _normalize_parent("impl Foo where T: Clone") == "Foo"

    def test_implementation_word_passes_through(self):
        """``implementation`` is not an impl block — must not be touched."""
        assert _normalize_parent("implementation") == "implementation"


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
