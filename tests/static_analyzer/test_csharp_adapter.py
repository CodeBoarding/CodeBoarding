"""Tests for the C# language adapter."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.constants import Language, NodeType
from static_analyzer.engine.adapters.csharp_adapter import CSharpAdapter


class TestGetLspCommandDotnetCheck:
    """``get_lsp_command`` must reject hosts without the .NET SDK.

    csharp-ls is a ``dotnet tool`` and cannot run without the runtime,
    so a missing ``dotnet`` produces a silently broken analysis. We
    surface that as a clear RuntimeError at LSP-launch, mirroring how
    ``RustAdapter`` enforces a cargo toolchain.
    """

    def test_raises_when_dotnet_missing(self, tmp_path: Path) -> None:
        real_which = shutil.which

        def selective(name: str) -> str | None:
            if name == "dotnet":
                return None
            return real_which(name)

        with patch("static_analyzer.engine.adapters.csharp_adapter.shutil.which", side_effect=selective):
            with pytest.raises(RuntimeError, match=r"\.NET SDK not found.*dotnet\.microsoft\.com"):
                CSharpAdapter().get_lsp_command(tmp_path)

    def test_returns_command_when_dotnet_present(self, tmp_path: Path) -> None:
        real_which = shutil.which

        def selective(name: str) -> str | None:
            if name == "dotnet":
                return "/usr/local/bin/dotnet"
            return real_which(name)

        resolved = "/opt/codeboarding/pm-tools/csharp/csharp-ls"
        lsp_servers = {"csharp": {"command": [resolved]}}

        with (
            patch("static_analyzer.engine.adapters.csharp_adapter.shutil.which", side_effect=selective),
            patch("static_analyzer.engine.language_adapter.get_config", return_value=lsp_servers),
        ):
            cmd = CSharpAdapter().get_lsp_command(tmp_path)
        assert cmd[0] == resolved


class TestCSharpAdapterProperties:
    """Tests for basic adapter properties."""

    def test_language(self):
        adapter = CSharpAdapter()
        assert adapter.language == "CSharp"

    def test_file_extensions(self):
        adapter = CSharpAdapter()
        assert adapter.file_extensions == (".cs",)

    def test_language_enum(self):
        assert CSharpAdapter().language_enum is Language.CSHARP

    def test_lsp_command(self):
        adapter = CSharpAdapter()
        cmd = adapter.lsp_command
        assert len(cmd) == 1
        assert cmd[0].endswith("csharp-ls")

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

    def test_csharp_ls_hierarchy_skips_file_and_namespace(self):
        """csharp-ls: File(kind=1) -> Namespace(kind=3) -> Class -> Method."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/src/Contoso.Processing/Download/DownloadService.cs"),
            symbol_name="ProcessAsync",
            symbol_kind=NodeType.METHOD,
            parent_chain=[
                ("DownloadService.cs", NodeType.FILE),
                ("Download", NodeType.NAMESPACE),
                ("DownloadService", NodeType.CLASS),
            ],
            project_root=self.root,
        )
        # File and Namespace skipped, DownloadService matches filename -> deduped
        assert result == "Contoso.Processing.Download.DownloadService.ProcessAsync"

    def test_namespace_symbol_uses_detail(self):
        """Namespace symbols use their detail field as the qualified name."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/src/Contoso.Processing/Download/DownloadService.cs"),
            symbol_name="Download",
            symbol_kind=NodeType.NAMESPACE,
            parent_chain=[("DownloadService.cs", NodeType.FILE)],
            project_root=self.root,
            detail="Contoso.Processing.Download",
        )
        assert result == "Contoso.Processing.Download"

    def test_class_matching_filename_deduplicates(self):
        """Class name == filename -> don't duplicate."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/src/Contoso.Api/Program.cs"),
            symbol_name="Program",
            symbol_kind=NodeType.CLASS,
            parent_chain=[("Program.cs", NodeType.FILE)],
            project_root=self.root,
        )
        assert result == "Contoso.Api.Program"

    def test_src_prefix_stripped(self):
        """The 'src' directory is stripped from qualified names."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/repo/src/Contoso.Core/Models/Media.cs"),
            symbol_name="Media",
            symbol_kind=NodeType.CLASS,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "Contoso.Core.Models.Media"


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


class TestWaitForDiagnostics:
    """csharp-ls publishes diagnostics asynchronously with no readiness signal,
    so the adapter falls back to debouncing on the publishDiagnostics stream."""

    def test_calls_quiesce_on_client(self):
        adapter = CSharpAdapter()
        called = {}

        class FakeClient:
            def wait_for_diagnostics_quiesce(self, idle_seconds, max_wait):
                called["idle"] = idle_seconds
                called["max"] = max_wait

        adapter.wait_for_diagnostics(FakeClient())  # type: ignore[arg-type]
        assert called.get("idle", 0) > 0, "csharp-ls needs an idle wait or diagnostics will be missed"
        assert called.get("max", 0) >= called.get("idle", 0)


class TestPrepareProject:
    """``prepare_project`` runs ``dotnet restore`` so csharp-ls sees framework
    references; without it diagnostics are flooded with bogus CS0518."""

    def test_runs_dotnet_restore_when_csproj_present(self, tmp_path, monkeypatch):
        (tmp_path / "Foo.csproj").write_text("<Project />")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/dotnet" if name == "dotnet" else None)

        called = {}

        def fake_run(cmd, **kwargs):
            called["cmd"] = cmd
            called["cwd"] = kwargs.get("cwd")
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(
            "static_analyzer.engine.adapters.csharp_adapter.subprocess.run",
            fake_run,
        )
        CSharpAdapter().prepare_project(tmp_path)
        assert called["cmd"][:2] == ["dotnet", "restore"]
        assert called["cmd"][2] == "Foo.csproj"
        assert called["cwd"] == str(tmp_path)

    def test_prefers_solution_over_csproj(self, tmp_path, monkeypatch):
        (tmp_path / "Foo.sln").write_text("")
        (tmp_path / "Foo.csproj").write_text("<Project />")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/dotnet" if name == "dotnet" else None)

        called = {}

        def fake_run(cmd, **kwargs):
            called["cmd"] = cmd
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(
            "static_analyzer.engine.adapters.csharp_adapter.subprocess.run",
            fake_run,
        )
        CSharpAdapter().prepare_project(tmp_path)
        assert called["cmd"][2] == "Foo.sln"

    def test_skips_when_no_project_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/dotnet" if name == "dotnet" else None)

        def fake_run(*_args, **_kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(
            "static_analyzer.engine.adapters.csharp_adapter.subprocess.run",
            fake_run,
        )
        CSharpAdapter().prepare_project(tmp_path)  # no exception

    def test_skips_when_dotnet_not_on_path(self, tmp_path, monkeypatch):
        (tmp_path / "Foo.csproj").write_text("<Project />")
        monkeypatch.setattr("shutil.which", lambda _: None)

        def fake_run(*_args, **_kwargs):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(
            "static_analyzer.engine.adapters.csharp_adapter.subprocess.run",
            fake_run,
        )
        CSharpAdapter().prepare_project(tmp_path)

    def test_swallows_restore_failure(self, tmp_path, monkeypatch):
        (tmp_path / "Foo.csproj").write_text("<Project />")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/dotnet" if name == "dotnet" else None)

        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=1, stdout="", stderr="boom")

        monkeypatch.setattr(
            "static_analyzer.engine.adapters.csharp_adapter.subprocess.run",
            fake_run,
        )
        # Should not raise — restore failures are warnings, not aborts.
        CSharpAdapter().prepare_project(tmp_path)

    def test_handles_subprocess_timeout(self, tmp_path, monkeypatch):
        (tmp_path / "Foo.csproj").write_text("<Project />")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/dotnet" if name == "dotnet" else None)

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

        monkeypatch.setattr(
            "static_analyzer.engine.adapters.csharp_adapter.subprocess.run",
            fake_run,
        )
        CSharpAdapter().prepare_project(tmp_path)  # no exception
