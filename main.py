from agent import AbstractionAgent, Component
from static_analyzer import Analyzer


def main():
    code = """from markitdown import MarkItDown

md = MarkItDown(enable_plugins=False) # Set to True to enable plugins
result = md.convert("./resources/test.xlsx")
"""
    stat_analyzer = Analyzer(module_name='markitdown', code=code)
    cfg_str, groups, nodes, edges = stat_analyzer.analyze()

    agent = AbstractionAgent("MarkItDown")
    abstract_modules = agent.get_interesting_modules(cfg_str)
    mermaid_str = "```mermaid\nflowchart LR\n"
    modules = abstract_modules['interesting_modules']
    for id in range(len(modules) - 1):
        # For now I will just do a naive diagram generation
        node, next_node = modules[id], modules[id + 1]
        node, next_node = Component.model_validate(node), Component.model_validate(next_node)
        descr = node.description.replace("(", "").replace(")", "")
        next_descr = next_node.description.replace("(", "").replace(")", "")
        mm_string = f"    {node.name}[<b>{node.name}</b><br><i>{descr}</i>] -- {node.communication} --> {next_node.name}[<b>{next_node.name}</b><br><i>{next_descr}</i>]\n"
        mermaid_str += mm_string
    mermaid_str += '```'
    with open("markitdown_diagram.md", "w") as f:
        f.write(mermaid_str)


if __name__ == "__main__":
    main()
