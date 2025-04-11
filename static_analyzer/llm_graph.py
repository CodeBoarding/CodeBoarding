class Node:
    def __init__(self, node):
        self.id = node.name
        self.num_calls = node.calls.value
        self.neighbours = []
        self.group = None
        self.grouping_name = node.name
        self.parse_name(node.name)

    def parse_name(self, name):
        """
        Very naive parsing for now -> PascalCase means Class def.
        I started doing loading, but this will make it much more complicated.
        """
        parts = name.split(".")
        class_name = ""

        is_class = False
        for part in parts:
            class_name += part
            if part[0].isupper():
                is_class = True
                break
            class_name += "."
        if is_class:
            self.grouping_name = class_name

    def add_edge(self, neighbour):
        if neighbour.id == self.id:
            print("WARNING: self is neighbour")
            return
        self.neighbours.append(neighbour)

    def to_group(self, group):
        assert self.group is None, f"{self.id} is already part of group: {self.group}, cannot be added to {group}"
        self.group = group


def regroup_nodes(nodes):
    new_nodes = []
    for node in nodes:
        new_neighbours = []
        for neighb in node.neighbours:
            if neighb.grouping_name.startswith("_"):
                # Filter out system private module calls
                continue
            # if any(n.grouping_name == neighb.grouping_name for n in new_neighbours):
            #     nb = [i for i in filter(lambda n: n.grouping_name == neighb.grouping_name, new_neighbours)][0]
            #     nb.num_calls += neighb.num_calls
            #     if nb.grouping_name == 'markitdown.converters._xlsx_converter.XlsxConverter':
            #         import pdb; pdb.set_trace()
            #     continue
            new_neighbours.append(neighb)
        node.neighbours = new_neighbours
        new_nodes.append(node)
    return new_nodes

def print_CFG(node, prefix=""):
    print(prefix + node.id)
    new_prefix = " " * len(prefix)

    for neighbour in node.neighbours:
        print_CFG(neighbour, new_prefix  + "|--")

class MockNode:
    def __init__(self, name, calls):
        self.name = name
        self.calls = type('Calls', (), {'value': calls})()


def build_tree_string(node, prefix="", is_last=True, visited=None):
    if visited is None:
        visited = set()

    # Prevent re-visiting
    if node.id in visited:
        return ""
    visited.add(node.id)

    # Build line for current node
    connector = "└── " if is_last else "├── "
    line = f"{prefix}{connector}{node.id} (calls: {node.num_calls}"
    if node.group:
        line += f", group: {node.group}"
    line += ")\n"

    # Update prefix for children
    new_prefix = prefix + ("    " if is_last else "│   ")
    total = len(node.neighbours)

    # Recurse and collect all child lines
    for i, child in enumerate(node.neighbours):
        line += build_tree_string(child, new_prefix, i == total - 1, visited)

    return line


def print_adjacency_list(nodes):
    for node in nodes:
        neighbours = ', '.join(n.id for n in node.neighbours) or "None"
        print(f"{node.id} -> {neighbours}")
