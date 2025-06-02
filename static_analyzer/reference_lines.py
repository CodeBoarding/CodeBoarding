import ast


def find_fqn_location(source_code: str, fqn: str):
    """
    Finds the start and end line numbers of a class/function/method given its fully qualified name.
    Args:
        source_code (str): Python source code as a string.
        fqn (str): Fully qualified name (colon or dot separated)

    Returns:
        tuple: (start_line, end_line) or None if not found.
    """
    # Normalize: convert "lib.setting.ClassFile:method" → "ClassFile.method"
    if ':' in fqn:
        fqn = fqn.split(':')[-2] + '.' + fqn.split(':')[-1] if '.' in fqn.split(':')[0] else fqn.replace(':', '.')

    path = fqn.split('.')
    tree = ast.parse(source_code)
    def find_node(nodes, path):
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name != path[0]:
                    continue
                if len(path) == 1:
                    return node
                elif hasattr(node, 'body'):
                    return find_node(node.body, path[1:])
        return None

    node = find_node(tree.body, path)

    if node:
        if hasattr(node, 'end_lineno'):  # Python 3.8+
            return node.lineno, node.end_lineno
        else:
            # Approximate end_lineno for older Python versions
            class LineCounter(ast.NodeVisitor):
                def __init__(self):
                    self.max_lineno = node.lineno

                def visit(self, sub):
                    if hasattr(sub, 'lineno'):
                        self.max_lineno = max(self.max_lineno, sub.lineno)
                    self.generic_visit(sub)

            counter = LineCounter()
            counter.visit(node)
            return node.lineno, counter.max_lineno

    return None
