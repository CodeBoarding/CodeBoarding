"""Guard-rail tests ensuring language support is wired end-to-end.

These tests fail loudly when a new language or tool kind is added to
``TOOL_REGISTRY`` without also wiring it into the downstream integration
points (install orchestrator, validation, VSCODE_CONFIG, adapters,
Language enum). Adding a language touches a handful of parallel lists
across the codebase; without these guards regressions hide until an
end-to-end CI run fails on a specific OS/language matrix cell.

Each test targets one specific integration point so a failure tells
you *which* list is out of sync.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install
from static_analyzer.constants import Language
from static_analyzer.engine.adapters import ADAPTER_REGISTRY
from tool_registry import TOOL_REGISTRY, ToolKind, has_required_tools, needs_install
from tool_registry.registry import ConfigSection, PackageManagerToolSource
from vscode_constants import VSCODE_CONFIG

# Sample LSP adapter imported just so test #4 can prove import ordering
# doesn't hide a missing adapter. Each adapter's own tests exercise it
# functionally — this file only checks structural consistency.


class TestInstallOrchestratorCoversEveryKind(unittest.TestCase):
    """``run_install`` must invoke an installer for every ``ToolKind`` that
    has at least one entry in ``TOOL_REGISTRY``.

    Regression this guards: adding a new ToolKind, plumbing it into
    registry + installers, but forgetting to wire it into ``install.py``'s
    per-kind step list. That's exactly how PACKAGE_MANAGER shipped silently
    broken on the first CI run — the installer existed but ``run_install``
    never called it.
    """

    # Map ToolKind -> install.py function name that must be invoked.
    # Kinds present in the registry but absent from this map will cause
    # the test below to fail — force the author to decide where to wire
    # the new kind in.
    _INSTALLER_FOR_KIND: dict[ToolKind, str] = {
        ToolKind.NATIVE: "download_binaries",
        ToolKind.NODE: "install_node_servers",
        ToolKind.ARCHIVE: "download_jdtls",
        ToolKind.PACKAGE_MANAGER: "install_package_manager_lsp_servers",
    }

    def test_every_tool_kind_in_registry_has_a_mapped_installer(self):
        kinds_in_registry = {dep.kind for dep in TOOL_REGISTRY}
        unmapped = kinds_in_registry - self._INSTALLER_FOR_KIND.keys()
        self.assertFalse(
            unmapped,
            f"ToolKind(s) {unmapped} are in TOOL_REGISTRY but not mapped "
            f"to an installer function in this test. Add them to both "
            f"``_INSTALLER_FOR_KIND`` here and to ``install.run_install``.",
        )

    def test_run_install_invokes_installer_for_every_kind_in_registry(self):
        """Patch each installer and assert it was called at least once for
        kinds present in the registry. Uses a throwaway target dir; the
        patches keep the test fast and filesystem-safe.
        """
        kinds_in_registry = {dep.kind for dep in TOOL_REGISTRY}

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)

            # npm availability stubbed True so the node installer branch runs;
            # the embedded Node bootstrap is stubbed so we don't actually
            # download ~30MB of runtime during a unit test.
            patches = [
                patch("install.download_binaries"),
                patch("install.install_node_servers"),
                patch("install.download_jdtls"),
                patch("install.install_package_manager_lsp_servers"),
                patch("install.install_pre_commit_hooks"),
                patch("install.ensure_node_runtime"),
                patch("install.resolve_npm_availability", return_value=True),
                patch("install.print_language_support_summary"),
            ]
            mocks = [p.start() for p in patches]
            try:
                install.run_install(target_dir=target)
            finally:
                for p in patches:
                    p.stop()

            # Keyed by the same names as _INSTALLER_FOR_KIND so a mismatch
            # produces an immediately readable assertion message.
            called_by_name = {
                "download_binaries": mocks[0].called,
                "install_node_servers": mocks[1].called,
                "download_jdtls": mocks[2].called,
                "install_package_manager_lsp_servers": mocks[3].called,
            }
            for kind in kinds_in_registry:
                fn_name = self._INSTALLER_FOR_KIND[kind]
                self.assertTrue(
                    called_by_name[fn_name],
                    f"run_install did not call {fn_name}(), but {kind} deps "
                    f"exist in TOOL_REGISTRY. Wire the installer into run_install.",
                )


class TestHasRequiredToolsCoversEveryKind(unittest.TestCase):
    """``has_required_tools`` must validate every kind present in the registry.

    Regression this guards: adding a new kind without adding a validation
    branch makes ``needs_install`` loop forever (install succeeds,
    validation says "still missing", repeat). Or the opposite: the check
    silently returns True when the artifact is actually absent, so the
    LSP launch fails at analysis time with a cryptic FileNotFoundError
    (which is exactly the bug this PR fixed for PACKAGE_MANAGER).
    """

    def test_missing_artifact_is_detected_for_every_kind(self):
        """For each kind present in the registry, populate everything, then
        remove one representative artifact of that kind and assert
        ``has_required_tools`` returns False.
        """
        # Lazy import: the helper lives in the test_tool_registry module
        # so we don't duplicate the per-kind fixture setup.
        from tests.test_tool_registry import _populate_complete_servers_dir
        from tool_registry.paths import exe_suffix, platform_bin_dir

        kinds_in_registry = {dep.kind for dep in TOOL_REGISTRY}

        for kind in kinds_in_registry:
            dep = next(d for d in TOOL_REGISTRY if d.kind is kind)
            with self.subTest(kind=kind, dep=dep.key):
                with tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    _populate_complete_servers_dir(base)
                    self.assertTrue(
                        has_required_tools(base),
                        f"Populated dir should pass has_required_tools but didn't — "
                        f"did _populate_complete_servers_dir get a new branch for {kind}?",
                    )

                    bin_dir = platform_bin_dir(base)
                    if kind is ToolKind.NATIVE:
                        (bin_dir / f"{dep.binary_name}{exe_suffix()}").unlink()
                    elif kind is ToolKind.NODE and dep.js_entry_file:
                        import shutil as _shutil

                        _shutil.rmtree(base / "node_modules" / dep.js_entry_parent)
                    elif kind is ToolKind.ARCHIVE and dep.archive_subdir:
                        import shutil as _shutil

                        _shutil.rmtree(base / "bin" / dep.archive_subdir)
                    elif kind is ToolKind.PACKAGE_MANAGER:
                        subdir = dep.archive_subdir or dep.key
                        (bin_dir / "pm-tools" / subdir / f"{dep.binary_name}{exe_suffix()}").unlink()
                    else:
                        self.skipTest(f"Kind {kind} has no deletion rule in this test — extend the match.")

                    self.assertFalse(
                        has_required_tools(base),
                        f"has_required_tools should return False after deleting "
                        f"the {kind} artifact for {dep.key} — the validation branch "
                        f"for this kind is likely missing or wrong.",
                    )

    def test_needs_install_does_not_loop_when_pm_manager_missing(self):
        """PACKAGE_MANAGER deps must be skipped by ``has_required_tools``
        when their ``manager_binary`` is unavailable — otherwise
        ``needs_install`` returns True forever on hosts without that
        toolchain and re-triggers the installer every run.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Deliberately populate only non-PM artifacts.
            from tests.test_tool_registry import _populate_complete_servers_dir
            from tool_registry.paths import exe_suffix, platform_bin_dir

            _populate_complete_servers_dir(base)
            # Delete every PM artifact so the only way for has_required_tools
            # to return True is via the "manager missing -> skip" branch.
            for dep in TOOL_REGISTRY:
                if dep.kind is ToolKind.PACKAGE_MANAGER:
                    subdir = dep.archive_subdir or dep.key
                    p = platform_bin_dir(base) / "pm-tools" / subdir / f"{dep.binary_name}{exe_suffix()}"
                    if p.exists():
                        p.unlink()

            with patch("tool_registry.manifest.shutil.which", return_value=None):
                self.assertTrue(has_required_tools(base))

    def test_needs_install_public_contract(self):
        """Smoke check: ``needs_install`` is callable and returns a bool
        even for a populated dir — protects against signature changes
        that silently break the top-level call in install.py.
        """
        self.assertIsInstance(needs_install(), bool)


class TestRegistryAndVscodeConfigParity(unittest.TestCase):
    """``VSCODE_CONFIG`` and ``TOOL_REGISTRY`` are two parallel lists —
    adding a tool to one without the other produces a silent misconfig
    (resolve_config has nothing to map into, or VSCODE_CONFIG entries
    that never get an absolute path). Enforce parity.
    """

    def test_every_registry_entry_has_matching_vscode_config_entry(self):
        for dep in TOOL_REGISTRY:
            with self.subTest(dep=dep.key):
                section = dep.config_section.value
                self.assertIn(
                    section,
                    VSCODE_CONFIG,
                    f"VSCODE_CONFIG has no section '{section}' for {dep.key}.",
                )
                self.assertIn(
                    dep.key,
                    VSCODE_CONFIG[section],
                    f"TOOL_REGISTRY entry '{dep.key}' has no matching "
                    f"VSCODE_CONFIG[{section!r}][{dep.key!r}] entry. "
                    f"Add it to vscode_constants.py.",
                )
                entry = VSCODE_CONFIG[section][dep.key]
                self.assertIn(
                    "command",
                    entry,
                    f"VSCODE_CONFIG[{section!r}][{dep.key!r}] is missing 'command'.",
                )
                cmd = entry["command"]
                self.assertTrue(
                    isinstance(cmd, list) and cmd,
                    f"VSCODE_CONFIG[{section!r}][{dep.key!r}]['command'] must be a non-empty list.",
                )
                # cmd[0] must reference either the registry ``binary_name``
                # (NATIVE/ARCHIVE/PM) or the ``js_entry_file`` (NODE). Any
                # other value means ``resolve_config`` has no path to fill in.
                acceptable = {dep.binary_name}
                if dep.js_entry_file:
                    acceptable.add(dep.js_entry_file)
                self.assertIn(
                    cmd[0],
                    acceptable,
                    f"VSCODE_CONFIG['command'][0] is {cmd[0]!r} but TOOL_REGISTRY "
                    f"declares binary_name={dep.binary_name!r} / js_entry_file="
                    f"{dep.js_entry_file!r}. resolve_config won't know which slot "
                    f"to rewrite unless one of these matches.",
                )

    def test_every_vscode_config_entry_has_matching_registry_entry(self):
        registry_keys_by_section: dict[str, set[str]] = {}
        for dep in TOOL_REGISTRY:
            registry_keys_by_section.setdefault(dep.config_section.value, set()).add(dep.key)
        for section_name, section in VSCODE_CONFIG.items():
            for key in section:
                with self.subTest(section=section_name, key=key):
                    self.assertIn(
                        key,
                        registry_keys_by_section.get(section_name, set()),
                        f"VSCODE_CONFIG[{section_name!r}][{key!r}] has no matching "
                        f"TOOL_REGISTRY entry. Either add one, or remove the orphan "
                        f"config block — codeboarding-setup won't know how to install it.",
                    )


class TestLspAdapterAndLanguageEnumParity(unittest.TestCase):
    """Every LSP registered in ``TOOL_REGISTRY`` must have:
    1. A ``LanguageAdapter`` subclass in ``ADAPTER_REGISTRY`` that can index it.
    2. A corresponding value in the ``Language`` enum.

    Regression this guards: registering an LSP but forgetting to add the
    adapter means the tool downloads but nothing ever spawns it; omitting
    the enum value means detection can't route files to the new language.
    """

    # Map from VSCODE_CONFIG language id (lowercase, e.g. "csharp") to
    # ADAPTER_REGISTRY key (e.g. "CSharp"). The adapter registry key is
    # the PascalCase form produced by ``LanguageAdapter.language``.
    _LANGUAGE_ID_TO_ADAPTER_KEY: dict[str, str] = {
        "python": "Python",
        "typescript": "TypeScript",
        "javascript": "JavaScript",
        "go": "Go",
        "php": "PHP",
        "csharp": "CSharp",
        "java": "Java",
        "rust": "Rust",
    }

    def test_every_lsp_tool_has_an_adapter_per_supported_language(self):
        for dep in TOOL_REGISTRY:
            if dep.config_section != ConfigSection.LSP_SERVERS:
                continue
            config_entry = VSCODE_CONFIG["lsp_servers"].get(dep.key, {})
            languages = config_entry.get("languages") or [dep.key]
            for lang in languages:
                with self.subTest(dep=dep.key, language=lang):
                    adapter_key = self._LANGUAGE_ID_TO_ADAPTER_KEY.get(lang)
                    self.assertIsNotNone(
                        adapter_key,
                        f"Language id {lang!r} (from VSCODE_CONFIG[{dep.key!r}]['languages']) "
                        f"is not mapped to an ADAPTER_REGISTRY key here. Add the mapping "
                        f"and the matching adapter.",
                    )
                    self.assertIn(
                        adapter_key,
                        ADAPTER_REGISTRY,
                        f"ADAPTER_REGISTRY has no adapter {adapter_key!r} for "
                        f"LSP dep {dep.key!r}. Register the adapter in "
                        f"static_analyzer/engine/adapters/__init__.py.",
                    )

    def test_every_lsp_language_is_in_the_language_enum(self):
        language_values = {lang.value for lang in Language}
        for dep in TOOL_REGISTRY:
            if dep.config_section != ConfigSection.LSP_SERVERS:
                continue
            config_entry = VSCODE_CONFIG["lsp_servers"].get(dep.key, {})
            languages = config_entry.get("languages") or [dep.key]
            for lang in languages:
                with self.subTest(dep=dep.key, language=lang):
                    self.assertIn(
                        lang,
                        language_values,
                        f"Language id {lang!r} from VSCODE_CONFIG has no matching "
                        f"entry in the Language enum (static_analyzer/constants.py). "
                        f"File detection won't be able to route to this language.",
                    )


class TestPackageManagerSourceInvariants(unittest.TestCase):
    """Structural checks for every ``PackageManagerToolSource`` in the
    registry. Catches copy-paste errors on the next PM-installed tool
    (pipx, cargo install, gem install, ...).
    """

    def _pm_deps(self):
        return [d for d in TOOL_REGISTRY if d.kind is ToolKind.PACKAGE_MANAGER]

    def test_registry_has_at_least_one_pm_dep(self):
        """If this ever fails, either PM support was removed (delete this
        whole test class) or the csharp entry regressed to NATIVE.
        """
        self.assertTrue(self._pm_deps(), "No PACKAGE_MANAGER deps in TOOL_REGISTRY.")

    def test_every_pm_dep_has_a_package_manager_source(self):
        for dep in self._pm_deps():
            with self.subTest(dep=dep.key):
                self.assertIsInstance(
                    dep.source,
                    PackageManagerToolSource,
                    f"{dep.key}: kind=PACKAGE_MANAGER but source is {type(dep.source).__name__}.",
                )

    def test_every_pm_dep_declares_manager_and_install_args(self):
        for dep in self._pm_deps():
            source = dep.source
            assert isinstance(source, PackageManagerToolSource)
            with self.subTest(dep=dep.key):
                self.assertTrue(
                    source.manager_binary,
                    f"{dep.key}: PackageManagerToolSource.manager_binary is empty — "
                    f"the installer wouldn't know which executable to invoke.",
                )
                self.assertTrue(
                    source.install_args,
                    f"{dep.key}: PackageManagerToolSource.install_args is empty — "
                    f"the installer would run a bare {source.manager_binary!r}.",
                )

    def test_every_pm_dep_has_exactly_one_tool_path_placeholder(self):
        """The installer substitutes ``{tool_path}`` with the managed install
        dir. Zero placeholders means the tool lands in the package manager's
        default location (polluting the user's global namespace, and
        ``has_required_tools`` won't find it). Multiple placeholders is a
        copy-paste bug.
        """
        for dep in self._pm_deps():
            source = dep.source
            assert isinstance(source, PackageManagerToolSource)
            with self.subTest(dep=dep.key):
                placeholder_count = sum(arg.count("{tool_path}") for arg in source.install_args)
                self.assertEqual(
                    placeholder_count,
                    1,
                    f"{dep.key}: expected exactly one '{{tool_path}}' placeholder in "
                    f"install_args, found {placeholder_count}. "
                    f"install_args={list(source.install_args)!r}",
                )


if __name__ == "__main__":
    unittest.main()
