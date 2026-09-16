"""What configuration declares: a key, the host its value names, and the store it connects to.

Spring YAML and properties, `appsettings*.json`, `.env` files and the configuration YAML a unit
keeps under `config/` are all the same shape once flattened: a dotted key, a value, and a line. A
key is an anchor when something can join it — the code that reads it, the host it names, the
database it connects to — and a `${KEY:default}` in a value is a use of that key.

A configuration server (`@EnableConfigServer` with a native `search-locations` directory) ships
other units' settings from its own directory: `<name>.yml` there is about the unit called `<name>`
and `application.yml` is about every unit that fetches from the server, never about the server.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence

from static_analyzer.wiring.anchors.keys import HOST_KEY, LOCAL_HOSTS, Names, Owners, env_key, hosts_in, service_host
from static_analyzer.wiring.scan import (
    GENERATED_FILE,
    MAX_CONFIGURATION_BYTES,
    FileKind,
    Scan,
    parent_dir,
    parse_dotenv,
    repo_path,
)
from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import Anchor, AnchorFamily, AnchorRole, DiagnosticCode, Tier, Unit, is_resource

#: A key whose name says what it connects to, whatever its value turns out to be.
CONNECTION_KEY = re.compile(
    r"(?i)(?:^|[.:_-])(?:connectionstring|connectionstrings|datasource|jdbc|mongodb|redis|rabbitmq|kafka|"
    r"elasticsearch|opensearch|memcached|cassandra|minio|s3|smtp|mail|zipkin|otlp|openai|azureopenai|"
    r"ollama|vectorstore|bootstrapservers)(?:[.:_-]|$)"
)

#: `${CONFIG_SERVER_URL:http://localhost:8888/}` — a use of a key, and a default worth reading.
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][\w.-]*)(?::([^}]*))?\}")

#: A connection string is a list of `Key=Value;` pairs; these two say where and what.
_CONNECTION_PART = re.compile(
    r"(?i)\b(server|host|data source|datasource|endpoint|database|initial catalog)\s*=\s*([^;]+)"
)

#: A scheme that names a store rather than a service: `jdbc:hsqldb://`, `mongodb://`, `amqp://`.
STORE_SCHEME = re.compile(
    r"(?i)^(jdbc:[a-z0-9]+|postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|kafka|s3|"
    r"minio|elasticsearch|clickhouse|cassandra|mssql|sqlserver|oracle|memcached)://"
)

#: Where a schema lives when a repository keeps one: what is in here defines a database's content.
SCHEMA_DIRS = ("db", "database", "migrations", "migration", "schema", "sql", "initdb", "changelog")
#: A directory of migrations, whichever tool writes them: Flyway and Liquibase, EF, Django, Rails.
MIGRATION_DIRS = ("migrations", "migration", "changelog", "initdb", "migrate")
_DDL_SUFFIXES = (".sql", ".ddl")
_MIGRATION_SUFFIXES = (".sql", ".ddl", ".xml", ".yaml", ".yml", ".json", ".py", ".rb", ".cs", ".java")

#: How SQL Server spells the way to reach a host, before it names the host: `tcp:`, `np:` (a named
#: pipe), `lpc:` (shared memory). None of them is part of the name.
_NETWORK_PROTOCOL = re.compile(r"(?i)^(?:tcp|np|lpc|admin):")

#: A file whose own name says it defines the schema, rather than one that merely mentions the word:
#: `schema.sql` declares, while `0041_create_attachments.py` and `FixOrderSchema.Designer.cs` are a
#: point in a history that happens to contain it.
_DECLARES_SCHEMA = re.compile(r"(?i)^(?:schema|create|init|initial)[\w.-]*\.")

#: The configuration YAML a unit keeps, named: a settings file by its own name, or anything under a
#: `config/` directory. A CI file, a lockfile or a hidden tool file is not configuration.
_CONFIGURATION_YAML = re.compile(
    r"(?i)^(?:config|configuration|settings|database|datasource|cable|storage|secrets)[\w.-]*\.ya?ml$"
)
_CONFIGURATION_DIRS = frozenset({"config", "configs", "conf", "settings"})
_SPRING_RESOURCES = "/resources/"
_SEARCH_LOCATIONS = env_key("spring.cloud.config.server.native.search-locations")
_PROFILE_SUFFIX = re.compile(r"-[\w]+$")


def read(scan: Scan, owners: Owners, *, paths: Sequence[str] | None = None) -> list[Anchor]:
    """Every anchor a configuration file writes down, plus the schema directories units keep.

    With ``paths`` only those files are read, each for every unit it is about.
    """
    found: list[Anchor] = []
    for path in paths if paths is not None else configuration_files(scan, owners):
        for unit in owners.about(path):
            found += _file(scan, path, unit)
    if paths is None:
        found += _schemas(scan, owners)
    return found


def configuration_files(scan: Scan, owners: Owners) -> list[str]:
    """The files the pass reads as configuration, in walk order."""
    files = []
    for path in scan.paths_of(FileKind.SPRING_CONFIG, FileKind.PROPERTIES, FileKind.DOTNET_SETTINGS, FileKind.DOTENV):
        # A catalogue is a catalogue in whatever notation it is written: the cap is about how much
        # a file declares, not about YAML.
        if scan.files[path].size > MAX_CONFIGURATION_BYTES:
            scan.diagnose(
                DiagnosticCode.IGNORED_MANIFEST,
                f"{path} is larger than {MAX_CONFIGURATION_BYTES // 1000} KB and is read as data, not configuration",
                path,
            )
            continue
        files.append(path)
    files += [path for path in scan.paths_of(FileKind.YAML) if _is_configuration_yaml(scan, path, owners)]
    return sorted(files)


def entries(scan: Scan, path: str) -> list[tuple[str, str, int]]:
    """A configuration file as (key, value, line), whatever notation it is written in."""
    if path.endswith(".properties"):
        return _properties(scan.text(path))
    if scan.files[path].kind is FileKind.DOTENV:
        text = scan.text(path)
        lines = {key: index for index, key in _dotenv_lines(text)}
        return [(key, value, lines.get(key, 1)) for key, value in parse_dotenv(text).items()]
    if path.endswith(".json"):
        return _flatten(_json_with_comments(scan, path), scan, path)
    flattened: list[tuple[str, str, int]] = []
    for document in scan.documents(path):
        flattened += _flatten(document, scan, path)
    return flattened


def shared_directories(scan: Scan, units: Sequence[Unit]) -> dict[str, tuple[str, ...]]:
    """Each configuration server's `search-locations` directories, by the unit that serves them."""
    by_unit = {unit.id: unit for unit in units}
    owners = Owners(units)
    found: dict[str, tuple[str, ...]] = {}
    for path in scan.paths_of(FileKind.SPRING_CONFIG):
        server = owners.of(path)
        if not server or server == "." or server not in by_unit:
            continue
        directories = []
        for key, value, _ in entries(scan, path):
            if env_key(key) != _SEARCH_LOCATIONS:
                continue
            for location in re.split(r"[,\s]+", value.strip()):
                directory = _search_location(path, location)
                if directory and scan.has_dir(directory):
                    directories.append(directory)
        if directories:
            found[server] = tuple(dict.fromkeys(directories))
    return found


def assign_shared(scan: Scan, units: Sequence[Unit], owners: Owners, names: Names, found: Sequence[Anchor]) -> bool:
    """Say which units a configuration server's shared files are about; True when any file was assigned.

    `<name>.yml` (and `<name>-<profile>.yml`) is about the unit called `<name>` when that resolves
    to another unit, `application*.yml` and `bootstrap*.yml` about every unit that fetches from the
    server; a shared file about no unit of this repository is reported, and the server is never a
    file's subject (§8).
    """
    shared = shared_directories(scan, units)
    assigned = False
    for server, directories in sorted(shared.items()):
        fetchers = sorted(
            {
                anchor.unit
                for anchor in found
                if anchor.family is AnchorFamily.SERVICE_NAMES
                and anchor.role is AnchorRole.USE
                and anchor.unit
                and anchor.unit != server
                and names.unit_of(anchor.norm_key) == server
            }
        )
        for path in scan.paths_of(FileKind.SPRING_CONFIG, FileKind.YAML, FileKind.PROPERTIES):
            if not any(path.startswith(f"{directory}/") for directory in directories):
                continue
            stem = os.path.basename(path).rsplit(".", 1)[0]
            if stem.lower().startswith(("application", "bootstrap")):
                owners.assign(path, fetchers)
            else:
                target = _unit_named(names, stem, server)
                owners.assign(path, (target,) if target else ())
                if not target:
                    scan.diagnose(
                        DiagnosticCode.IGNORED_MANIFEST,
                        f"{path} is served by {server} and names no other unit of this repository",
                        path,
                    )
            assigned = True
    return assigned


def _unit_named(names: Names, stem: str, server: str) -> str:
    """The unit a shared file's name resolves to, with Spring's `-<profile>` suffixes stripped one by one."""
    candidate = stem
    while candidate:
        unit = names.unit_of(candidate)
        if unit and unit != server:
            return unit
        shorter = _PROFILE_SUFFIX.sub("", candidate)
        if shorter == candidate:
            return ""
        candidate = shorter
    return ""


def _search_location(path: str, location: str) -> str:
    """A `search-locations` entry as a directory here: `classpath:/shared` is under the resources root."""
    cleaned = location.strip().strip("\"'")
    if cleaned.startswith("classpath:"):
        resources = (
            path[: path.index(_SPRING_RESOURCES) + len(_SPRING_RESOURCES) - 1] if _SPRING_RESOURCES in path else ""
        )
        return repo_path(resources, cleaned[len("classpath:") :].lstrip("/") or ".") or resources
    if cleaned.startswith("file:"):
        return repo_path(parent_dir(path), cleaned[len("file:") :].lstrip("/"))
    return repo_path(parent_dir(path), cleaned)


def _is_configuration_yaml(scan: Scan, path: str, owners: Owners) -> bool:
    """The YAML a unit keeps as its settings, and not the YAML a tool keeps beside its code."""
    name = os.path.basename(path)
    if scan.files[path].size > MAX_CONFIGURATION_BYTES or name.startswith("."):
        return False
    if any(is_resource(about) for about in owners.about(path)):
        # A resource's own directory (§6): its scrape list, its data sources, whatever they are called.
        return True
    if not owners.of(path):
        return False
    if GENERATED_FILE.search(path) or _is_kubernetes(scan, path):
        return False
    return bool(_CONFIGURATION_YAML.match(name)) or os.path.basename(parent_dir(path)).lower() in _CONFIGURATION_DIRS


def _file(scan: Scan, path: str, unit: str) -> list[Anchor]:
    found: list[Anchor] = []
    definition = AnchorFamily.CONFIGURATION
    # A `.properties` that is not Spring's own configuration is often a message bundle, and
    # `label.server=Server` is a label; only a value shaped like a host or a connection counts there.
    strict = scan.files[path].kind is FileKind.PROPERTIES
    for key, value, line in entries(scan, path):
        if strict and not _host_shaped(value, key):
            continue
        interesting = CONNECTION_KEY.search(key) or HOST_KEY.search(key.split(".")[-1])
        if interesting:
            found.append(_anchor(definition, AnchorRole.DEF, key, env_key(key), path, line, unit))
        found += [
            Anchor(
                family=AnchorFamily.SERVICE_NAMES,
                role=AnchorRole.USE,
                key=host,
                norm_key=alias_key(host),
                file=path,
                line=line,
                unit=unit,
                setting=key,
            )
            for host in hosts_in(_expanded(value), key)
        ]
        found += _connection(key, value, path, line, unit)
        for name, default in PLACEHOLDER.findall(value):
            found.append(_anchor(definition, AnchorRole.USE, name, env_key(name), path, line, unit))
            del default
    return found


def _host_shaped(value: str, key: str) -> bool:
    """A URL, a `host:port`, a connection string or a store scheme; a bare word is not a host here."""
    expanded = _expanded(value)
    return "://" in expanded or "=" in expanded or bool(service_host(expanded)) or bool(STORE_SCHEME.match(expanded))


def _connection(key: str, value: str, path: str, line: int, unit: str) -> list[Anchor]:
    """A connection string names a server and a database; both are things a resource is made of.

    The `Key=Value;` parts are read under a connection key or in a value with no scheme: a
    documentation URL's `?host=&database=` query names nothing (§6 rule 9).
    """
    if not CONNECTION_KEY.search(key) and "://" not in value:
        return []
    found = []
    if CONNECTION_KEY.search(key) or "://" not in value:
        for part, target in _CONNECTION_PART.findall(value):
            if _local(target):
                continue
            found.append(
                _anchor(
                    AnchorFamily.DATA_ACCESS,
                    AnchorRole.USE,
                    f"{part.strip().lower()}={target.strip()}",
                    alias_key(target),
                    path,
                    line,
                    unit,
                    setting=key,
                )
            )
    scheme = STORE_SCHEME.match(value.strip())
    if scheme is not None:
        found.append(
            _anchor(
                AnchorFamily.DATA_ACCESS,
                AnchorRole.USE,
                f"{key}: {scheme.group(1)}",
                env_key(key),
                path,
                line,
                unit,
                setting=key,
            )
        )
    return found


def _schemas(scan: Scan, owners: Owners) -> list[Anchor]:
    """A migration or DDL directory says what a database holds, which is what makes it a resource.

    Only DDL (`.sql`, `.ddl`) or the files of a migrations directory count: a `db/` package of
    source is code that talks to a database, not a declaration of what it holds.
    """
    found: list[Anchor] = []
    seen: set[str] = set()
    for directory, names in sorted(scan.by_dir.items()):
        segments = directory.split("/")
        files = sorted(name for name in names if not GENERATED_FILE.search(name))
        migrating = any(part.lower() in MIGRATION_DIRS for part in segments)
        declaring_files = [
            name
            for name in files
            if name.lower().endswith(_DDL_SUFFIXES) or (migrating and name.lower().endswith(_MIGRATION_SUFFIXES))
        ]
        if not declaring_files:
            continue
        depth = next((index for index, part in enumerate(segments) if part.lower() in SCHEMA_DIRS), -1)
        schema = "/".join(segments[: depth + 1]) if depth >= 0 else directory
        if schema in seen:
            continue
        # One anchor per schema directory, wherever its files sit inside it: what a database holds
        # is the directory's fact, not each migration's. The place is the key: three services each
        # keep a `db`, and a bare `db` tells them apart from nothing (§6 rule 1).
        seen.add(schema)
        declaring = [name for name in declaring_files if _DECLARES_SCHEMA.match(name)]
        path = f"{directory}/{(declaring or declaring_files)[0]}"
        found.append(
            _anchor(AnchorFamily.DATA_ACCESS, AnchorRole.DEF, schema, alias_key(schema), path, 1, owners.of(path))
        )
    return found


def _anchor(
    family: AnchorFamily,
    role: AnchorRole,
    key: str,
    norm_key: str,
    path: str,
    line: int,
    unit: str,
    setting: str = "",
) -> Anchor:
    return Anchor(
        family=family,
        role=role,
        key=key,
        norm_key=norm_key,
        file=path,
        line=line,
        unit=unit,
        tier=Tier.T1,
        setting=setting,
    )


def _local(target: str) -> bool:
    """Whether a connection part names this machine, in any of the spellings a driver accepts:
    `localhost`, `localhost,1433`, `localhost\\SQLEXPRESS`, `tcp:localhost,1433`, `np:\\localhost\\pipe`."""
    cleaned = _NETWORK_PROTOCOL.sub("", target.strip().strip("\"'").lstrip("\\")).lstrip("\\")
    first = re.split(r"[\\,:/]", cleaned, maxsplit=1)[0].strip().lower()
    # `(LocalDb)\MSSQLLocalDB` is SQL Server's per-user instance on this machine.
    return cleaned.lower() in LOCAL_HOSTS or first in LOCAL_HOSTS or first.strip("()") == "localdb"


def _expanded(value: str) -> str:
    """A value with each `${KEY:default}` replaced by its default, which is what a run without it sees."""
    return PLACEHOLDER.sub(lambda found: found.group(2) or "", value)


def _flatten(node: object, scan: Scan, path: str, prefix: str = "") -> list[tuple[str, str, int]]:
    """Every scalar of a nested document as a dotted key, with the line its value sits on."""
    if isinstance(node, dict):
        found = []
        for key, value in node.items():
            found += _flatten(value, scan, path, f"{prefix}.{key}" if prefix else str(key))
        return found
    if isinstance(node, list):
        found = []
        for index, value in enumerate(node):
            found += _flatten(value, scan, path, f"{prefix}[{index}]")
        return found
    if node is None or isinstance(node, bool) or not prefix:
        return []
    value = str(node)
    # A list item is written without its index, so there only the value can find the line.
    segment = re.split(r"[.\[]", prefix)[-1].rstrip("]")
    return [(prefix, value, scan.line_where(path, "" if segment.isdigit() else segment, value))]


def _properties(text: str) -> list[tuple[str, str, int]]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        written = re.match(r"\s*([^#!=:\s][^=:\s]*)\s*[=:]\s*(.*)$", line)
        if written is not None:
            found.append((written.group(1), written.group(2).strip(), number))
    return found


def _dotenv_lines(text: str) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        key = line.strip().removeprefix("export ").split("=", 1)[0].strip()
        if key and not line.strip().startswith("#"):
            found.append((number, key))
    return found


def _json_with_comments(scan: Scan, path: str) -> dict:
    """`appsettings.json` as .NET reads it: comments and a trailing comma are allowed; nonsense is a row."""
    text = scan.text(path)
    without_comments = re.sub(r"//[^\n\"]*$|/\*.*?\*/", "", text, flags=re.M | re.S)
    try:
        loaded = json.loads(re.sub(r",(\s*[}\]])", r"\1", without_comments))
    except (json.JSONDecodeError, RecursionError) as error:
        scan.diagnose(DiagnosticCode.UNREADABLE_MANIFEST, f"{path} is not JSON: {type(error).__name__}", path)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _is_kubernetes(scan: Scan, path: str) -> bool:
    text = scan.text(path)
    return "apiVersion" in text and "kind:" in text
