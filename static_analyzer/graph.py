import logging

logger = logging.getLogger(__name__)


class Node:
    def __init__(self, fully_qualified_name, node_type, file_path, line_start, line_end):
        self.fully_qualified_name = fully_qualified_name
        self.file_path = file_path
        self.line_start = line_start
        self.line_end = line_end
        self.type = node_type
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

    def to_networkx(self):
        import networkx as nx

        nx_graph = nx.DiGraph()
        for node in self.nodes.values():
            nx_graph.add_node(node.fully_qualified_name, file_path=node.file_path,
                              line_start=node.line_start, line_end=node.line_end, type=node.type)
        for edge in self.edges:
            nx_graph.add_edge(edge.get_source(), edge.get_destination())
        return nx_graph

    def __str__(self):
        result = f"Control flow graph with {len(self.nodes)} nodes and {len(self.edges)} edges\n"
        for _, node in self.nodes.items():
            if node.methods_called_by_me:
                result += f"Method {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}\n"
        return result

    def llm_str(self, size_limit=2_500_000, skip_nodes=[]):
        """
        Return a string representation with size limits.
        If output exceeds size_limit, group method calls by class.
        """
        # First try the default string representation
        default_str = str(self)

        logger.info(f"[CFG Tool] LLM string: {len(default_str)} characters, size limit: {size_limit} characters")

        if len(default_str) <= size_limit:
            return default_str

        # Group method nodes by class for type 6 (methods)
        class_calls = {}
        function_calls = []

        logger.info(
            f"[CallGraph] Control flow graph is too large, grouping method calls by class. ({len(default_str)} characters)")

        for _, node in self.nodes.items():
            if node in skip_nodes:
                continue
            if node.type == 6 and node.methods_called_by_me:  # type 6 = method
                # Extract class name from fully qualified name
                parts = node.fully_qualified_name.split('.')
                if len(parts) > 1:
                    class_name = '.'.join(parts[:-1])  # Everything except method name

                    if class_name not in class_calls:
                        class_calls[class_name] = {}

                    # Group called methods by their classes
                    for called_method in node.methods_called_by_me:
                        called_parts = called_method.split('.')
                        if len(called_parts) > 1:
                            called_class = '.'.join(called_parts[:-1])
                            if called_class not in class_calls[class_name]:
                                class_calls[class_name][called_class] = 0
                            class_calls[class_name][called_class] += 1
                        else:
                            # Handle cases where called method doesn't have class structure
                            if called_method not in class_calls[class_name]:
                                class_calls[class_name][called_method] = 0
                            class_calls[class_name][called_method] += 1
                else:
                    # This is a function (not a class method), keep original format
                    function_calls.append(
                        f"Function {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}")
            elif node.methods_called_by_me:
                # Non-method nodes that have calls, keep original format
                function_calls.append(
                    f"Function {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}")

        # Build the grouped output
        result = f"Control flow graph with {len(self.nodes)} nodes and {len(self.edges)} edges (grouped view)\n"

        # Add grouped class calls
        for class_name, called_classes in class_calls.items():
            calls_str = []
            for called_class, count in called_classes.items():
                calls_str.append(f"{called_class}({count} methods)")

            if calls_str:
                result += f"Class {class_name} is calling the following classes {', '.join(calls_str)}\n"

        # Add function calls in original format
        for func_call in function_calls:
            result += func_call + "\n"

        logger.info(f"[CallGraph] Control flow graph grouped by class, total characters: {len(result)}")
        return result
