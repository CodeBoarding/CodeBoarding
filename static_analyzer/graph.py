class Node:
    def __init__(self, fully_qualified_name, file_path, line_start, line_end):
        self.fully_qualified_name = fully_qualified_name
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.methods_called_by_me = set()

    def added_method_called_by_me(self, node):
        """Add a calling method to this node."""
        if isinstance(node, Node):
            self.methods_called_by_me.add(node.fully_qualified_name)
        else:
            raise ValueError("Expected a Node instance.")

    def __hash__(self):
        return hash(self.fully_qualified_name)

    def __repr__(self):
        return f"Node({self.fully_qualified_name}, {self.file_path}, {self.line_start}-{self.line_end})"


class Edge:
    def __init__(self, src_node: Node, dst_node: Node):
        self.src_node = src_node
        self.dst_node = dst_node

    def get_source(self):
        return self.src_node.fully_qualified_name

    def get_destination(self):
        return self.dst_node.fully_qualified_name

    def __repr__(self):
        return f"Edge({self.src_node.fully_qualified_name} -> {self.dst_node.fully_qualified_name})"


class CallGraph:
    def __init__(self, nodes=None, edges=None):
        self.nodes = nodes if nodes is not None else {}
        self.edges = edges if edges is not None else []
        self._edge_set = set()  # Track existing edges to avoid duplicates

    def add_node(self, node):
        if node.fully_qualified_name not in self.nodes:
            self.nodes[node.fully_qualified_name] = node

    def add_edge(self, src_name, dst_name):
        if src_name not in self.nodes or dst_name not in self.nodes:
            raise ValueError("Both source and destination nodes must exist in the graph.")

        # Check for duplicate edges
        edge_key = (src_name, dst_name)
        if edge_key in self._edge_set:
            return  # Edge already exists

        edge = Edge(self.nodes[src_name], self.nodes[dst_name])
        self.edges.append(edge)
        self._edge_set.add(edge_key)

        # Update the destination node's calling methods
        self.nodes[src_name].added_method_called_by_me(self.nodes[dst_name])

    def __str__(self):
        result = f"Control flow graph with {len(self.nodes)} nodes and {len(self.edges)} edges\n"
        for _, node in self.nodes.items():
            result += f"Method {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}\n"
        return result
