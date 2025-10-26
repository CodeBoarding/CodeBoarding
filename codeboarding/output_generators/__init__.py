import re


def sanitize(name: str) -> str:
    # Replace non-alphanumerics with underscores so IDs are valid Mermaid identifiers
    return re.sub(r'\W+', '_', name)
