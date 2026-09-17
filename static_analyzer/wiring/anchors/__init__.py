"""Where a wiring key is declared or used: one reader per source, one anchor per place.

An anchor is a place, not an edge. The compose file that sets `SERVICE_URL`, the code that reads
it, the route a gateway matches, the connection string that names a database: PR 4 joins them, and
this layer only records what is written where, with the unit it is about and the line it is on.

The readers that look at source (`service_names`, `routes`, `readers`) share one pass over it,
because reading a repository's source twice is the only expensive thing this pass could do, and
they all read it with its asides blanked: a docstring's usage example, a block comment and a
commented-out line are not declarations.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from static_analyzer.wiring.anchors import configuration, deployment, readers, routes, service_names
from static_analyzer.wiring.anchors.keys import Names, Owners
from static_analyzer.wiring.compose import ComposeProject
from static_analyzer.wiring.scan import Scan
from static_analyzer.wiring_results import Anchor, Unit

#: What a file writes down without meaning it: a docstring, a block comment, a line comment. A
#: `//` counts only where a comment can start — never inside `http://`.
_ASIDE = re.compile(
    r"\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|/\*[\s\S]*?\*/|^[ \t]*#[^\n]*|(?:^|(?<=[\s;{}()]))//[^\n]*", re.M
)


def collect(scan: Scan, units: Sequence[Unit], projects: Sequence[ComposeProject]) -> list[Anchor]:
    """Every anchor the readers find, deduplicated and sorted, so two runs agree."""
    owners, names = Owners(units), Names(units)
    sources = [(path, blank_asides(scan.text(path))) for path in scan.sources()]
    found = [
        *deployment.read(scan, list(projects), owners, names),
        *configuration.read(scan, owners),
        *service_names.read(sources, owners),
        *routes.read(scan, sources, owners),
        *readers.read(sources, owners),
    ]
    # A configuration server's shared files are about the units they configure, which is known
    # only once every reader has said who fetches from it; those files are read once more.
    if configuration.assign_shared(scan, units, owners, names, found):
        overridden = set(owners.overridden())
        found = [anchor for anchor in found if anchor.file not in overridden]
        found += configuration.read(scan, owners, paths=sorted(overridden))
    return sorted(set(found), key=_order)


def blank_asides(text: str) -> str:
    """Source with its comments and docstrings blanked, so every line and column still says where it is."""
    return _ASIDE.sub(lambda aside: re.sub(r"[^\n]", " ", aside.group()), text)


def to_json(anchor: Anchor) -> dict:
    return {
        "family": anchor.family.value,
        "role": anchor.role.value,
        "key": anchor.key,
        "norm_key": anchor.norm_key,
        "file": anchor.file,
        "line": anchor.line,
        "column": anchor.column,
        "unit": anchor.unit,
        "tier": anchor.tier.value,
        "setting": anchor.setting,
    }


def _order(anchor: Anchor) -> tuple[str, int, int, str, str, str, str, str, str, str]:
    """A complete key: two anchors that differ anywhere sort by that difference, whatever the hash seed."""
    return (
        anchor.file,
        anchor.line,
        anchor.column,
        anchor.family.value,
        anchor.role.value,
        anchor.key,
        anchor.norm_key,
        anchor.unit,
        anchor.tier.value,
        anchor.setting,
    )
