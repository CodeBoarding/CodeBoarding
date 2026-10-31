"""kotlin-lsp's pin moves on main, and an installed Core follows it without a package upgrade."""

import json
import shutil
import stat
import tomllib
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from infra.tool_registry import (
    TOOL_REGISTRY,
    ToolKind,
    archive_launcher_path,
    install_archive_tool,
    needs_install,
    platform_bin_dir,
    required_tools,
    resolve_config,
    tool_fingerprint,
    write_manifest,
)
from infra.tool_registry import installers

KOTLIN = next(dep for dep in TOOL_REGISTRY if dep.key == "kotlin")
WHEEL = KOTLIN.source.tag  # type: ignore[union-attr]
PIN_URL = "https://raw.githubusercontent.com/CodeBoarding/CodeBoarding/main/infra/tool_registry/kotlin-server-1.json"
REPO_ROOT = Path(__file__).resolve().parents[2]

major = int(WHEEL.split(".")[0])
OLDER = f"{major - 1}.1.0"
NEWER = f"{major + 1}.1.0"
NEWEST = f"{major + 2}.1.0"


@pytest.fixture(autouse=True)
def no_live_kotlin_pin(monkeypatch):
    """Replaces the suite-wide fixture: these tests serve main's copy themselves."""
    monkeypatch.setattr(installers, "_kotlin_lsp_pin_read", None, raising=False)


@pytest.fixture
def servers(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("infra.tool_registry.manifest.installed_version", lambda: "1.0.0")
    base = tmp_path / ".codeboarding" / "servers"
    tokei = platform_bin_dir(base) / "tokei"
    tokei.parent.mkdir(parents=True)
    tokei.write_text("#!/bin/sh\n")
    tokei.chmod(0o755)
    return base


def pin(version: str) -> dict:
    assets = [name.replace(WHEEL, version) for name in KOTLIN.source.asset_arch_overrides.values()]  # type: ignore[union-attr]
    return {"version": version, "sha256": {name: f"{index:x}" * 64 for index, name in enumerate(assets)}}


def main_serves(body=None, error: Exception | None = None):
    """Stand in for ``requests.get``: *body* is what main's pin file reads as, *error* is how the read fails."""
    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    if isinstance(body, str):
        response.json.side_effect = requests.exceptions.JSONDecodeError("not JSON", body, 0)
    else:
        response.json.return_value = body

    def get(url, **_kwargs):
        assert url == PIN_URL
        if error:
            raise error
        return response

    return patch("requests.get", side_effect=get)


def install_on_disk(base: Path, version: str) -> None:
    launcher = archive_launcher_path(base, KOTLIN)
    assert launcher is not None
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\n")
    (base / "bin" / "kotlin-lsp" / installers.ARCHIVE_VERSION_STAMP).write_text(version)


def stamp(base: Path) -> str:
    return (base / "bin" / "kotlin-lsp" / installers.ARCHIVE_VERSION_STAMP).read_text()


def run_installer(base: Path, tmp_path: Path) -> list[tuple[str, str | None]]:
    """Run the installer with a stubbed download; returns what it fetched, with the checksum it demanded."""
    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        launcher = zipfile.ZipInfo("kotlin-server/bin/intellij-server")
        launcher.external_attr = (stat.S_IFREG | 0o755) << 16
        zf.writestr(launcher, "#!/bin/sh\n")

    fetched: list[tuple[str, str | None]] = []

    def fake_download(url: str, destination: Path, expected_sha256: str | None = None) -> bool:
        fetched.append((url, expected_sha256))
        shutil.copy(archive, destination)
        return True

    with (
        patch("infra.tool_registry.installers.download_asset", side_effect=fake_download),
        patch("infra.tool_registry.installers.platform.system", return_value="Darwin"),
        patch("infra.tool_registry.installers.platform.machine", return_value="arm64"),
    ):
        install_archive_tool(base, KOTLIN)
    return fetched


class TestFollowsMain:
    def test_a_machine_on_an_old_pin_picks_up_the_pin_on_main(self, servers, tmp_path):
        install_on_disk(servers, WHEEL)
        with main_serves(pin(WHEEL)):
            write_manifest(required_tools({"kotlin"}))
            assert not needs_install({"kotlin"}), "the machine must start current on the wheel's pin"

        served = pin(NEWER)
        installers._kotlin_lsp_pin_read = None
        with main_serves(served):
            assert needs_install({"kotlin"}), "main moved on and this machine did not notice"

            fetched = run_installer(servers, tmp_path)
            asset = f"kotlin-server-{NEWER}-aarch64.sit"
            assert fetched == [
                (
                    f"https://download.jetbrains.com/language-server/kotlin-server/{NEWER}/{asset}",
                    served["sha256"][asset],
                )
            ]
            assert stamp(servers) == NEWER

            write_manifest(required_tools({"kotlin"}))
            assert not needs_install({"kotlin"}), "the new build must be taken as current, not installed on every run"

    def test_a_checkout_ahead_of_main_keeps_its_own_pin(self, servers):
        """A bump's own CI runs this way: its branch is ahead of main, and it must test the new build."""
        install_on_disk(servers, WHEEL)
        write_manifest(required_tools({"kotlin"}))
        with main_serves(pin(OLDER)):
            assert not needs_install({"kotlin"})

    def test_reverting_a_bad_build_on_main_moves_a_machine_back(self, servers, tmp_path):
        install_on_disk(servers, NEWEST)
        with main_serves(pin(NEWER)):
            write_manifest(required_tools({"kotlin"}))
            assert needs_install({"kotlin"})
            run_installer(servers, tmp_path)
            assert stamp(servers) == NEWER

    def test_main_is_read_once_however_many_times_a_run_asks(self, servers):
        install_on_disk(servers, WHEEL)
        with main_serves(pin(WHEEL)) as get:
            needs_install({"kotlin"})
            needs_install({"kotlin"})
            write_manifest(required_tools({"kotlin"}))
        assert get.call_count == 1

    def test_a_long_running_process_reads_main_again(self, servers):
        install_on_disk(servers, WHEEL)
        with main_serves(pin(WHEEL)) as get, patch("infra.tool_registry.installers.time.monotonic") as now:
            now.return_value = 0
            write_manifest(required_tools({"kotlin"}))
            needs_install({"kotlin"})
            assert get.call_count == 1
            now.return_value = installers.KOTLIN_LSP_PIN_MAX_AGE_SECONDS
            needs_install({"kotlin"})
        assert get.call_count == 2


class TestOnlyWhenKotlinIsNeeded:
    def test_a_run_without_kotlin_never_reads_main(self, servers):
        with patch("requests.get") as get:
            needs_install({"python"})
            needs_install(())
            write_manifest(required_tools({"python"}))
            resolve_config(servers)
        get.assert_not_called()

    def test_finding_the_installed_server_never_reads_main(self, servers):
        install_on_disk(servers, NEWER)
        with patch("requests.get") as get:
            config = resolve_config(servers)
        get.assert_not_called()
        assert config["lsp_servers"]["kotlin"]["command"][0] == str(archive_launcher_path(servers, KOTLIN))


FAILED_READS = [
    pytest.param({"error": requests.ConnectionError("offline")}, id="offline"),
    pytest.param({"error": requests.Timeout("slow")}, id="timeout"),
    pytest.param({"body": "<html>rate limited</html>"}, id="not-json"),
    pytest.param({"body": ["a", "list"]}, id="not-an-object"),
]


class TestAFailedRead:
    @pytest.mark.parametrize("read", FAILED_READS)
    def test_a_newer_installed_build_stays(self, servers, tmp_path, read):
        install_on_disk(servers, NEWER)
        with main_serves(**read):
            write_manifest(required_tools({"kotlin"}))
            assert not needs_install({"kotlin"})
            assert run_installer(servers, tmp_path) == []
        assert stamp(servers) == NEWER

    @pytest.mark.parametrize("read", FAILED_READS)
    def test_an_installed_build_older_than_the_wheel_moves_up_to_the_wheel(self, servers, tmp_path, read):
        install_on_disk(servers, OLDER)
        with main_serves(**read):
            write_manifest(required_tools({"kotlin"}))
            assert needs_install({"kotlin"})
            fetched = run_installer(servers, tmp_path)
        assert [url.split("/")[-2] for url, _ in fetched] == [WHEEL]
        assert stamp(servers) == WHEEL

    def test_nothing_installed_gets_the_wheel_pin_with_its_checksum(self, servers, tmp_path):
        with main_serves(error=requests.ConnectionError("offline")):
            fetched = run_installer(servers, tmp_path)
        asset = f"kotlin-server-{WHEEL}-aarch64.sit"
        assert fetched == [
            (
                f"https://download.jetbrains.com/language-server/kotlin-server/{WHEEL}/{asset}",
                KOTLIN.source.sha256[asset],  # type: ignore[union-attr]
            )
        ]

    def test_a_stamp_left_without_its_launcher_is_not_trusted(self, servers, tmp_path):
        """The installed build has no checksums here, so it must never be what gets downloaded."""
        install_on_disk(servers, NEWER)
        launcher = archive_launcher_path(servers, KOTLIN)
        assert launcher is not None
        launcher.unlink()
        with main_serves(error=requests.ConnectionError("offline")):
            fetched = run_installer(servers, tmp_path)
        assert [url.split("/")[-2] for url, _ in fetched] == [WHEEL]

    def test_a_garbled_stamp_is_not_a_build(self, servers):
        install_on_disk(servers, "not a version")
        with main_serves(error=requests.ConnectionError("offline")):
            assert tool_fingerprint(KOTLIN).startswith(WHEEL)
        assert resolve_config(servers)["lsp_servers"]["kotlin"]["command"][0] != str(
            archive_launcher_path(servers, KOTLIN)
        )

    def test_a_run_does_not_fail(self, servers):
        with main_serves(error=requests.ConnectionError("offline")):
            assert needs_install({"kotlin"}) is True


class TestAFailedDownload:
    def test_the_installed_build_stays_in_use(self, servers):
        install_on_disk(servers, NEWER)
        with (
            main_serves(pin(NEWEST)),
            patch("infra.tool_registry.installers.download_asset", side_effect=requests.ConnectionError("cut off")),
        ):
            install_archive_tool(servers, KOTLIN)
            assert needs_install({"kotlin"}), "the next run must try again"
        assert stamp(servers) == NEWER
        assert resolve_config(servers)["lsp_servers"]["kotlin"]["command"][0] == str(
            archive_launcher_path(servers, KOTLIN)
        )


def _with(**changes) -> dict:
    return {**pin(NEWER), **changes}


def _with_sha256(change) -> dict:
    sha256 = dict(pin(NEWER)["sha256"])
    change(sha256)
    return _with(sha256=sha256)


MALFORMED = [
    pytest.param(_with(version="../../evil"), id="path-traversal"),
    pytest.param(_with(version="263.7084.0/../../x"), id="traversal-after-a-version"),
    pytest.param(_with(version="263.7084"), id="two-parts"),
    pytest.param(_with(version="263.7084.0.1"), id="four-parts"),
    pytest.param(_with(version="263.7084.0\n"), id="trailing-newline"),
    pytest.param(_with(version="v263.7084.0"), id="prefix"),
    pytest.param(_with(version="\u0662\u0666\u0663.\u0667\u0660\u0668\u0664.\u0660"), id="non-ascii-digits"),
    pytest.param(_with(version="263.7084.0?x=1"), id="query"),
    pytest.param(_with(version=263), id="number"),
    pytest.param(_with(version=None), id="null"),
    pytest.param(_with(sha256="deadbeef"), id="sha256-not-an-object"),
    pytest.param(_with(sha256={}), id="no-checksums"),
    pytest.param(_with_sha256(lambda sha: sha.popitem()), id="five-checksums"),
    pytest.param(_with_sha256(lambda sha: sha.update({"extra.tar.gz": "0" * 64})), id="seven-checksums"),
    pytest.param(_with_sha256(lambda sha: sha.update({next(iter(sha)): "abc"})), id="short-checksum"),
    pytest.param(_with_sha256(lambda sha: sha.update({next(iter(sha)): "A" * 64})), id="uppercase-checksum"),
    pytest.param(_with_sha256(lambda sha: sha.update({next(iter(sha)): 1})), id="checksum-not-a-string"),
    pytest.param(pin(NEWER) | {"sha256": pin(NEWEST)["sha256"]}, id="checksums-for-another-build"),
]


class TestAMalformedPin:
    @pytest.mark.parametrize("served", MALFORMED)
    def test_it_never_reaches_a_url(self, servers, tmp_path, served):
        with main_serves(served):
            fetched = run_installer(servers, tmp_path)
        assert [url.split("/")[-2] for url, _ in fetched] == [WHEEL]
        assert stamp(servers) == WHEEL


class TestPublicContract:
    """Every installed Core reads this path for as long as it runs, so a refactor must not move the file."""

    PATH = REPO_ROOT / "infra" / "tool_registry" / "kotlin-server-1.json"

    def test_installed_core_reads_the_file_that_ships_in_the_wheel(self):
        assert installers.KOTLIN_LSP_PIN_URL == PIN_URL
        assert PIN_URL.endswith(self.PATH.relative_to(REPO_ROOT).as_posix())
        pin_file = json.loads(self.PATH.read_text())
        assert pin_file["version"] == WHEEL
        assert pin_file["sha256"] == KOTLIN.source.sha256  # type: ignore[union-attr]

    def test_the_wheel_carries_it(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as pyproject:
            package_data = tomllib.load(pyproject)["tool"]["setuptools"]["package-data"]
        assert self.PATH.name in package_data["infra.tool_registry"]

    def test_the_server_is_never_mirrored(self):
        assert KOTLIN.kind is ToolKind.ARCHIVE
        assert KOTLIN.source.url_template.startswith("https://download.jetbrains.com/")  # type: ignore[union-attr]
        assert set(KOTLIN.source.sha256) == set(KOTLIN.source.asset_arch_overrides.values())  # type: ignore[union-attr]
