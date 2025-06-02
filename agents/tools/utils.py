import json

import pydot


def read_dot_file(file_path: str) -> str:
    """
    Read a dot file and return its content as a string.
    """
    (G,) = pydot.graph_from_dot_file(file_path)
    result = {}
    for edge in G.get_edges():
        src = edge.get_source()
        dst = edge.get_destination()
        if src not in result:
            result[src] = [dst]
        else:
            result[src].append(dst)
    return json.dumps(result)
