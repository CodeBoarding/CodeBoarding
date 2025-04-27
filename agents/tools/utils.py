import re

def read_dot_file(file_path: str) -> str:
    """
    Read a dot file and return its content as a string.
    """
    with open(file_path, 'r') as file:
        content = file.read()

    cleaned_lines = []
    for line in content.splitlines():
        # Remove anything inside brackets [] including the brackets
        cleaned_line = re.sub(r'\[.*?\]', '', line)
        cleaned_lines.append(cleaned_line)
    return '\n'.join(cleaned_lines)