from py2cfg import CFGBuilder
from pycallgraph import PyCallGraph

from test_file_for_cfg import visualize_me


def main():
    cfg = CFGBuilder().build_from_file('langchain_example', '/home/ivan/StartUp/CodeBoarding/repos/ToolFuzz/langchain_example.py')
    print(cfg)
    cfg.build_visual('ex_cfg', 'pdf', calls=True, interactive=True)


def pycallgraph():

    graphviz = GraphvizOutput(output_file='filter_none.png')

    pygraph = PyCallGraph(output=graphviz)
    pygraph.start()
    visualize_me()
    pygraph.stop()
    # pygraph.done()
    print(pygraph.output)
    import pdb; pdb.set_trace()


if __name__ == "__main__":
    pycallgraph()
