from static_analyzer.llm_graph import regroup_nodes, build_tree_string
from static_analyzer.pycallflow_patch.custom_pycall_graph_output import LLMAwareOutput
from static_analyzer.pycallflow_patch.module_call_graph import ModuleCallGraph


class Analyzer:
    def __init__(self, module_name, code):
        self.module_name = module_name
        self.executable_code = code


    def analyze(self):
        llm_output_graph = LLMAwareOutput()

        py_call_graph = ModuleCallGraph(self.module_name , output=llm_output_graph)
        py_call_graph.start()
        exec(self.executable_code)
        py_call_graph.stop()
        py_call_graph.done()

        groups, nodes, edges = llm_output_graph.done()
        print("Static analysis complete.")
        print(f"Groups: {len(groups)}, Nodes: {len(nodes)}, Edges: {len(edges)}")

        nnodes = regroup_nodes(nodes.values())
        main_node = [n for n in nnodes if n.id == '__main__'][0]

        return build_tree_string(main_node), groups, nodes, edges
