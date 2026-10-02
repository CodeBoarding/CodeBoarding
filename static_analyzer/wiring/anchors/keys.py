"""What a key is, whose unit a file is, and when a value names a service: the readers' vocabulary.

Two normalisations, because two things are compared. An environment or configuration key is
compared the way Spring's relaxed binding and ASP.NET's `A__B` rule compare it — case and
punctuation carry no meaning, so `spring.datasource.url` and `SPRING_DATASOURCE_URL` are one key. A
name a unit answers to is compared the way the unit table spells it (`units.alias_key`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import Unit

#: Hosts that are the machine talking to itself, never another unit.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal", "*"})

#: Suffixes a cluster adds to a service's own name. Anything else with a dot is somebody's domain.
INTERNAL_SUFFIXES = (".svc.cluster.local", ".cluster.local", ".svc", ".local", ".internal")

#: A key whose name says its value is a host: `SERVICE_URL`, `eureka...defaultZone`, `x.addresses`.
HOST_KEY = re.compile(
    r"(?i)(?:host|hostname|hosts|addr|address|addresses|server|servers|endpoint|endpoints|url|uri|"
    r"urls|uris|serviceid|service-id|service_id|defaultzone|broker|bootstrapservers)$"
)

_NOT_A_KEY = re.compile(r"[^A-Z0-9]")
#: The schemes that carry a host a unit could answer to. A `maui://` deep link carries a callback.
_NETWORK_SCHEME = (
    r"(?:https?|https\+http|http\+https|lb|grpc[s]?|ws[s]?|amqps?|kafka|redis[s]?|mongodb(?:\+srv)?|"
    r"postgres(?:ql)?|mysql|mariadb|mssql|sqlserver|jdbc:[a-z0-9]+|tcp|udp|configserver|eureka|consul|"
    r"elasticsearch|clickhouse|cassandra|memcached|s3|minio|smtp[s]?|otlp|nats|mqtt|stomp|pulsar|"
    r"zookeeper|etcd|bolt|ldaps?|ftps?|sftp|ssh|thrift)"
)
_IN_URL = re.compile(rf"^(?:{_NETWORK_SCHEME}:)*//(?:[^@/\s]*@)?([A-Za-z0-9_][\w.-]*)", re.IGNORECASE)
_HOST_PORT = re.compile(r"^([A-Za-z][\w.-]*):(\d{2,5})(?:[/?].*)?$")
_BARE = re.compile(r"^[A-Za-z][\w.-]*$")


def env_key(key: str) -> str:
    """The key as a join compares it: `spring.datasource.url` and `SPRING_DATASOURCE_URL` are one."""
    return _NOT_A_KEY.sub("", key.upper())


def hosts_in(value: str, key: str = "") -> list[str]:
    """Every service name a value names, in the order written; empty when it names nobody here.

    A comma or space separates several (`defaultZone`, `bootstrap-servers`), and each is read on
    its own.
    """
    found = []
    for part in re.split(r"[,\s]+", value.strip()):
        host = service_host(part, key)
        if host and host not in found:
            found.append(host)
    return found


def service_host(value: str, key: str = "") -> str:
    """The service name a single value names, or an empty string.

    A URL or a `host:port` gives its host; a bare word is a host only where the key says so. A
    dotted public name is somebody's domain and never resolves to a unit (§6 rule 9), while a
    cluster-internal name is the service it starts with.
    """
    candidate = value.strip().strip("\"'").rstrip("/")
    if not candidate or "$" in candidate or "{" in candidate:
        return ""
    found = _IN_URL.match(candidate) or _HOST_PORT.match(candidate)
    internal = candidate.endswith(INTERNAL_SUFFIXES) and _BARE.fullmatch(candidate)
    if found is not None:
        host = found.group(1)
    elif internal or (_BARE.fullmatch(candidate) and HOST_KEY.search(_last_segment(key))):
        # A cluster suffix names a service whatever key it sits under; a bare word needs the key
        # to say it is a host.
        host = candidate
    else:
        return ""
    host = host.lower().strip(".")
    if not host or host in LOCAL_HOSTS:
        return ""
    if "." not in host:
        return host
    for suffix in INTERNAL_SUFFIXES:
        if host.endswith(suffix):
            return host[: -len(suffix)].split(".")[0]
    return ""


class Owners:
    """Which unit a file belongs to, and which units a file is about when those differ.

    A file belongs to the unit whose directory is its longest ancestor. It is *about* that unit
    unless the pass says otherwise: a configuration server ships other units' settings from its
    own directory, so `shared/<name>.yml` is about `<name>` and never about the server (§8).
    """

    def __init__(self, units: Sequence[Unit]) -> None:
        self._directories = sorted((unit.dir for unit in units if unit.dir != "."), key=len, reverse=True)
        self._root = any(unit.dir == "." for unit in units)
        self._about: dict[str, tuple[str, ...]] = {}

    def of(self, path: str) -> str:
        for directory in self._directories:
            if path == directory or path.startswith(f"{directory}/"):
                return directory
        return "." if self._root else ""

    def about(self, path: str) -> tuple[str, ...]:
        """The units a file is about: the one it belongs to, unless `assign` said otherwise."""
        return self._about.get(path, (self.of(path),))

    def assign(self, path: str, units: Sequence[str]) -> None:
        """Say which units a file is about; an empty sequence says it is about none."""
        self._about[path] = tuple(dict.fromkeys(units))

    def overridden(self) -> tuple[str, ...]:
        return tuple(sorted(self._about))


class Names:
    """Which unit answers to a name, when exactly one does (§6 rule 7)."""

    def __init__(self, units: Sequence[Unit]) -> None:
        claims: dict[str, set[str]] = {}
        for unit in units:
            for alias in unit.aliases:
                claims.setdefault(alias_key(alias), set()).add(unit.id)
        self._by_name = {name: next(iter(ids)) for name, ids in claims.items() if len(ids) == 1}

    def unit_of(self, name: str) -> str:
        return self._by_name.get(alias_key(name), "")


def _last_segment(key: str) -> str:
    """The part of a key its name-sense lives in: `spring.redis.host` is about a `host`."""
    return re.sub(r"\[\d+\]$", "", re.split(r"[.:_-]", key.replace("__", "."))[-1] if key else "")
