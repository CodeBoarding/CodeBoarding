"""The compose project, read the way the compose specification says to read it.

Files in one directory are one project: a base file and the files beside it merge by service name,
so a service whose image is written in one file and whose build context is written in another is
one service (§6 rule 6). `${VAR}` and `${VAR:-default}` interpolate from the `.env` beside them and
from nothing else — never this machine's environment, which would make a local run disagree with
the same commit in CI. `extends` is followed, `include` brings another file's services in, and YAML
anchors and merge keys are ordinary YAML. A service with `profiles` is a variant: recorded here,
and not drawn.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from typing import Any

from static_analyzer.wiring.scan import FileKind, Scan, listing, mapping, parent_dir, parse_dotenv, repo_path
from static_analyzer.wiring_results import DiagnosticCode

#: `$VAR`, `${VAR}`, and the specification's six forms of `${VAR<op>word}`; `$$` is a literal `$`.
_VARIABLE = re.compile(r"\$(\$)|\$\{([A-Za-z_]\w*)(?:(:?[-?+])([^{}]*))?\}|\$([A-Za-z_]\w*)")

#: The files compose itself would read first, in its own order; the rest follow, overrides last.
_BASE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


@dataclass(frozen=True)
class EnvEntry:
    """One environment variable a service sets, and where it is written."""

    key: str
    value: str
    file: str
    line: int


@dataclass(frozen=True)
class ComposeService:
    """One merged service: what it runs, what it builds, and the names it answers to."""

    name: str
    project: str
    files: tuple[str, ...] = ()
    image: str = ""
    context: str = ""
    dockerfile: str = ""
    container_name: str = ""
    hostname: str = ""
    network_aliases: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()
    ports: tuple[str, ...] = ()
    environment: tuple[EnvEntry, ...] = ()
    depends_on: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    line: int = 1

    @property
    def names(self) -> tuple[str, ...]:
        """Every name this service answers to on its network, the service name first."""
        found = (self.name, self.container_name, self.hostname, *self.network_aliases)
        return tuple(dict.fromkeys(name for name in found if name))


@dataclass(frozen=True)
class ComposeProject:
    directory: str
    files: tuple[str, ...]
    services: tuple[ComposeService, ...]


def compose_projects(scan: Scan) -> list[ComposeProject]:
    """One project per directory holding compose files, its services merged as compose merges them."""
    by_directory: dict[str, list[str]] = {}
    for path in scan.paths_of(FileKind.COMPOSE):
        by_directory.setdefault(parent_dir(path), []).append(path)

    projects = []
    for directory, paths in sorted(by_directory.items()):
        environment = _environment(scan, directory)
        merged: dict[str, ComposeService] = {}
        read: list[str] = []
        for path in sorted(paths, key=_merge_order):
            _read_file(scan, path, environment, merged, read)
        services = tuple(merged[name] for name in sorted(merged))
        if services:
            projects.append(ComposeProject(directory=directory, files=tuple(read), services=services))
    return projects


def interpolate(value: str, environment: dict[str, str]) -> str:
    """A compose value with its variables substituted, each form as the specification reads it.

    `${VAR:-word}` and `${VAR-word}` fall back to the word; `${VAR:+word}` and `${VAR+word}` are
    the word only when the variable is set; `${VAR:?err}` and `${VAR?err}` would stop compose, so
    the text stays as written. An unset variable with no word stays as written too.
    """

    def replace_one(match: re.Match[str]) -> str:
        if match.group(1):
            return "$"
        name = match.group(2) or match.group(5) or ""
        operator, word = match.group(3) or "", match.group(4) or ""
        set_enough = name in environment and (environment[name] != "" or not operator.startswith(":"))
        if operator.endswith("+"):
            return word if set_enough else ""
        if set_enough:
            return environment[name]
        if operator.endswith("-"):
            return word
        return match.group(0)

    # One pass, as compose itself substitutes: a second would read `$$TAG`, an escaped literal,
    # as the variable it deliberately is not.
    return _VARIABLE.sub(replace_one, value)


def _environment(scan: Scan, directory: str) -> dict[str, str]:
    """The `.env` beside the compose files, which is the only thing interpolation may read."""
    path = f"{directory}/.env" if directory else ".env"
    return parse_dotenv(scan.text(path)) if scan.has_file(path) else {}


def _merge_order(path: str) -> tuple[int, int, str]:
    name = os.path.basename(path).lower()
    if name in _BASE_NAMES:
        return 0, _BASE_NAMES.index(name), name
    return (2 if ".override." in name else 1), 0, name


def _read_file(
    scan: Scan,
    path: str,
    environment: dict[str, str],
    merged: dict[str, ComposeService],
    read: list[str],
) -> None:
    for document in scan.documents(path):
        read.append(path)
        for included in _includes(document):
            target = repo_path(parent_dir(path), included)
            if target and scan.has_file(target) and target not in read:
                _read_file(scan, target, _environment(scan, parent_dir(target)), merged, read)
        for name, raw in sorted(mapping(document.get("services")).items(), key=lambda item: str(item[0])):
            if not isinstance(name, (str, int)) or isinstance(name, bool):
                scan.diagnose(DiagnosticCode.UNREADABLE_MANIFEST, f"{path} declares a service with no name", path)
                continue
            if not isinstance(raw, dict):
                continue
            resolved = _service(scan, str(name), path, _extended(scan, path, raw, environment, set()), environment)
            merged[str(name)] = _merge(merged.get(str(name)), resolved)


def _includes(document: dict) -> list[str]:
    paths: list[str] = []
    for entry in listing(document.get("include")):
        if isinstance(entry, str):
            paths.append(entry)
        elif isinstance(entry, dict):
            paths += [target for target in listing(entry.get("path")) if isinstance(target, str)]
    return paths


def _extended(scan: Scan, path: str, raw: dict, environment: dict[str, str], seen: set[tuple[str, str]]) -> dict:
    """The service with what it `extends` merged under it, following the chain across files."""
    extends = raw.get("extends")
    if isinstance(extends, str):
        extends = {"service": extends}
    if not isinstance(extends, dict) or not isinstance(extends.get("service"), str):
        return raw
    file = extends.get("file")
    base_path = repo_path(parent_dir(path), file) if isinstance(file, str) else path
    key = (base_path, str(extends["service"]))
    if not base_path or key in seen:
        return raw
    seen.add(key)
    for document in scan.documents(base_path):
        base = mapping(document.get("services")).get(extends["service"])
        if isinstance(base, dict):
            resolved_base = _extended(scan, base_path, base, environment, seen)
            return {**resolved_base, **raw}
    return raw


def _service(scan: Scan, name: str, path: str, raw: dict, environment: dict[str, str]) -> ComposeService:
    directory = parent_dir(path)
    build = raw.get("build")
    context, dockerfile = "", ""
    if isinstance(build, (str, dict)):
        where = build if isinstance(build, str) else build.get("context", ".")
        context = repo_path(directory, _text(where, environment))
        named = _text(build.get("dockerfile", ""), environment) if isinstance(build, dict) else ""
        beside = f"{context}/Dockerfile" if context else "Dockerfile"
        dockerfile = repo_path(context, named) if named else (beside if scan.has_file(beside) else "")
    profiles = tuple(
        _text(profile, environment) for profile in listing(raw.get("profiles")) if isinstance(profile, str)
    )
    return ComposeService(
        environment=_env_entries(scan, raw, path, name, environment),
        ports=_ports(raw, environment),
        depends_on=_referenced(raw.get("depends_on")),
        links=tuple(link.split(":")[0] for link in _referenced(raw.get("links"))),
        line=scan.key_lines(path).get(f"services.{name}", 1),
        name=name,
        project=directory,
        files=(path,),
        image=_text(raw.get("image", ""), environment),
        context=context,
        dockerfile=dockerfile,
        container_name=_text(raw.get("container_name", ""), environment),
        hostname=_text(raw.get("hostname", ""), environment),
        network_aliases=_network_aliases(raw, environment),
        profiles=profiles,
    )


def _env_entries(scan: Scan, raw: dict, path: str, name: str, values: dict[str, str]) -> tuple[EnvEntry, ...]:
    """A service's `environment` in either form, each key with the line it was written on."""
    declared = raw.get("environment")
    lines = scan.key_lines(path)
    pairs: list[tuple[str, str, int]] = []
    if isinstance(declared, dict):
        for key, value in declared.items():
            line = lines.get(f"services.{name}.environment.{key}", 1)
            pairs.append((str(key), _scalar(value, values), line))
    else:
        for index, item in enumerate(listing(declared)):
            if isinstance(item, str):
                key, separator, value = item.partition("=")
                line = lines.get(f"services.{name}.environment.{index}", 1)
                pairs.append((key.strip(), _text(value, values) if separator else "", line))
    return tuple(EnvEntry(key=key, value=value, file=path, line=line) for key, value, line in pairs if key)


def _ports(raw: dict, values: dict[str, str]) -> tuple[str, ...]:
    """The ports a service publishes, in either notation: `"8080:8080"` or `{published, target}`."""
    published = []
    for entry in listing(raw.get("ports")):
        if isinstance(entry, str):
            published.append(_text(entry, values))
        elif isinstance(entry, dict) and entry.get("published"):
            published.append(f"{entry['published']}:{entry.get('target', entry['published'])}")
        elif isinstance(entry, int):
            published.append(str(entry))
    return tuple(port for port in published if port)


def _referenced(declared: object) -> tuple[str, ...]:
    """The service names a `depends_on` or `links` entry names, in either form."""
    if isinstance(declared, dict):
        return tuple(str(name) for name in declared)
    return tuple(str(name) for name in listing(declared) if isinstance(name, (str, int)))


def _scalar(value: object, values: dict[str, str]) -> str:
    if isinstance(value, str):
        return _text(value, values)
    return "" if value is None or isinstance(value, bool) else str(value)


def _network_aliases(raw: dict, environment: dict[str, str]) -> tuple[str, ...]:
    aliases = []
    for settings in mapping(raw.get("networks")).values():
        for alias in listing(mapping(settings).get("aliases")):
            if isinstance(alias, str):
                aliases.append(_text(alias, environment))
    return tuple(dict.fromkeys(aliases))


def _merge(before: ComposeService | None, after: ComposeService) -> ComposeService:
    """Compose's own merge: a later file's scalars win, and what a service answers to accumulates."""
    if before is None:
        return after
    merged: dict[str, EnvEntry] = {entry.key: entry for entry in before.environment}
    merged.update({entry.key: entry for entry in after.environment})
    return replace(
        after,
        files=before.files + after.files,
        environment=tuple(merged.values()),
        depends_on=tuple(dict.fromkeys(before.depends_on + after.depends_on)),
        links=tuple(dict.fromkeys(before.links + after.links)),
        line=before.line if before.files else after.line,
        image=after.image or before.image,
        context=after.context or before.context,
        dockerfile=after.dockerfile or before.dockerfile,
        container_name=after.container_name or before.container_name,
        hostname=after.hostname or before.hostname,
        network_aliases=tuple(dict.fromkeys(before.network_aliases + after.network_aliases)),
        ports=tuple(dict.fromkeys(before.ports + after.ports)),
        profiles=tuple(dict.fromkeys(before.profiles + after.profiles)),
    )


def _text(value: Any, environment: dict[str, str]) -> str:
    return interpolate(value, environment).strip() if isinstance(value, str) else ""
