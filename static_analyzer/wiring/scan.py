"""The files the wiring pass may read, found in one walk of the repository.

The pass reads manifests, deployment topology and configuration, never source, so the walk is an
allowlist rather than a filter: a file the allowlist does not name is never classified, and only a
reader that asks for a path by name (an Aspire AppHost's own `.cs` files) opens anything else.

Two ignore files, two jobs. `.gitignore` decides what is in the tree at all, so a developer's
untracked `.env` cannot make a local run disagree with the same commit in CI. `.codeboardingignore`
decides what the user wants analysed, so its directory exclusions hold here too; its file patterns
(`*.config.*`, `*.test.*`) do not, because they would hide the very files this pass exists to read.
Every place the walk leaves out is one diagnostic row, so nothing is dropped silently.
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

#: A configuration file states a handful of facts. Past this, a YAML is a catalogue — a provider
#: list, an API specification, a lockfile — and reading it costs far more than it ever declares.
MAX_CONFIGURATION_BYTES = 128_000

#: A file whose name says a tool wrote it: nothing in it is a decision anyone made.
GENERATED_FILE = re.compile(
    r"(?i)\.(?:designer|g|generated)\.[a-z]+$|modelsnapshot\.cs$|_pb2\.py$|\.pb\.go$|lock\.ya?ml$"
)

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

#: A directory named for what it tests: `ui-tests`, `Basket.FunctionalTests`, `Acme.Tests`. Plural
#: only, in both spellings: a singular `-test` or `Test` is a name (`plugin-chart-paired-t-test`,
#: `ABTest`), and losing a unit costs a box while keeping one costs an entry nothing joins.
TEST_DIR_SUFFIX = re.compile(r"[._-]tests$|[A-Za-z0-9._-]Tests$")

#: A file named as a test, which anchors nothing (`docs/design/wiring.md` §6).
TEST_FILE = re.compile(
    r"(?i)^test_|^tests?\.(?:py|rb|js|ts|tsx|go|java|cs)$"
    r"|[._-](?:tests?|specs?)\.[^.]+$|[._-][A-Za-z0-9]*Tests?\.(?:csproj|fsproj|java|kt|cs)$"
)

#: What a reader may open for a literal service name or an environment read (`docs/design/wiring.md` §3).
#: Source is never scanned for structure — that is the language servers' work — only for these two.
SOURCE_SUFFIXES = frozenset(
    {".cs", ".java", ".kt", ".scala", ".groovy", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
     ".go", ".rb", ".php", ".rs", ".vue", ".svelte"}
)  # fmt: skip

#: A directory holding one of these is a template to instantiate, not a system to read.
TEMPLATE_MARKERS = frozenset({".template.config", "cookiecutter.json", "copier.yml", "copier.yaml"})

#: A `.env` written to be copied is documentation of what a deployment may set, and sets nothing.
DOCUMENTATION_DOTENV = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist"})


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
    DOTNET_SETTINGS = "dotnet_settings"
    PROPERTIES = "properties"
    NGINX = "nginx"
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

#: Where nginx keeps configuration that has no extension: `conf.d`, `sites-enabled`, an include dir.
_NGINX_DIR = re.compile(r"(?i)(?:^|/)(?:nginx[\w.-]*|conf\.d|sites-(?:enabled|available))(?:/|$)")

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
    (re.compile(r"(?i)^appsettings[\w.-]*\.json$"), FileKind.DOTNET_SETTINGS),
    (re.compile(r"(?i)^[\w.-]*\.properties$"), FileKind.PROPERTIES),
    (re.compile(r"(?i)^nginx[\w.-]*\.conf$|^[\w.-]+\.conf$"), FileKind.NGINX),
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


def mapping(node: object) -> dict:
    """*node* when it is a mapping, else an empty one, so a `.get` chain over hand-written YAML never raises.

    Why: a scalar where a manifest expects a mapping (`spec: {template: metadata}` written as text,
    a `services:` that is a list) is a mistake in the file, and the pass reports files rather than
    crashing on them.
    """
    return node if isinstance(node, dict) else {}


def listing(node: object) -> list:
    """*node* as the list a manifest meant: a list as is, a scalar as its one entry, nothing otherwise."""
    if isinstance(node, list):
        return node
    return [node] if isinstance(node, (str, int, float)) and not isinstance(node, bool) else []


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


def classify(directory: str, name: str) -> FileKind | None:
    """What the pass would read this file as, or None when it is not on the allowlist."""
    if directory == WORKFLOWS_DIR:
        return FileKind.WORKFLOW if re.fullmatch(r"(?i)[\w.-]+\.ya?ml", name) else None
    if directory.startswith(".github"):
        return None
    lowered = name.lower()
    if lowered in DOCUMENTATION_DOTENV:
        return None
    if "." not in name and _NGINX_DIR.search(directory):
        return FileKind.NGINX
    if lowered in _BY_NAME:
        return _BY_NAME[lowered]
    for pattern, kind in _BY_PATTERN:
        if pattern.fullmatch(name):
            return kind
    return None


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
        self._text: dict[str, str] = {}
        self._documents: dict[str, list[dict]] = {}
        self._key_lines: dict[str, dict[str, int]] = {}
        self._walk()

    def paths_of(self, *kinds: FileKind) -> tuple[str, ...]:
        """Every scanned path of these kinds, sorted, so a reader's output order is the tree's."""
        wanted = set(kinds)
        return tuple(sorted(path for path, file in self.files.items() if file.kind in wanted))

    def sources(self, inside: str = "") -> tuple[str, ...]:
        """The source files a reader may look inside: never a test, never an excluded place."""
        prefix = f"{inside}/" if inside else ""
        found = []
        for directory, names in sorted(self.by_dir.items()):
            if inside and directory != inside and not directory.startswith(prefix):
                continue
            for name in names:
                path = f"{directory}/{name}" if directory else name
                tooling = ".config." in name or name.startswith(".") or GENERATED_FILE.search(name)
                if os.path.splitext(name)[1] in SOURCE_SUFFIXES and not TEST_FILE.search(name) and not tooling:
                    if not self._ignore.gitignore_spec.match_file(path):
                        found.append(path)
        return tuple(found)

    def names_in(self, directory: str) -> tuple[str, ...]:
        """The file names directly inside *directory*, whether the allowlist names them or not."""
        return tuple(self.by_dir.get(directory, ()))

    def paths_below(self, directory: str) -> tuple[str, ...]:
        """Every file inside *directory* and the directories under it that the walk reached."""
        prefix = f"{directory}/" if directory else ""
        return tuple(
            f"{where}/{name}" if where else name
            for where, names in sorted(self.by_dir.items())
            if where == directory or where.startswith(prefix)
            for name in names
        )

    def has_file(self, path: str) -> bool:
        return os.path.basename(path) in self.by_dir.get(parent_dir(path), ())

    def has_dir(self, directory: str) -> bool:
        return directory in self.by_dir

    def on_disk(self, path: str) -> bool:
        """Whether a file a manifest names by path exists, even under a directory the walk left out.

        Why: the Go standard layout keeps its Dockerfiles under `build/`, and a build that names one
        explicitly means that file and not whatever the walk skipped past.
        """
        return self.has_file(path) or (bool(path) and (self.repo_root / path).is_file())

    def text(self, path: str) -> str:
        """The file's text, or an empty string when it is missing, unreadable or over the cap."""
        if path in self._text:
            return self._text[path]
        self._text[path] = self._read(path)
        return self._text[path]

    def documents(self, path: str) -> list[dict]:
        """Every YAML mapping in the file; empty when it does not parse. Parsed once per file.

        A Go template — a Helm chart's `templates/` — is not YAML and is not reported as broken:
        its braces already say why. A file that merely mentions `{{defaultContext}}`, as a workflow
        does, is ordinary YAML and parses like any other.
        """
        if path in self._documents:
            return self._documents[path]
        text = self.text(path)
        loaded: list = []
        if text:
            try:
                loaded = list(yaml.load_all(text, Loader=_Loader))
            except (yaml.YAMLError, RecursionError) as error:
                if "{{" not in text:
                    self.diagnose(
                        DiagnosticCode.UNREADABLE_MANIFEST, f"{path} is not YAML: {type(error).__name__}", path
                    )
        self._documents[path] = [document for document in loaded if isinstance(document, dict)]
        return self._documents[path]

    def key_lines(self, path: str) -> dict[str, int]:
        """The one-based line of every mapping key in the file, by its dotted path from the document root.

        A list item is addressed by its index (`services.api.ports.0`). The first document that
        writes a path wins, and a file that does not parse has no lines.
        """
        if path in self._key_lines:
            return self._key_lines[path]
        lines: dict[str, int] = {}
        text = self.text(path)
        if text:
            try:
                for document in yaml.compose_all(text, Loader=_Loader):
                    _collect_lines(document, "", lines)
            except (yaml.YAMLError, RecursionError):
                pass
        self._key_lines[path] = lines
        return lines

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

    def line_where(self, path: str, key: str, value: str) -> int:
        """The one-based line a setting is written on: where *key* is followed by *value*.

        Why both halves: a key repeated across documents (`import:` in two of them) and a value
        repeated across settings (`0.7` under two of them) each name the wrong line on their own,
        and a setting is the one place where its own key and its own value meet.
        """
        text = self.text(path)
        start = 0
        while key and (index := text.find(key, start)) >= 0:
            end = text.find("\n", index)
            if _holds(text[index : end if end >= 0 else len(text)], value):
                return text.count("\n", 0, index) + 1
            start = index + 1
        # A value written with escapes (`\\` in JSON) never equals its parsed self: the key's line, then.
        index = text.find(value) if value else -1
        if index >= 0:
            return text.count("\n", 0, index) + 1
        return self.line_of(path, key)

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
            return target.read_text(encoding="utf-8-sig", errors="replace")
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
            kept = []
            for name in sorted(directories):
                reason = self._pruned(relative, name)
                if reason is None:
                    kept.append(name)
                elif reason:
                    place = f"{relative}/{name}" if relative else name
                    self.diagnose(DiagnosticCode.IGNORED_MANIFEST, f"{place} is {reason}", place)
            directories[:] = kept
            self.by_dir[relative] = sorted(names)
            for name in sorted(names):
                self._keep(relative, name)

    def _pruned(self, directory: str, name: str) -> str | None:
        """Why the walk leaves a directory out, empty for the repository's own metadata, None to walk it."""
        path = f"{directory}/{name}" if directory else name
        if name in SKIP_DIRS:
            return "a dependency or build directory"
        if TEST_DIR.fullmatch(name) or TEST_DIR_SUFFIX.search(name):
            return "a test directory"
        if "{{" in name:
            return "a template path"
        if name == "lib" and directory.endswith("wwwroot"):
            return "a vendored client library directory"
        if name.startswith("."):
            if (not directory and name == ".github") or (directory == ".github" and name == "workflows"):
                return None
            return "" if path == ".git" else "a hidden directory"
        if directory == ".github" and name != "workflows":
            # Only the workflows say what a repository builds; a script beside them is the forge's tooling.
            return "inside a hidden directory"
        if self._ignore.gitignore_spec.match_file(path + "/"):
            return "excluded by .gitignore"
        if self._ignore.codeboardingignore_spec.match_file(path + "/"):
            return "excluded by .codeboardingignore"
        return None

    def _keep(self, directory: str, name: str) -> None:
        kind = classify(directory, name)
        if kind is None:
            return
        path = f"{directory}/{name}" if directory else name
        if TEST_FILE.search(name):
            self.diagnose(DiagnosticCode.IGNORED_MANIFEST, f"{path} is named as a test", path)
            return
        if self._ignore.gitignore_spec.match_file(path):
            self.diagnose(DiagnosticCode.IGNORED_MANIFEST, f"{path} is excluded by .gitignore", path)
            return
        try:
            size = (self.repo_root / path).stat().st_size
        except OSError:
            return
        self.files[path] = ScannedFile(path=path, kind=kind, size=size)

    def _relative(self, absolute: str) -> str:
        relative = os.path.relpath(absolute, self.repo_root).replace(os.sep, "/")
        return "" if relative == "." else relative


def _holds(line: str, value: str) -> bool:
    """Whether the line writes this value, rather than a longer one it is a fragment of.

    Why: `30` reads as written on a line setting `300`, and a setting's own line is the one thing
    its value is supposed to identify.
    """
    if not value:
        return True
    return re.search(rf"(?<![\w.]){re.escape(value)}(?![\w.])", line) is not None


def _collect_lines(node: yaml.Node | None, prefix: str, lines: dict[str, int]) -> None:
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode):
                continue
            path = f"{prefix}.{key.value}" if prefix else str(key.value)
            lines.setdefault(path, key.start_mark.line + 1)
            _collect_lines(value, path, lines)
    elif isinstance(node, yaml.SequenceNode):
        for index, value in enumerate(node.value):
            path = f"{prefix}.{index}" if prefix else str(index)
            lines.setdefault(path, value.start_mark.line + 1)
            _collect_lines(value, path, lines)


class _Loader(yaml.SafeLoader):
    """Safe YAML that keeps going past tags it does not know, such as compose's `!reset`."""


def _unknown_tag(loader: yaml.Loader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None


_Loader.add_multi_constructor("", _unknown_tag)
