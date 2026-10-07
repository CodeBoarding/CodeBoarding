"""A name in code: the service a literal URL names, and the client a framework binds by name.

The pass reads source for exactly two things and this is one of them (§3). Only a literal counts: a
base address assembled at run time is T3 and never an anchor (§6 rule 4), and a dotted public name
is somebody's domain rather than a unit (§6 rule 9).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from static_analyzer.wiring.anchors.keys import Owners, service_host
from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import Anchor, AnchorFamily, AnchorRole

#: `"http://vets-service/"`, `"lb://customers-service"`, `new("https+http://catalog-api")`.
URL_LITERAL = re.compile(r"""["'`]((?:https?|lb|grpc|amqp|https\+http|http\+https)://[A-Za-z][^"'`\s]*)["'`]""")

#: A client a framework resolves by name rather than by address.
FEIGN_CLIENT = re.compile(r"@FeignClient\s*\(\s*(?:\"([\w.-]+)\"|[^)]*?\b(?:name|value)\s*=\s*\"([\w.-]+)\")")
DISCOVERY_LOOKUP = re.compile(r"\b(?:getInstances|getInstancesById|choose)\s*\(\s*\"([\w.-]+)\"")

#: What a Spring application says it is: a client of a registry, or the server other units look for.
REGISTERS = re.compile(r"@Enable(?:DiscoveryClient|EurekaClient|FeignClients)\b")
PROVIDES = re.compile(r"@Enable(?:ConfigServer|EurekaServer|AdminServer|TurbineStream|Turbine)\b")

_TRIGGERS = ("://", "@FeignClient", "getInstances", "@Enable")


def read(sources: Sequence[tuple[str, str]], owners: Owners) -> list[Anchor]:
    """Every service name written as a literal in code."""
    found: list[Anchor] = []
    for path, text in sources:
        if not any(trigger in text for trigger in _TRIGGERS):
            continue
        unit = owners.of(path)
        found += _literals(path, text, unit)
        found += _annotations(path, text, unit)
    return found


def _literals(path: str, text: str, unit: str) -> list[Anchor]:
    found = []
    for pattern, groups in ((URL_LITERAL, 1), (FEIGN_CLIENT, 2), (DISCOVERY_LOOKUP, 1)):
        for match in pattern.finditer(text):
            written = next((group for group in match.groups()[:groups] if group), "")
            host = service_host(written) if "//" in written else written.lower()
            if not host:
                continue
            found.append(
                Anchor(
                    family=AnchorFamily.SERVICE_NAMES,
                    role=AnchorRole.USE,
                    key=written,
                    norm_key=alias_key(host),
                    file=path,
                    line=text.count("\n", 0, match.start()) + 1,
                    column=match.start() - text.rfind("\n", 0, match.start()),
                    unit=unit,
                )
            )
    return found


def _annotations(path: str, text: str, unit: str) -> list[Anchor]:
    """A role a unit takes on: registering with a registry, or being the server others look for.

    The normalised key is `role:<annotation>`, a form no unit's name can take, so a role is never
    mistaken for a name a unit answers to.
    """
    found = []
    for pattern, role in ((REGISTERS, AnchorRole.USE), (PROVIDES, AnchorRole.DEF)):
        for match in pattern.finditer(text):
            written = match.group(0)
            found.append(
                Anchor(
                    family=AnchorFamily.SERVICE_NAMES,
                    role=role,
                    key=written,
                    norm_key=f"role:{alias_key(written)}",
                    file=path,
                    line=text.count("\n", 0, match.start()) + 1,
                    column=match.start() - text.rfind("\n", 0, match.start()),
                    unit=unit,
                )
            )
    return found
