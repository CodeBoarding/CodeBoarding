from typing import Any

import pydot


def read_dot_file(file_path: str) -> dict[Any, list[Any]]:
    """
    Read a dot file and return its content as a string.
    """
    (G,) = pydot.graph_from_dot_file(file_path)
    result = {}
    for edge in G.get_edges():
        src = edge.get_source()
        dst = edge.get_destination()
        # clean src and dst from ""
        src = src.strip('"')
        dst = dst.strip('"')
        if src not in result:
            result[src] = [dst]
        else:
            result[src].append(dst)
    return result
