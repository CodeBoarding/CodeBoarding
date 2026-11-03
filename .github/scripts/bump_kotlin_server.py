"""Open a pull request when JetBrains ships a newer kotlin-lsp. Merging it is what publishes the build."""

import argparse
import base64
import io
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from check_kotlin_server import days_left
from infra.tool_registry import (
    KOTLIN_LSP_ASSETS,
    KOTLIN_LSP_PIN_FILE,
    KOTLIN_LSP_URL_TEMPLATE,
    kotlin_lsp_build,
    kotlin_lsp_source,
)

PIN_PATH = f"infra/tool_registry/{KOTLIN_LSP_PIN_FILE}"
REVIEWER = "Svilen-Stefanov"
BRANCH_PREFIX = "kotlin-server/"
GITHUB_RELEASE = "https://github.com/Kotlin/kotlin-lsp/releases/tag/kotlin-lsp/v{version}"
MARKETPLACE_ITEM = "https://marketplace.visualstudio.com/items?itemName=JetBrains.kotlin-server"
MARKETPLACE_QUERY = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
MARKETPLACE_INCLUDE_VERSIONS_AND_FILES = 0x1 | 0x2
TIMEOUT_SECONDS = 60


class RemoteFile(io.RawIOBase):
    """Enough of a file for ``zipfile`` to read one member of a 360 MB archive without downloading the archive."""

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.position = 0
        response = requests.head(url, allow_redirects=True, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        self.size = int(response.headers["content-length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self.position = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}[whence] + offset
        return self.position

    def readinto(self, buffer: Any) -> int:
        if self.position >= self.size:
            return 0
        last = min(self.position + len(buffer), self.size) - 1
        response = requests.get(self.url, headers={"Range": f"bytes={self.position}-{last}"}, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        if response.status_code != 206:
            raise ValueError(f"{self.url} does not serve byte ranges")
        buffer[: len(response.content)] = response.content
        self.position += len(response.content)
        return len(response.content)


def product_info(url: str) -> dict[str, Any]:
    """``product-info.json`` from a zip of the server: its build number and its build date."""
    with zipfile.ZipFile(io.BufferedReader(RemoteFile(url), 1 << 16)) as archive:
        name = next(name for name in archive.namelist() if name.endswith("product-info.json"))
        return json.loads(archive.read(name))


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, stdout=subprocess.PIPE, text=True).stdout.strip()


def gh_lookup(*args: str) -> str | None:
    """A ``gh`` read of something that may not exist."""
    done = subprocess.run(["gh", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def github_build() -> str:
    return gh("api", "repos/Kotlin/kotlin-lsp/releases/latest", "-q", ".tag_name").removeprefix("kotlin-lsp/v")


def marketplace_build() -> str:
    """The build inside the newest extension, whose own version (``0.0.27``) says nothing about it."""
    response = requests.post(
        MARKETPLACE_QUERY,
        json={
            "filters": [{"criteria": [{"filterType": 7, "value": "JetBrains.kotlin-server"}]}],
            "flags": MARKETPLACE_INCLUDE_VERSIONS_AND_FILES,
        },
        headers={"Accept": "application/json;api-version=7.2-preview.1"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    versions = response.json()["results"][0]["extensions"][0]["versions"]
    version = next(version for version in versions if version.get("targetPlatform") == "linux-x64")
    package = next(
        file["source"]
        for file in version["files"]
        if file["assetType"] == "Microsoft.VisualStudio.Services.VSIXPackage"
    )
    return product_info(package)["buildNumber"]


def upstream_builds() -> tuple[dict[str, str], list[str]]:
    """Each build upstream offers with a link to it, and what could not be asked.

    The Marketplace comes first so that GitHub's link wins for a build on both. A build has reached the Marketplace 11
    days ahead of GitHub, so one source being down must not hide a build on the other.
    """
    builds: dict[str, str] = {}
    failures: list[str] = []
    try:
        version = marketplace_build()
        kotlin_lsp_build(version)
        builds[version] = MARKETPLACE_ITEM
    except Exception as error:
        failures.append(f"Could not read the VS Code Marketplace: {error!r}")
    try:
        version = github_build()
        kotlin_lsp_build(version)
        builds[version] = GITHUB_RELEASE.format(version=version)
    except Exception as error:
        failures.append(f"Could not read the GitHub releases: {error!r}")
    return builds, failures


def asset_url(version: str, asset: str) -> str:
    return KOTLIN_LSP_URL_TEMPLATE.format(version=version, asset=asset)


def published_checksums(version: str) -> dict[str, str] | None:
    """The checksum JetBrains publishes beside each archive, or None until all six archives are up."""
    checksums: dict[str, str] = {}
    for template in KOTLIN_LSP_ASSETS.values():
        asset = template.format(version=version)
        response = requests.get(asset_url(version, f"{asset}.sha256"), timeout=TIMEOUT_SECONDS)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        checksums[asset] = response.text.split()[0]
    return checksums


def branch_of(version: str) -> str:
    return f"{BRANCH_PREFIX}{version}"


def pull_request_body(current: str, version: str, link: str) -> str:
    windows_asset = KOTLIN_LSP_ASSETS[("Windows", "AMD64")].format(version=version)
    built = product_info(asset_url(version, windows_asset))["majorVersionReleaseDate"]
    return (
        f"kotlin-lsp {version} is out and main is on {current}. Merging this publishes the new build to every "
        "installed Core, with no release.\n\n"
        f"- **Built:** {built[:4]}-{built[4:6]}-{built[6:]}\n"
        f"- **Days left:** about {days_left(built)}\n"
        f"- **Upstream:** {link}\n\n"
        "The **Kotlin server** check starts this build. The **Integration Tests** check runs the Kotlin suite "
        "against this branch.\n"
    )


@dataclass(frozen=True)
class Repository:
    name: str
    base: str
    reviewer: str
    dry_run: bool

    def write(self, *args: str, shows: str = "") -> str:
        """Run a ``gh`` command that changes something; a dry run prints it, and *shows* in place of its arguments."""
        if self.dry_run:
            print(f"Would run: gh {shows or ' '.join(args)}")
            return "<new pull request>"
        return gh(*args)

    def bumps(self) -> list[dict[str, Any]]:
        """Bumps into the base branch only, so a test run into a scratch branch neither blocks nor closes real ones."""
        fields = "number,state,headRefName,baseRefName"
        pull_requests = json.loads(
            gh("pr", "list", "--repo", self.name, "--state", "all", "--limit", "200", "--json", fields)
        )
        return [
            pr for pr in pull_requests if pr["headRefName"].startswith(BRANCH_PREFIX) and pr["baseRefName"] == self.base
        ]

    def open_bump(self, current: str, pin: dict[str, Any], link: str) -> str:
        """Commit *pin* on the build's own branch and open the pull request, which is what starts its checks."""
        version = pin["version"]
        branch = branch_of(version)
        print(f"Opening a pull request for kotlin-lsp {version} on {branch}")
        # A run that died after pushing left this branch without a pull request.
        if gh_lookup("api", f"repos/{self.name}/git/ref/heads/{branch}") is not None:
            self.write("api", "-X", "DELETE", f"repos/{self.name}/git/refs/heads/{branch}")
        base_commit = gh("api", f"repos/{self.name}/git/ref/heads/{self.base}", "-q", ".object.sha")
        self.write(
            "api",
            "-X",
            "POST",
            f"repos/{self.name}/git/refs",
            "-f",
            f"ref=refs/heads/{branch}",
            "-f",
            f"sha={base_commit}",
        )

        message = f"chore(kotlin): bump kotlin-lsp to {version}"
        content = json.dumps(pin, indent=2) + "\n"
        put = ["-f", f"message={message}", "-f", f"branch={branch}"]
        put += ["-f", f"content={base64.b64encode(content.encode()).decode()}"]
        if blob := gh_lookup("api", f"repos/{self.name}/contents/{PIN_PATH}?ref={self.base}", "-q", ".sha"):
            put += ["-f", f"sha={blob}"]
        self.write(
            "api", "-X", "PUT", f"repos/{self.name}/contents/{PIN_PATH}", *put, shows=f"api PUT {PIN_PATH}:\n{content}"
        )

        body = pull_request_body(current, version, link)
        create = ["pr", "create", "--repo", self.name, "--base", self.base, "--head", branch, "--title", message]
        url = self.write(
            *create,
            "--body",
            body,
            *(["--reviewer", self.reviewer] if self.reviewer else []),
            shows=f"{' '.join(create[:8])} --reviewer {self.reviewer}\ntitle: {message}\n{body}",
        )
        return url

    def close_older_bumps(self, bumps: list[dict[str, Any]], newest: str, superseded_by: str) -> None:
        for bump in bumps:
            version = bump["headRefName"].removeprefix(BRANCH_PREFIX)
            if bump["state"] == "OPEN" and kotlin_lsp_build(version) < kotlin_lsp_build(newest):
                print(f"Closing the pull request for kotlin-lsp {version}: {superseded_by}")
                comment = f"Superseded by {superseded_by}"
                self.write(
                    "pr", "close", str(bump["number"]), "--repo", self.name, "--delete-branch", "--comment", comment
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="CodeBoarding/CodeBoarding")
    parser.add_argument("--base", default="main", help="the branch the pin is read from and the bump targets")
    parser.add_argument("--reviewer", default=REVIEWER)
    parser.add_argument("--dry-run", action="store_true", help="read everything, change nothing")
    args = parser.parse_args()
    repository = Repository(args.repo, args.base, args.reviewer, args.dry_run)

    pin_file = Path(__file__).resolve().parents[2] / PIN_PATH
    current = kotlin_lsp_source(json.loads(pin_file.read_text("utf-8"))).tag
    builds, failures = upstream_builds()
    print(f"Pinned {current}; upstream offers {sorted(builds)}")

    newest = current
    superseded_by = f"kotlin-lsp {current}"
    bumps = repository.bumps()
    for version in sorted(builds, key=kotlin_lsp_build, reverse=True):
        if kotlin_lsp_build(version) <= kotlin_lsp_build(current):
            break
        checksums = published_checksums(version)
        if checksums is None:
            print(f"kotlin-lsp {version} is not fully up at JetBrains yet")
            continue
        newest = version
        superseded_by = f"kotlin-lsp {version}"
        if any(bump["headRefName"] == branch_of(version) for bump in bumps):
            print(f"kotlin-lsp {version} already has a pull request")
        else:
            pin = {"version": version, "sha256": checksums}
            kotlin_lsp_source(pin)
            superseded_by = repository.open_bump(current, pin, builds[version])
        break

    repository.close_older_bumps(bumps, newest, superseded_by)
    if failures:
        sys.exit("\n".join(failures))


if __name__ == "__main__":
    main()
