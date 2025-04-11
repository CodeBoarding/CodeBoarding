from agent import AbstractionAgent
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
    print(agent.get_interesting_modules(cfg_str, nodes))


if __name__ == "__main__":
    main()
