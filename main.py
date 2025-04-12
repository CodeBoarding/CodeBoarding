from agent import AbstractionAgent, Component
from static_analyzer import Analyzer
import os

def main():
    os.environ["GOOGLE_API_KEY"] = "AIzaSyCp6jlH3m0GunL3NrFHb0l7PxsioPKD4aY"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGCHAIN_API_KEY"] = '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'

    code = """from markitdown import MarkItDown

md = MarkItDown(enable_plugins=False) # Set to True to enable plugins
result = md.convert("./resources/test.xlsx")
"""
    stat_analyzer = Analyzer(module_name='markitdown', code=code)
    cfg_str, groups, nodes, edges = stat_analyzer.analyze()

    agent = AbstractionAgent("MarkItDown")
    abstract_modules = agent.get_interesting_modules(cfg_str, nodes)
    mermaid_str = "flowchart LR\n"
    modules = abstract_modules['interesting_modules']
    for id in range(len(modules) - 1):
        # For now I will just do a naive diagram generation
        node, next_node  = modules[id], modules[id + 1]
        node, next_node = Component.model_validate(node), Component.model_validate(next_node)
        descr = node.description.replace("(", "").replace(")", "")
        next_descr = next_node.description.replace("(", "").replace(")", "")
        mm_string = f"    {node.name}[<b>{node.name}</b><br><i>{descr}</i>] -- {node.communication} --> {next_node.name}[<b>{next_node.name}</b><br><i>{next_descr}</i>]\n"
        mermaid_str += mm_string
    with open("markitdown_diagram.md", "w") as f:
        f.write(mermaid_str)

if __name__ == "__main__":
    main()
