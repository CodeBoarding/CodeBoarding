import os
import re
from utils import init_llm

class EntryPointScanner:
    def __init__(self):
        self.entrypoint_patterns = {
            "main_block":      re.compile(r'if\s+__name__\s*==\s*[\'"]__main__[\'"]'),
            "main_function":   re.compile(r'^(\s*)def\s+main\s*\('),
            "flask_app":       re.compile(r'Flask\s*\('),
            "flask_run":       re.compile(r'\.run\s*\('),
            "django_manage":   re.compile(r'execute_from_command_line'),
            "click_decorator": re.compile(r'@click\.command'),
            "typer_app":       re.compile(r'Typer\s*\('),
            "argparse":        re.compile(r'argparse\.ArgumentParser'),
            "bash_python_call":re.compile(r'python[0-9.]*\s+[\w./_-]+\.py'),
            "shebang_python":  re.compile(r'^#!.*python'),
            "fire_cli":        re.compile(r'fire\.Fire\s*\('),
            "sys_args":        re.compile(r'sys\.argv'),
            "cli_class":       re.compile(r'\b[A-Za-z0-9_]+CLI\s*\(')
        }
        self.cli_keys = {"main_function", "argparse", "click_decorator", "typer_app", "fire_cli", "sys_args", "cli_class"}
        self.llm = init_llm()
        self.ignored_dirs = {'.git', '__pycache__', 'venv', '.venv', 'node_modules', '.mypy_cache'}


    def is_text_file(self, filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                f.read(1024)
            return True
        except Exception:
            return False

    def scan_file(self, filepath):
        """Scans a file line-by-line for any matching entrypoint patterns."""
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
        """
        Extract the source code of a function defined with `def <function_name>( ... ):`
        using standard Python indentation. Returns the function source as a string.
        """
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        func_code = []
        func_found = False
        # Pattern to locate the candidate function definition.
        pattern = re.compile(r'^(\s*)def\s+' + re.escape(function_name) + r'\s*\(')
        base_indent = None

        for line in lines:
            m = pattern.match(line)
            if m:
                func_found = True
                base_indent = len(m.group(1))
                func_code.append(line)
                continue

            if func_found:
                # Include blank lines.
                if line.strip() == "":
                    func_code.append(line)
                else:
                    current_indent = len(line) - len(line.lstrip())
                    if current_indent > base_indent:
                        func_code.append(line)
                    else:
                        # End of the function block.
                        break

        return "".join(func_code) if func_found else ""

    def analyze_function_for_cli_entrypoints(self, func_code, filepath):
        """
        Given the source code of a candidate function, call the LLM with a prompt that
        asks for the relevant entrypoints (ignoring helper functions) intended for CLI usage.
        """
        prompt = (
            "Given the following Python function that appears to handle command-line arguments, "
            "identify and list the relevant entrypoint functions intended for CLI usage. "
            "Exclude any internal or helper functions. Provide only the function names "
            "and a brief explanation.\n\n"
        )
        full_prompt = prompt + func_code

        # Call the LLM using a callable interface (adjust this if your LLM uses a different method)
        response = self.llm.invoke(full_prompt)
        print(f"\nLLM Analysis for function in {filepath}:\n{response}\n")

    def find_entrypoints(self, root='.'):
        results = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip ignored directories.
            dirnames[:] = [d for d in dirnames if d not in self.ignored_dirs]
            for filename in filenames:
                # Process candidate files.
                if filename.endswith(('.py', '.sh', 'Dockerfile')) or filename.startswith(('run', 'start')):
                    filepath = os.path.join(dirpath, filename)
                    file_hits = self.scan_file(filepath)
                    results.extend(file_hits)

                    # For Python files, attempt to extract and analyze the candidate entrypoint function.
                    if filename.endswith('.py'):
                        if any(hit[0] in self.cli_keys for hit in file_hits):
                            func_code = self.extract_function_body(filepath, "main")
                            cli_indicators = (
                                "argparse.ArgumentParser",
                                "sys.argv",
                                "fire.Fire",
                                "Typer(",
                                "CLI(",
                            )
                            if func_code and any(ind in func_code for ind in cli_indicators):
                                self.analyze_function_for_cli_entrypoints(func_code, filepath)
        return results

def main():
    scanner = EntryPointScanner()
    print("🔍 Scanning for possible Python entrypoints...\n")
    entries = scanner.find_entrypoints()
    if not entries:
        print("No entrypoints found.")
        return
    for kind, filepath, line, code in entries:
        print(f"[{kind:<20}] {filepath}:{line} → {code}")

if __name__ == "__main__":
    main()