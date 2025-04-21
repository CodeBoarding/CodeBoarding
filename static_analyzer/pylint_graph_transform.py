import pydot

class DotGraphTransformer:
    def __init__(self, dot_file, project_name):
        self.dot_file = dot_file
        self.project_name = project_name
        self._load()

    def _load(self):
        # Perform transformation logic here
        (self.G,) = pydot.graph_from_dot_file(self.dot_file)

    def transform(self):
        # Perform transformation logic here
        result = []
        for edge in self.G.get_edges():
            src = edge.get_source()
            dst = edge.get_destination()
            attrs = edge.get_attributes()
            if self.project_name.lower() not in src.lower() or self.project_name.lower() not in dst.lower():
                continue

            edge_s = ""
            edge_s += f"{src} -> {dst}"

            for k, v in attrs.items():
                edge_s += f" [{k}={v}]"
            result.append(edge_s)
        return "\n".join(result)

if __name__ == "__main__":
    transformer = DotGraphTransformer("/home/ivan/StartUp/CodeBoarding/static_analyzer/pylint_analyze/test_out.dot", "markitdown")
    transformed_graph = transformer.transform()
    print(transformed_graph)