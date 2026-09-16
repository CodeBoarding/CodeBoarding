"""The files the wiring pass may read, found in one walk of the repository.

The pass reads manifests, deployment topology and configuration, never source, so the walk is an
allowlist rather than a filter: a file the allowlist does not name is never classified, and only a
reader that asks for a path by name (an Aspire AppHost's own `.cs` files) opens anything else.

Two ignore files, two jobs. `.gitignore` decides what is in the tree at all, so a developer's
untracked `.env` cannot make a local run disagree with the same commit in CI. `.codeboardingignore`
decides what the user wants analysed, so its directory exclusions hold here too; its file patterns
(`*.config.*`, `*.test.*`) do not, because they would hide the very files this pass exists to read.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.wiring_results import Diagnostic, DiagnosticCode

logger = logging.getLogger(__name__)

#: A manifest bigger than this is machine-generated or vendored, and reading it is not worth the cost.
MAX_BYTES = 2_000_000

#: Directories the pass never walks: dependency installs, build output, tooling caches.
SKIP_DIRS = frozenset(
    {
        "node_modules",
        "bower_components",
        "vendor",
        "third_party",
        "site-packages",
        "venv",
        "virtualenv",
        "__pycache__",
        "bin",
        "obj",
        "build",
        "dist",
        "out",
        "target",
        "coverage",
        "htmlcov",
    }
)

#: The one hidden directory the pass reads: a workflow says which directory it builds an image from.
WORKFLOWS_DIR = ".github/workflows"

#: A directory whose name says its contents are tests: `src/test` (Maven), `__tests__` (jest).
TEST_DIR = re.compile(r"(?i)^(?:tests?|__tests__|specs?|e2e|testdata|fixtures?|mocks?|__mocks__|testing)$")

#: A directory named for what it tests: `ui-tests`, `Basket.FunctionalTests`. A separator or a
#: capital is what keeps `latest` out of it, and the plural is what keeps a real unit whose name
#: ends in a singular `-test` (`plugin-chart-paired-t-test`) out of it: losing a unit costs a box,
#: while keeping one costs an entry nothing joins.
TEST_DIR_SUFFIX = re.compile(r"[._-]tests$|[A-Za-z0-9]*Tests?$")

#: A file named as a test, which anchors nothing (`docs/design/wiring.md` §6).
TEST_FILE = re.compile(
    r"(?i)^test_|^tests?\.(?:py|rb|js|ts|tsx|go|java|cs)$"
    r"|[._-](?:tests?|specs?)\.[^.]+$|[._-][A-Za-z0-9]*Tests?\.(?:csproj|fsproj|java|kt|cs)$"
)

#: A directory holding one of these is a template to instantiate, not a system to read.
TEMPLATE_MARKERS = frozenset({".template.config", "cookiecutter.json", "copier.yml", "copier.yaml"})


class FileKind(StrEnum):
    """What a file the pass keeps is, by its name. Kubernetes is decided by content, not by name."""

    COMPOSE = "compose"
    SKAFFOLD = "skaffold"
    HELM_CHART = "helm_chart"
    HELM_VALUES = "helm_values"
    YAML = "yaml"
    DOCKERFILE = "dockerfile"
    WORKFLOW = "workflow"
    DOTNET_PROJECT = "dotnet_project"
    DOTNET_SOLUTION = "dotnet_solution"
    MAVEN = "maven"
    GRADLE = "gradle"
    NPM = "npm"
    PNPM_WORKSPACE = "pnpm_workspace"
    LERNA = "lerna"
    PYTHON_PROJECT = "python_project"
    REQUIREMENTS = "requirements"
    GO_MODULE = "go_module"
    CARGO = "cargo"
    COMPOSER = "composer"
    GEMFILE = "gemfile"
    GEMSPEC = "gemspec"
    MIX = "mix"
    SPRING_CONFIG = "spring_config"
    DOTENV = "dotenv"


_BY_NAME: dict[str, FileKind] = {
    "pom.xml": FileKind.MAVEN,
    "build.gradle": FileKind.GRADLE,
    "build.gradle.kts": FileKind.GRADLE,
    "settings.gradle": FileKind.GRADLE,
    "settings.gradle.kts": FileKind.GRADLE,
    "package.json": FileKind.NPM,
    "pnpm-workspace.yaml": FileKind.PNPM_WORKSPACE,
    "pnpm-workspace.yml": FileKind.PNPM_WORKSPACE,
    "lerna.json": FileKind.LERNA,
    "pyproject.toml": FileKind.PYTHON_PROJECT,
    "setup.py": FileKind.PYTHON_PROJECT,
    "go.mod": FileKind.GO_MODULE,
    "cargo.toml": FileKind.CARGO,
    "composer.json": FileKind.COMPOSER,
    "gemfile": FileKind.GEMFILE,
    "mix.exs": FileKind.MIX,
    "chart.yaml": FileKind.HELM_CHART,
    "chart.yml": FileKind.HELM_CHART,
}

_BY_PATTERN: tuple[tuple[re.Pattern[str], FileKind], ...] = (
    (re.compile(r"(?i)^(?:docker-)?compose[\w.-]*\.ya?ml$"), FileKind.COMPOSE),
    (re.compile(r"(?i)^skaffold[\w.-]*\.ya?ml$"), FileKind.SKAFFOLD),
    (re.compile(r"(?i)^values[\w.-]*\.ya?ml$"), FileKind.HELM_VALUES),
    (re.compile(r"(?i)^(?:dockerfile|containerfile)(?:[.-][\w.-]+)?$"), FileKind.DOCKERFILE),
    (re.compile(r"(?i)^[\w.-]+\.(?:dockerfile|containerfile)$"), FileKind.DOCKERFILE),
    (re.compile(r"(?i)^(?:application|bootstrap)[\w-]*\.(?:ya?ml|properties)$"), FileKind.SPRING_CONFIG),
    (re.compile(r"(?i)^requirements[\w.-]*\.txt$"), FileKind.REQUIREMENTS),
    (re.compile(r"(?i)^[\w.-]+\.gemspec$"), FileKind.GEMSPEC),
    (re.compile(r"(?i)^[\w.-]+\.(?:cs|fs)proj$"), FileKind.DOTNET_PROJECT),
    (re.compile(r"(?i)^[\w.-]+\.slnx?$"), FileKind.DOTNET_SOLUTION),
    (re.compile(r"^\.env(?:\.[\w.-]+)?$"), FileKind.DOTENV),
    (re.compile(r"(?i)^[\w.-]*\.ya?ml$"), FileKind.YAML),
)


def repo_path(base: str, target: str) -> str:
    """*target* read relative to directory *base*, spelled from the repository root.

    Empty when it leaves the tree or is not a path here, which is how a build context of `../..`
    outside a checkout and a `https://` context both stop being directories (§6 rule 8).
    """
    if not target or "://" in target or target.startswith("/"):
        return ""
    joined = os.path.normpath(os.path.join(base, target)).replace(os.sep, "/")
    if joined == ".." or joined.startswith("../"):
        return ""
    return "" if joined == "." else joined


def parent_dir(path: str) -> str:
    return os.path.dirname(path)


def parse_dotenv(text: str) -> dict[str, str]:
    """A `.env` file as compose reads it: `KEY=value`, `export` allowed, quotes stripped."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


@dataclass(frozen=True)
class ScannedFile:
    path: str
    kind: FileKind
    size: int


class Scan:
    """One walk of the repository: what the pass may read, and the text of what it asks for."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.files: dict[str, ScannedFile] = {}
        self.by_dir: dict[str, list[str]] = {}
        self.diagnostics: list[Diagnostic] = []
        self._ignore = RepoIgnoreManager(repo_root)
        self._ignored_dirs: dict[str, str] = {}
        self._ignored_scopes: dict[tuple[str, str], int] = {}
        self._text: dict[str, str] = {}
        self._walk()

    def paths_of(self, *kinds: FileKind) -> tuple[str, ...]:
        """Every scanned path of these kinds, sorted, so a reader's output order is the tree's."""
        wanted = set(kinds)
        return tuple(sorted(path for path, file in self.files.items() if file.kind in wanted))

    def names_in(self, directory: str) -> tuple[str, ...]:
        """The file names directly inside *directory*, whether the allowlist names them or not."""
        return tuple(self.by_dir.get(directory, ()))

    def has_file(self, path: str) -> bool:
        return os.path.basename(path) in self.by_dir.get(parent_dir(path), ())

    def has_dir(self, directory: str) -> bool:
        return directory in self.by_dir

    def text(self, path: str) -> str:
        """The file's text, or an empty string when it is missing, unreadable or over the cap."""
        if path in self._text:
            return self._text[path]
        self._text[path] = self._read(path)
        return self._text[path]

    def documents(self, path: str) -> list[dict]:
        """Every YAML mapping in the file; empty when it does not parse.

        A Go template — a Helm chart's `templates/` — is not YAML and is not reported as broken:
        its braces already say why. A file that merely mentions `{{defaultContext}}`, as a workflow
        does, is ordinary YAML and parses like any other.
        """
        text = self.text(path)
        if not text:
            return []
        try:
            loaded = list(yaml.load_all(text, Loader=_Loader))
        except (yaml.YAMLError, RecursionError) as error:
            if "{{" not in text:
                self.diagnose(DiagnosticCode.UNREADABLE_MANIFEST, f"{path} is not YAML: {type(error).__name__}", path)
            return []
        return [document for document in loaded if isinstance(document, dict)]

    def json_object(self, path: str) -> dict:
        """The file's top-level JSON object; empty when it does not parse."""
        text = self.text(path)
        if not text:
            return {}
        try:
            loaded = json.loads(text)
        except (json.JSONDecodeError, RecursionError) as error:
            self.diagnose(DiagnosticCode.UNREADABLE_MANIFEST, f"{path} is not JSON: {type(error).__name__}", path)
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def line_of(self, path: str, token: str, start: int = 0) -> int:
        """The one-based line where *token* first occurs at or after offset *start*; 1 when absent."""
        text = self.text(path)
        index = text.find(token, start)
        return text.count("\n", 0, index) + 1 if index >= 0 else 1

    def diagnose(self, code: DiagnosticCode, message: str, *paths: str) -> None:
        self.diagnostics.append(Diagnostic(code=code, message=message, paths=tuple(paths)))

    def _read(self, path: str) -> str:
        target = self.repo_root / path
        try:
            size = target.stat().st_size
        except OSError:
            return ""
        if size > MAX_BYTES:
            self.diagnose(DiagnosticCode.IGNORED_MANIFEST, f"{path} is larger than {MAX_BYTES // 1_000_000} MB", path)
            return ""
        try:
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            self.diagnose(DiagnosticCode.UNREADABLE_MANIFEST, f"{path} could not be read: {error.strerror}", path)
            return ""

    def _walk(self) -> None:
        for current, directories, names in os.walk(self.repo_root):
            relative = self._relative(current)
            if TEMPLATE_MARKERS & set(directories) or TEMPLATE_MARKERS & set(names):
                self.diagnose(
                    DiagnosticCode.IGNORED_MANIFEST,
                    f"{relative or '.'} is a project template, not a system",
                    relative,
                )
                directories[:] = []
                continue
            directories[:] = sorted(name for name in directories if _walkable(relative, name))
            self.by_dir[relative] = sorted(names)
            for name in sorted(names):
                self._keep(relative, name)
        for (scope, reason), count in sorted(self._ignored_scopes.items()):
            self.diagnose(
                DiagnosticCode.IGNORED_MANIFEST,
                f"{scope} is excluded by {reason} ({count} {'file' if count == 1 else 'files'})",
                scope,
            )

    def _keep(self, directory: str, name: str) -> None:
        kind = classify(directory, name)
        if kind is None:
            return
        path = f"{directory}/{name}" if directory else name
        if TEST_FILE.search(name):
            self.diagnose(DiagnosticCode.IGNORED_MANIFEST, f"{path} is named as a test", path)
            return
        scope, reason = self._ignored(path)
        if reason:
            self._ignored_scopes[(scope, reason)] = self._ignored_scopes.get((scope, reason), 0) + 1
            return
        try:
            size = (self.repo_root / path).stat().st_size
        except OSError:
            return
        self.files[path] = ScannedFile(path=path, kind=kind, size=size)

    def _ignored(self, path: str) -> tuple[str, str]:
        """What hides *path* from the pass: the place the user excluded, and which file said so."""
        if self._ignore.gitignore_spec.match_file(path):
            return path, ".gitignore"
        excluded = self._excluded_root(parent_dir(path))
        return (excluded, ".codeboardingignore") if excluded else ("", "")

    def _excluded_root(self, directory: str) -> str:
        """The highest directory above a file that `.codeboardingignore` excludes; one decision, one row."""
        if not directory:
            return ""
        if directory not in self._ignored_dirs:
            above = self._excluded_root(parent_dir(directory))
            matched = self._ignore.codeboardingignore_spec.match_file(directory + "/")
            self._ignored_dirs[directory] = above or (directory if matched else "")
        return self._ignored_dirs[directory]

    def _relative(self, absolute: str) -> str:
        relative = os.path.relpath(absolute, self.repo_root).replace(os.sep, "/")
        return "" if relative == "." else relative


def classify(directory: str, name: str) -> FileKind | None:
    """What the pass would read this file as, or None when it is not on the allowlist."""
    if directory == WORKFLOWS_DIR:
        return FileKind.WORKFLOW if re.fullmatch(r"(?i)[\w.-]+\.ya?ml", name) else None
    if directory.startswith(".github"):
        return None
    lowered = name.lower()
    if lowered in _BY_NAME:
        return _BY_NAME[lowered]
    for pattern, kind in _BY_PATTERN:
        if pattern.fullmatch(name):
            return kind
    return None


def _walkable(directory: str, name: str) -> bool:
    if name in SKIP_DIRS or TEST_DIR.fullmatch(name) or TEST_DIR_SUFFIX.search(name) or "{{" in name:
        return False
    if name == "lib" and directory.endswith("wwwroot"):
        return False
    if name.startswith("."):
        return (not directory and name == ".github") or (directory == ".github" and name == "workflows")
    return True


class _Loader(yaml.SafeLoader):
    """Safe YAML that keeps going past tags it does not know, such as compose's `!reset`."""


def _unknown_tag(loader: yaml.Loader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None


_Loader.add_multi_constructor("", _unknown_tag)
