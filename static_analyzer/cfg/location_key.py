"""Physical-location identity, used to dedup the several qualified names the LSP
can emit for one symbol."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LocationKey:
    """Hashable key identifying a symbol's physical location in the source tree."""

    file_path: str
    line_start: int
    line_end: int
    node_type: int
    col_start: int = 0
