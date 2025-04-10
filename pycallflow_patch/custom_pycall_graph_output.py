from llm_graph_repr import Node
from pycallgraph.output import GraphvizOutput


class LLMAwareOutput(GraphvizOutput):
    """
    class which aims at collecting the CFG in a way which is nice to represent in LLM
    context.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.nodes = {}

    def done(self):
        # Invoked after the tracing is done.
        return self.generate()
    
    def generate(self):
        nodes = self.generate_nodes()
        edges = self.generate_edges()
        groups = self.generate_groups()

        return groups, nodes, edges
    
    def generate_groups(self):
        # Group the nodes and return the groups;
        groups = {}        
        for group, nodes in self.processor.groups():
            groups[group] = []
            for node in nodes:
                self.nodes[node.name].to_group(group)
                groups[group].append(node.name)
        return groups


    def generate_nodes(self):
        for node in self.processor.nodes():
            self.nodes[node.name] = Node(node)
        return self.nodes
    
    def generate_edges(self):
        for edge in self.processor.edges():
            src_node = self.nodes[edge.src_func]
            dest_node = self.nodes[edge.dst_func]

            src_node.add_edge(dest_node)
        return super().generate_edges()