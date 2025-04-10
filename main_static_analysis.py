from pycallgraph import PyCallGraph
from second_test_file import Calculator
from pycallgraph.output import GraphvizOutput
from llm_graph_repr import print_CFG
from custom_pycall_graph_output import LLMAwareOutput

from test_file_for_cfg import visualize_me


def run_program():
    """
    Here we run the code from the entry point for which we want to collect the CFG.
    It can be take fully from an LLM generation as it works with exec.
    """
    exec("""from markitdown import MarkItDown

md = MarkItDown(enable_plugins=False) # Set to True to enable plugins
result = md.convert("./resources/test.xlsx")
print(result.text_content)
""")

def collect_CFG():
    llm_output_graph = LLMAwareOutput()

    py_call_graph = PyCallGraph(output=llm_output_graph)
    py_call_graph.start()

    run_program()

    py_call_graph.stop()
    py_call_graph.done()

    groups, nodes, edges = llm_output_graph.done()
    print(f"Groups: {len(groups)}, Nodes: {len(nodes)}, Edges: {len(edges)}")

    # For MarkItDown it is too big and hits recursion depth exception
    # print_CFG(llm_output_graph.nodes['__main__'])


if __name__ == "__main__":
    collect_CFG()
    