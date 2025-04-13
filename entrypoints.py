import os
import re
from utils import init_llm

class EntryPoint:

    def __init__(self, kind, filepath, line, code, args=None):
        self.kind = kind
        self.filepath = filepath
        self.line = line
        self.code = code
        self.args = args or {}

    def __repr__(self):
        args_repr = ', '.join(f'{k}={v}' for k, v in self.args.items()) if self.args else 'No args'
        return f"[{self.kind}] {self.filepath}:{self.line} → {self.code} ({args_repr})"


class EntryPointScanner:
    def __init__(self):
        self.entrypoint_patterns = {
            "main_block": re.compile(r'if\s+__name__\s*==\s*[\'\"]__main__[\'\"]'),
            "main_function": re.compile(r'^\s*def\s+main\s*\('),
            "flask_app": re.compile(r'Flask\s*\('),
            "flask_run": re.compile(r'\.run\s*\('),
            "typer_app": re.compile(r'Typer\s*\('),
            "argparse": re.compile(r'argparse\.ArgumentParser'),
            "bash_python_call": re.compile(r'python[0-9.]*\s+[\w./_-]+\.py'),
            "shebang_python": re.compile(r'^#!.*python'),
            "fire_cli": re.compile(r'fire\.Fire\s*\('),
            "sys_args": re.compile(r'sys\.argv'),
            "cli_class": re.compile(r'\b[A-Za-z0-9_]+CLI\s*\(')
        }
        self.cli_keys = {"main_function", "click_decorator", "typer_app", "fire_cli", "sys_args", "cli_class"}
        self.llm = init_llm()
        self.ignored_dirs = {'.git', '__pycache__', 'venv', '.venv', 'node_modules', '.mypy_cache'}

    def is_text_file(self, filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                f.read(1024)
            return True
        except:
            return False

    def scan_file(self, filepath):
        hits = []
        if not self.is_text_file(filepath):
            return hits

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            for key, pattern in self.entrypoint_patterns.items():
                if pattern.search(line):
                    hits.append((key, filepath, i + 1, line.strip()))
        return hits

    def extract_function_body(self, filepath, function_name="main"):
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        func_code, func_found, base_indent = [], False, None
        pattern = re.compile(r'^(\s*)def\s+' + re.escape(function_name) + r'\s*\(')

        for line in lines:
            if (match := pattern.match(line)):
                func_found, base_indent = True, len(match.group(1))
                func_code.append(line)
                continue

            if func_found:
                current_indent = len(line) - len(line.lstrip())
                if line.strip() == "" or current_indent > base_indent:
                    func_code.append(line)
                else:
                    break

        return "".join(func_code) if func_found else ""

    def analyze_branches_in_function(self, func_code, filepath):
        prompt = (
    "Analyze the following Python function that handles command-line arguments. "
    "Identify each distinct execution branch that is selected based on specific argument values. "
    "For each such branch, return the line number of the function call that begins that branch, "
    "along with the argument condition that triggers it. Format the output as: "
    "line_number | args.argument=value"
)

        response = self.llm.invoke(prompt + func_code)
        response_text = response.content

        entrypoints = []
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            file_lines = f.readlines()

        for line in response_text.strip().split('\n'):
            line = line.strip()
            if '|' in line:
                func_name, args_str = line.split('|', 1)
                args_dict = dict(arg.split('=') for arg in args_str.split(',') if '=' in arg)
                for idx, file_line in enumerate(file_lines, 1):
                    if re.search(rf'\b{re.escape(func_name.strip())}\s*\(', file_line):
                        entrypoints.append(EntryPoint("cli_branch", filepath, idx, file_line.strip(), args_dict))
                        break

        return entrypoints

    def find_entrypoints(self, root='.'):
        current_file = os.path.abspath(__file__)
        results = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.ignored_dirs]
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                if os.path.abspath(filepath) == current_file:
                    continue

                if filename.endswith(('.py', '.sh', 'Dockerfile')) or filename.startswith(('run', 'start')):
                    file_hits = self.scan_file(filepath)

                    cli_branches = []
                    if filename.endswith('.py') and any(hit[0] in self.cli_keys for hit in file_hits):
                        func_code = self.extract_function_body(filepath)
                        if func_code:
                            cli_branches = self.analyze_branches_in_function(func_code, filepath)

                    if cli_branches:
                        results.extend(cli_branches)
                    else:
                        results.extend([EntryPoint(*hit) for hit in file_hits if hit[0] != "argparse"])

        return results

def main():
    scanner = EntryPointScanner()
    print("🔍 Scanning for possible Python entrypoints and branches...\n")
    entries = scanner.find_entrypoints()
    if entries:
        for entry in entries:
            print(entry)
    else:
        print("No entrypoints found.")

if __name__ == "__main__":
    main()
