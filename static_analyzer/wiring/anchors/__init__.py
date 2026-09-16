"""Where a wiring key is declared or used: one reader per source, one anchor per place.

An anchor is a place, not an edge. The compose file that sets `SERVICE_URL`, the code that reads
it, the route a gateway matches, the connection string that names a database: PR 4 joins them, and
this layer only records what is written where, with the unit it is about and the line it is on.

The two readers that look at source (`service_names`, `readers`) share one pass over it, because
reading a repository's source twice is the only expensive thing this pass could do.
"""

from __future__ import annotations

from collections.abc import Sequence

from static_analyzer.wiring.anchors import configuration, deployment, readers, routes, service_names
from static_analyzer.wiring.anchors.keys import Names, Owners
from static_analyzer.wiring.compose import ComposeProject
from static_analyzer.wiring.scan import Scan
from static_analyzer.wiring_results import Anchor, Unit


def collect(scan: Scan, units: Sequence[Unit], projects: Sequence[ComposeProject]) -> list[Anchor]:
    """Every anchor the readers find, deduplicated and sorted, so two runs agree."""
    owners, names = Owners(units), Names(units)
    sources = [(path, scan.text(path)) for path in scan.sources()]
    found = [
        *deployment.read(scan, list(projects), owners, names),
        *configuration.read(scan, owners),
        *service_names.read(sources, owners),
        *routes.read(scan, sources, owners),
        *readers.read(sources, owners),
    ]
    return sorted(set(found), key=_order)


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
    }


def _order(anchor: Anchor) -> tuple[str, int, int, str, str, str]:
    return anchor.file, anchor.line, anchor.column, anchor.family.value, anchor.role.value, anchor.key
