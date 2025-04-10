class Node:
    def __init__(self, node):
        self.id = node.name
        self.num_calls = node.calls.value
        # self.extract_naming(node.name)
        self.neighbours = []
        self.group = None

    def add_edge(self, neighbour):
        if neighbour.id == self.id:
            print("WARNING : self is neighbour")
            return
        self.neighbours.append(neighbour)

    def to_group(self, group):
        assert self.group is None, f"{self.id} is already part of group: {self.group}, cannot be added to {group}"
        self.group = group


def print_CFG(node, prefix=""):
    print(prefix + node.id)
    new_prefix = " " * len(prefix)

    for neighbour in node.neighbours:
        print_CFG(neighbour, new_prefix  + "|--")

class MockNode:
    def __init__(self, name, calls):
        self.name = name
        self.calls = type('Calls', (), {'value': calls})()


def print_tree(node, prefix="", is_last=True, visited=None):
    if visited is None:
        visited = set()

    # Prevent re-visiting (even though it's a tree, just in case)
    if node.id in visited:
        return
    visited.add(node.id)

    # Tree line formatting
    connector = "└── " if is_last else "├── "
    print(f"{prefix}{connector}{node.id} (calls: {node.num_calls}" + (f", group: {node.group}" if node.group else "") + ")")

    # Update prefix for children
    prefix += "    " if is_last else "│   "
    total = len(node.neighbours)
    for i, child in enumerate(node.neighbours):
        print_tree(child, prefix, i == total - 1, visited)


def print_adjacency_list(nodes):
    for node in nodes:
        neighbours = ', '.join(n.id for n in node.neighbours) or "None"
        print(f"{node.id} -> {neighbours}")
