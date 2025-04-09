from py2cfg import CFGBuilder
from pycallgraph import PyCallGraph
from pycallgraph.output import GraphvizOutput


def main():
    cfg = CFGBuilder().build_from_file('langchain_example', '/home/ivan/StartUp/CodeBoarding/repos/ToolFuzz/langchain_example.py')
    print(cfg)
    cfg.build_visual('ex_cfg', 'pdf', calls=True, interactive=True)


def pycallgraph():
    from banana import Banana

    graphviz = GraphvizOutput(output_file='filter_none.png')

    with PyCallGraph(output=graphviz):
        banana = Banana()
        banana.eat()

if __name__ == "__main__":
    main()
