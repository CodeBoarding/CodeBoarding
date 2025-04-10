from pycallgraph import PyCallGraph

from llm_graph_repr import print_tree, print_adjacency_list
from pycallflow_patch.custom_pycall_graph_output import LLMAwareOutput
from pycallflow_patch.module_call_graph import ModuleCallGraph
from resources.test_file_for_cfg import visualize_me


def run_program():
    """
    Here we run the code from the entry point for which we want to collect the CFG.
    It can be take fully from an LLM generation as it works with exec.
    """
    exec("""from markitdown import MarkItDown

md = MarkItDown(enable_plugins=False) # Set to True to enable plugins
result = md.convert("./resources/test.xlsx")
""")


def collect_CFG():
    llm_output_graph = LLMAwareOutput()

    py_call_graph = ModuleCallGraph('markitdown', output=llm_output_graph)
    py_call_graph.start()

    run_program()

    py_call_graph.stop()
    py_call_graph.done()

    groups, nodes, edges = llm_output_graph.done()
    print(f"Groups: {len(groups)}, Nodes: {len(nodes)}, Edges: {len(edges)}")
    print_tree(nodes['__main__'])
    for node in nodes.keys():
        print_adjacency_list(nodes[node].neighbours)


if __name__ == "__main__":
    collect_CFG()
