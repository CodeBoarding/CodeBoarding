"""What configuration declares: a key, the host its value names, and the store it connects to.

Spring YAML and properties, `appsettings*.json`, `.env` files and a unit's own YAML (a Prometheus
scrape list, a Grafana datasource) are all the same shape once flattened: a dotted key, a value,
and a line. A key is an anchor when something can join it — the code that reads it, the host it
names, the database it connects to — and a `${KEY:default}` in a value is a use of that key.
"""

from __future__ import annotations

import json
import re

from static_analyzer.wiring.anchors.keys import HOST_KEY, LOCAL_HOSTS, Owners, env_key, hosts_in
from static_analyzer.wiring.scan import GENERATED_FILE, MAX_CONFIGURATION_BYTES, FileKind, Scan, parse_dotenv
from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import Anchor, AnchorFamily, AnchorRole, Tier

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
_SCHEMA_SUFFIXES = (".sql", ".ddl", ".xml", ".yaml", ".yml", ".json", ".py", ".rb", ".cs", ".java")

#: A file whose own name says it defines the schema, rather than one that merely mentions the word:
#: `schema.sql` declares, while `0041_create_attachments.py` and `FixOrderSchema.Designer.cs` are a
#: point in a history that happens to contain it.
_DECLARES_SCHEMA = re.compile(r"(?i)^(?:schema|create|init|initial)[\w.-]*\.")


def read(scan: Scan, owners: Owners) -> list[Anchor]:
    """Every anchor a configuration file writes down, plus the schema directories units keep."""
    found: list[Anchor] = []
    for path in scan.paths_of(FileKind.SPRING_CONFIG, FileKind.PROPERTIES, FileKind.DOTNET_SETTINGS, FileKind.DOTENV):
        # A catalogue is a catalogue in whatever notation it is written: the cap is about how much a
        # file declares, not about YAML.
        if scan.files[path].size <= MAX_CONFIGURATION_BYTES:
            found += _file(scan, path, owners.of(path))
    for path in scan.paths_of(FileKind.YAML):
        # A unit's own YAML is configuration; a catalogue or a lockfile sitting in one is data.
        if not owners.of(path) or scan.files[path].size > MAX_CONFIGURATION_BYTES:
            continue
        if GENERATED_FILE.search(path) or _is_kubernetes(scan, path):
            continue
        found += _file(scan, path, owners.of(path))
    found += _schemas(scan, owners)
    return found


def entries(scan: Scan, path: str) -> list[tuple[str, str, int]]:
    """A configuration file as (key, value, line), whatever notation it is written in."""
    if path.endswith(".properties"):
        return _properties(scan.text(path))
    if scan.files[path].kind is FileKind.DOTENV:
        text = scan.text(path)
        lines = {key: index for index, key in _dotenv_lines(text)}
        return [(key, value, lines.get(key, 1)) for key, value in parse_dotenv(text).items()]
    if path.endswith(".json"):
        return _flatten(_json_with_comments(scan.text(path)), scan, path)
    flattened: list[tuple[str, str, int]] = []
    for document in scan.documents(path):
        flattened += _flatten(document, scan, path)
    return flattened


def _file(scan: Scan, path: str, unit: str) -> list[Anchor]:
    found: list[Anchor] = []
    definition = AnchorFamily.CONFIGURATION
    for key, value, line in entries(scan, path):
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
            )
            for host in hosts_in(_expanded(value), key)
        ]
        found += _connection(key, value, path, line, unit)
        for name, default in PLACEHOLDER.findall(value):
            found.append(_anchor(definition, AnchorRole.USE, name, env_key(name), path, line, unit))
            del default
    return found


def _connection(key: str, value: str, path: str, line: int, unit: str) -> list[Anchor]:
    """A connection string names a server and a database; both are things a resource is made of."""
    if not CONNECTION_KEY.search(key) and "://" not in value:
        return []
    found = []
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
            )
        )
    scheme = STORE_SCHEME.match(value.strip())
    if scheme is not None:
        found.append(
            _anchor(
                AnchorFamily.DATA_ACCESS, AnchorRole.USE, f"{key}: {scheme.group(1)}", env_key(key), path, line, unit
            )
        )
    return found


def _schemas(scan: Scan, owners: Owners) -> list[Anchor]:
    """A migration or DDL directory says what a database holds, which is what makes it a resource."""
    found: list[Anchor] = []
    seen: set[str] = set()
    for directory, names in sorted(scan.by_dir.items()):
        segments = directory.split("/")
        depth = next((index for index, part in enumerate(segments) if part.lower() in SCHEMA_DIRS), -1)
        schema = "/".join(segments[: depth + 1]) if depth >= 0 else ""
        files = sorted(
            name for name in names if name.lower().endswith(_SCHEMA_SUFFIXES) and not GENERATED_FILE.search(name)
        )
        if not schema or schema in seen or not files:
            continue
        # One anchor per schema directory, wherever its files sit inside it: what a database holds
        # is the directory's fact, not each migration's.
        seen.add(schema)
        # The place is the key: three services each keep a `db`, and a bare `db` tells them apart
        # from nothing (§6 rule 1).
        declaring = [name for name in files if _DECLARES_SCHEMA.match(name)]
        path = f"{directory}/{(declaring or files)[0]}"
        found.append(
            _anchor(AnchorFamily.DATA_ACCESS, AnchorRole.DEF, schema, alias_key(schema), path, 1, owners.of(path))
        )
    return found


def _anchor(family: AnchorFamily, role: AnchorRole, key: str, norm_key: str, path: str, line: int, unit: str) -> Anchor:
    return Anchor(family=family, role=role, key=key, norm_key=norm_key, file=path, line=line, unit=unit, tier=Tier.T1)


def _local(target: str) -> bool:
    """Whether a connection part names this machine: `localhost`, `localhost,1433`, `localhost\\SQLEXPRESS`."""
    cleaned = target.strip().strip("\"'")
    return cleaned.lower() in LOCAL_HOSTS or re.split(r"[\\,:/]", cleaned, maxsplit=1)[0].strip().lower() in LOCAL_HOSTS


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


def _json_with_comments(text: str) -> dict:
    """`appsettings.json` as .NET reads it: comments and a trailing comma are allowed."""
    without_comments = re.sub(r"//[^\n\"]*$|/\*.*?\*/", "", text, flags=re.M | re.S)
    try:
        loaded = json.loads(re.sub(r",(\s*[}\]])", r"\1", without_comments))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _is_kubernetes(scan: Scan, path: str) -> bool:
    text = scan.text(path)
    return "apiVersion" in text and "kind:" in text
