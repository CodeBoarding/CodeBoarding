import os
import re


class EntryPointScanner:
    def __init__(self):
        self.entrypoint_patterns = {
            "main_block": re.compile(r'if\s+__name__\s*==\s*[\'"]__main__[\'"]'),
            "main_function": re.compile(r'def\s+main\s*\('),
            "flask_app": re.compile(r'Flask\s*\('),
            "flask_run": re.compile(r'\.run\s*\('),
            "django_manage": re.compile(r'execute_from_command_line'),
            "click_decorator": re.compile(r'@click\.command'),
            "typer_app": re.compile(r'Typer\s*\('),
            "argparse": re.compile(r'argparse\.ArgumentParser'),
            "bash_python_call": re.compile(r'python[0-9.]*\s+[\w./_-]+\.py'),
            "shebang_python": re.compile(r'^#!.*python')
        }

        self.ignored_dirs = {
            '.git', '__pycache__', 'venv', '.venv', 'node_modules', '.mypy_cache'
        }

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

    def find_entrypoints(self, root='.'):
        results = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.ignored_dirs]
            for filename in filenames:
                if filename.endswith(('.py', '.sh', 'Dockerfile')) or filename.startswith(('run', 'start')):
                    filepath = os.path.join(dirpath, filename)
                    results.extend(self.scan_file(filepath))
        return results


def main():
    scanner = EntryPointScanner()
    print("\U0001F50D Scanning for possible Python entrypoints...\n")
    entries = scanner.find_entrypoints()
    if not entries:
        print("No entrypoints found.")
        return

    for kind, file, line, code in entries:
        print(f"[{kind:<20}] {file}:{line} → {code}")


if __name__ == "__main__":
    main()
