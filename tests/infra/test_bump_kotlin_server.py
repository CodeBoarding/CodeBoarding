"""The nightly job proposes a newer kotlin-lsp and leaves the merge to a person."""

import base64
import importlib
import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infra.tool_registry import TOOL_REGISTRY, kotlin_lsp_source

SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"
KOTLIN = next(dep for dep in TOOL_REGISTRY if dep.key == "kotlin")
CURRENT = KOTLIN.source.tag  # type: ignore[union-attr]
major = int(CURRENT.split(".")[0])
NEWER = f"{major + 1}.1.0"
NEWEST = f"{major + 2}.1.0"


def checksums(version: str) -> dict[str, str]:
    assets = [name.replace(CURRENT, version) for name in KOTLIN.source.asset_arch_overrides.values()]  # type: ignore[union-attr]
    return {name: f"{index:x}" * 64 for index, name in enumerate(assets)}


class Upstream:
    """GitHub's and JetBrains' side of a run: what they answer, and every change the job asks of GitHub."""

    def __init__(self, bump, marketplace=NEWER, github=NEWER, published=(NEWER, NEWEST), pull_requests=()):
        self.pull_requests = list(pull_requests)
        self.branches: set[str] = set()
        self.writes: list[tuple[str, ...]] = []
        self.published = published
        self.bump = bump
        self.marketplace = marketplace
        self.github = github

    def gh(self, *args: str) -> str:
        if args[:2] == ("pr", "list"):
            return json.dumps(self.pull_requests)
        if args[:2] == ("api", "repos/Kotlin/kotlin-lsp/releases/latest"):
            return f"kotlin-lsp/v{self.github}"
        if args[0] == "api" and args[1].endswith("/git/ref/heads/main"):
            return "base-commit"
        self.writes.append(args)
        return "https://github.com/CodeBoarding/CodeBoarding/pull/1" if args[:2] == ("pr", "create") else ""

    def gh_lookup(self, *args: str) -> str | None:
        if "/contents/" in args[1]:
            return "pin-blob"
        return "exists" if args[1].rsplit("/heads/")[-1] in self.branches else None

    def marketplace_build(self) -> str:
        if isinstance(self.marketplace, Exception):
            raise self.marketplace
        return self.marketplace

    def published_checksums(self, version: str) -> dict[str, str] | None:
        return checksums(version) if version in self.published else None

    def run(self, *argv: str) -> None:
        with (
            patch.object(self.bump, "gh", self.gh),
            patch.object(self.bump, "gh_lookup", self.gh_lookup),
            patch.object(self.bump, "marketplace_build", self.marketplace_build),
            patch.object(self.bump, "published_checksums", self.published_checksums),
            patch.object(self.bump, "product_info", return_value={"majorVersionReleaseDate": "20261014"}),
            patch.object(sys, "argv", ["bump_kotlin_server.py", *argv]),
        ):
            self.bump.main()

    def written(self, *command: str) -> list[tuple[str, ...]]:
        return [write for write in self.writes if write[: len(command)] == command]


@pytest.fixture
def bump(monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS)
    return importlib.import_module("bump_kotlin_server")


def bump_pr(number: int, version: str, state: str = "OPEN") -> dict:
    return {"number": number, "state": state, "headRefName": f"kotlin-server/{version}"}


def value_of(write: tuple[str, ...], field: str) -> str:
    return next(arg.split("=", 1)[1] for arg in write if arg.startswith(f"{field}="))


class TestANewerBuild:
    def test_opens_one_pull_request_for_a_person_to_merge(self, bump):
        upstream = Upstream(bump)
        upstream.run()

        created = upstream.written("api", "-X", "POST")
        assert [value_of(write, "ref") for write in created] == [f"refs/heads/kotlin-server/{NEWER}"]
        assert value_of(created[0], "sha") == "base-commit"

        [commit] = upstream.written("api", "-X", "PUT")
        assert commit[3].endswith("/contents/infra/tool_registry/kotlin-server-1.json")
        assert value_of(commit, "branch") == f"kotlin-server/{NEWER}"
        assert value_of(commit, "sha") == "pin-blob"
        pin = json.loads(base64.b64decode(value_of(commit, "content")))
        assert kotlin_lsp_source(pin).tag == NEWER

        [opened] = upstream.written("pr", "create")
        assert opened[opened.index("--head") + 1] == f"kotlin-server/{NEWER}"
        assert opened[opened.index("--base") + 1] == "main"
        assert opened[opened.index("--reviewer") + 1] == "Svilen-Stefanov"
        body = opened[opened.index("--body") + 1]
        assert "2026-10-14" in body and "Days left" in body
        assert f"https://github.com/Kotlin/kotlin-lsp/releases/tag/kotlin-lsp/v{NEWER}" in body

        assert not any("merge" in part for write in upstream.writes for part in write)

    def test_the_suite_runs_as_a_check_on_that_branch(self, bump):
        """Nothing dispatches it: GitHub leaves workflow_dispatch runs out of a pull request's checks."""
        workflow = (SCRIPTS.parent / "workflows" / "integration-tests.yml").read_text()
        assert f"startsWith(github.head_ref, '{bump.BRANCH_PREFIX}')" in workflow
        assert "- 'infra/tool_registry/**'" in workflow

    def test_a_build_on_the_marketplace_first_is_proposed_with_a_marketplace_link(self, bump):
        upstream = Upstream(bump, marketplace=NEWEST, github=CURRENT)
        upstream.run()
        [opened] = upstream.written("pr", "create")
        assert opened[opened.index("--head") + 1] == f"kotlin-server/{NEWEST}"
        assert "marketplace.visualstudio.com" in opened[opened.index("--body") + 1]

    def test_a_build_not_yet_up_at_jetbrains_gives_way_to_the_next(self, bump):
        upstream = Upstream(bump, marketplace=NEWEST, github=NEWER, published=(NEWER,))
        upstream.run()
        [opened] = upstream.written("pr", "create")
        assert opened[opened.index("--head") + 1] == f"kotlin-server/{NEWER}"

    def test_a_branch_left_by_a_run_that_died_is_replaced(self, bump):
        upstream = Upstream(bump)
        upstream.branches = {f"kotlin-server/{NEWER}"}
        upstream.run()
        assert upstream.written("api", "-X", "DELETE")
        assert len(upstream.written("pr", "create")) == 1


class TestNothingToOpen:
    def test_no_newer_build(self, bump):
        upstream = Upstream(bump, marketplace=CURRENT, github=CURRENT)
        upstream.run()
        assert upstream.writes == []

    @pytest.mark.parametrize("state", ["OPEN", "CLOSED", "MERGED"])
    def test_a_version_that_already_has_a_pull_request(self, bump, state):
        upstream = Upstream(bump, pull_requests=[bump_pr(5, NEWER, state)])
        upstream.run()
        assert upstream.writes == []

    def test_a_dry_run_changes_nothing(self, bump, capsys):
        upstream = Upstream(bump, pull_requests=[bump_pr(5, NEWER)], marketplace=NEWEST)
        upstream.run("--dry-run")
        assert upstream.writes == []
        assert f"kotlin-server/{NEWEST}" in capsys.readouterr().out


class TestSuperseding:
    def test_a_newer_build_closes_the_older_pull_request(self, bump):
        upstream = Upstream(bump, marketplace=NEWEST, pull_requests=[bump_pr(5, NEWER)])
        upstream.run()
        assert len(upstream.written("pr", "create")) == 1
        [closed] = upstream.written("pr", "close")
        assert closed[2] == "5" and "--delete-branch" in closed
        assert closed[closed.index("--comment") + 1].endswith("/pull/1")

    def test_a_closed_pull_request_is_left_alone(self, bump):
        upstream = Upstream(bump, marketplace=NEWEST, pull_requests=[bump_pr(5, NEWER, "CLOSED")])
        upstream.run()
        assert upstream.written("pr", "close") == []

    def test_a_bump_the_pin_has_passed_is_closed(self, bump):
        upstream = Upstream(bump, marketplace=CURRENT, github=CURRENT, pull_requests=[bump_pr(5, f"{major - 1}.1.0")])
        upstream.run()
        assert len(upstream.written("pr", "close")) == 1


class TestAFailingSource:
    def test_the_other_source_still_gets_its_build_proposed_and_the_run_goes_red(self, bump):
        upstream = Upstream(bump, marketplace=ConnectionError("down"))
        with pytest.raises(SystemExit, match="Marketplace"):
            upstream.run()
        assert len(upstream.written("pr", "create")) == 1

    def test_a_malformed_build_number_is_refused(self, bump):
        upstream = Upstream(bump, marketplace="../../evil", github=CURRENT)
        with pytest.raises(SystemExit, match="not a kotlin-lsp version"):
            upstream.run()
        assert upstream.writes == []


class TestRemoteZip:
    @staticmethod
    def _serve(archive: bytes, partial: bool = True):
        def head(_url, **_kwargs):
            return MagicMock(headers={"content-length": str(len(archive))})

        def get(_url, headers, **_kwargs):
            first, last = headers["Range"].removeprefix("bytes=").split("-")
            return MagicMock(status_code=206 if partial else 200, content=archive[int(first) : int(last) + 1])

        return patch("requests.head", head), patch("requests.get", get)

    @staticmethod
    def _archive() -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("kotlin-server/lib/app.jar", b"x" * 100_000)
            archive.writestr("kotlin-server/product-info.json", json.dumps({"buildNumber": NEWER}))
        return buffer.getvalue()

    def test_reads_one_member_by_byte_ranges(self, bump):
        head, get = self._serve(self._archive())
        with head, get:
            assert bump.product_info("https://example.test/server.zip") == {"buildNumber": NEWER}

    def test_a_host_without_byte_ranges_is_an_error_not_a_garbled_read(self, bump):
        head, get = self._serve(self._archive(), partial=False)
        with head, get, pytest.raises(ValueError, match="byte ranges"):
            bump.product_info("https://example.test/server.zip")
