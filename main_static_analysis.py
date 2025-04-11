from static_analyzer.llm_graph import regroup_nodes, build_tree_string
from static_analyzer.pycallflow_patch.custom_pycall_graph_output import LLMAwareOutput
from static_analyzer.pycallflow_patch.module_call_graph import ModuleCallGraph


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
    # print_tree(nodes['__main__'])
    # for node in nodes.keys():
    #     print_adjacency_list(nodes[node].neighbours)
    nnodes = regroup_nodes(nodes.values())
    main_node = [n for n in nnodes if n.id == '__main__'][0]
    res = build_tree_string(main_node)
    print(res)


if __name__ == "__main__":
    collect_CFG()
