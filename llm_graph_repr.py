class Node:
    def __init__(self, node):
        self.id = node.name
        self.neighbours = []
        self.group = None

    def add_edge(self, neighbour):
        self.neighbours.append(neighbour)


    def to_group(self, group):
        assert self.group is None, f"{self.id} is already part of group: {self.group}, cannot be added to {group}"
        self.group = group


def print_CFG(node, prefix=""):
    print(prefix + node.id)
    new_prefix = " " * len(prefix)

    for neighbour in node.neighbours:
        print_CFG(neighbour, new_prefix  + "|--")
