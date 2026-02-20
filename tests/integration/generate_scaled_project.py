#!/usr/bin/env python3
"""Generate a scaled-up Python edge cases project.

Duplicates the base python_edge_cases_project N times with unique package
suffixes (core_00, core_01, ...), creates entry_XX.py files (one per copy
of main.py), and a mega main.py that imports all entries.
Also generates the corresponding fixture JSON.

Usage:
    uv run python tests/integration/generate_scaled_project.py
    uv run python tests/integration/generate_scaled_project.py --copies 50
"""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SOURCE_PROJECT = SCRIPT_DIR / "projects" / "python_edge_cases_project"
TARGET_PROJECT = SCRIPT_DIR / "projects" / "python_scaled_project"
FIXTURE_DIR = SCRIPT_DIR / "fixtures"
SOURCE_FIXTURE = FIXTURE_DIR / "python_edge_cases.json"

PACKAGES = ["core", "utils", "unused"]

PACKAGE_FILES = [
    "core/__init__.py",
    "core/base.py",
    "core/models.py",
    "core/services.py",
    "core/pipeline.py",
    "utils/__init__.py",
    "utils/helpers.py",
    "unused/__init__.py",
    "unused/dead_code.py",
]

_width = 2


def sfx(i: int) -> str:
    return f"_{i:0{_width}d}"


def rewrite_imports(content: str, i: int) -> str:
    """Replace package names in import statements with suffixed versions."""
    s = sfx(i)
    for pkg in PACKAGES:
        content = content.replace(f"from {pkg}.", f"from {pkg}{s}.")
        content = content.replace(f"import {pkg}.", f"import {pkg}{s}.")
    return content


def rewrite_pkg(pkg: str, i: int) -> str:
    """Rewrite a bare top-level package name for copy i."""
    if pkg == "main":
        return f"entry{sfx(i)}"
    if pkg in PACKAGES:
        return f"{pkg}{sfx(i)}"
    return pkg


def rewrite_name(name: str, i: int) -> str:
    """Rewrite a dotted reference/edge name for copy i."""
    parts = name.split(".", 1)
    head = rewrite_pkg(parts[0], i)
    return f"{head}.{parts[1]}" if len(parts) > 1 else head


def generate_project(n: int) -> int:
    """Generate the scaled project directory. Returns number of .py files created."""
    if TARGET_PROJECT.exists():
        shutil.rmtree(TARGET_PROJECT)
    TARGET_PROJECT.mkdir(parents=True)

    for i in range(n):
        s = sfx(i)
        # Copy package files with renamed directories
        for rel in PACKAGE_FILES:
            pkg, filename = rel.split("/", 1)
            dst = TARGET_PROJECT / f"{pkg}{s}" / filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            content = (SOURCE_PROJECT / rel).read_text()
            dst.write_text(rewrite_imports(content, i))

        # Copy main.py as entry_XX.py
        content = (SOURCE_PROJECT / "main.py").read_text()
        (TARGET_PROJECT / f"entry{s}.py").write_text(rewrite_imports(content, i))

    # Mega main.py
    lines = []
    for i in range(n):
        lines.append(f"from entry{sfx(i)} import main as main{sfx(i)}")
    lines += ["", "", "def main():"]
    for i in range(n):
        lines.append(f"    main{sfx(i)}()")
    lines += ["", "", 'if __name__ == "__main__":', "    main()", ""]
    (TARGET_PROJECT / "main.py").write_text("\n".join(lines))

    # git init + commit
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }
    subprocess.run(["git", "init"], cwd=TARGET_PROJECT, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=TARGET_PROJECT, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=TARGET_PROJECT, capture_output=True, check=True, env=env)

    file_count = sum(1 for _ in TARGET_PROJECT.rglob("*.py"))
    return file_count


def generate_fixture(n: int) -> dict:
    """Generate the fixture JSON for the scaled project. Returns summary stats."""
    original = json.loads(SOURCE_FIXTURE.read_text())
    fixture: dict = {"language": "Python"}

    # --- References ---
    refs = []
    for i in range(n):
        for r in original["sample_references"]:
            refs.append(rewrite_name(r, i))
    refs.append("main.main")
    fixture["sample_references"] = refs

    # --- Classes ---
    classes = []
    for i in range(n):
        for c in original["sample_classes"]:
            classes.append(rewrite_name(c, i))
    fixture["sample_classes"] = classes

    # --- Hierarchy ---
    hierarchy = {}
    for i in range(n):
        for cls_name, exp in original["sample_hierarchy"].items():
            new_exp = {}
            for key in ("superclasses_contain", "subclasses_contain"):
                if key in exp:
                    new_exp[key] = [rewrite_name(s, i) for s in exp[key]]
            hierarchy[rewrite_name(cls_name, i)] = new_exp
    fixture["sample_hierarchy"] = hierarchy

    # --- Edges ---
    edges = []
    for i in range(n):
        for src, dst in original["sample_edges"]:
            edges.append([rewrite_name(src, i), rewrite_name(dst, i)])
    for i in range(n):
        edges.append(["main.main", f"entry{sfx(i)}.main"])
    fixture["sample_edges"] = edges

    # --- Package deps ---
    deps: dict = {}
    for i in range(n):
        for pkg_name, exp in original["sample_package_deps"].items():
            new_pkg = rewrite_pkg(pkg_name, i)
            new_exp: dict = {}
            if "imports_contain" in exp:
                new_exp["imports_contain"] = [rewrite_pkg(p, i) for p in exp["imports_contain"]]
            if "imported_by_contain" in exp:
                new_exp["imported_by_contain"] = [rewrite_pkg(p, i) for p in exp["imported_by_contain"]]
            deps[new_pkg] = new_exp
    deps["main"] = {"imports_contain": [f"entry{sfx(i)}" for i in range(n)]}
    fixture["sample_package_deps"] = deps

    # --- Source files ---
    files = []
    for i in range(n):
        s = sfx(i)
        for f in original["expected_source_files_contain"]:
            if f == "main.py":
                files.append(f"entry{s}.py")
            else:
                pkg, rest = f.split("/", 1)
                files.append(f"{pkg}{s}/{rest}")
    files.append("main.py")
    fixture["expected_source_files_contain"] = sorted(files)

    output = FIXTURE_DIR / "python_scaled.json"
    output.write_text(json.dumps(fixture, indent=2) + "\n")

    return {
        "references": len(fixture["sample_references"]),
        "classes": len(fixture["sample_classes"]),
        "edges": len(fixture["sample_edges"]),
        "hierarchy": len(fixture["sample_hierarchy"]),
        "package_deps": len(fixture["sample_package_deps"]),
        "source_files": len(fixture["expected_source_files_contain"]),
    }


def main():
    global _width
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--copies", type=int, default=100, help="Number of copies (default: 100)")
    args = parser.parse_args()

    n = args.copies
    _width = max(2, len(str(n - 1)))

    print(f"Generating {n} copies...")
    file_count = generate_project(n)
    print(f"Project: {TARGET_PROJECT}")
    print(f"  Python files: {file_count}")

    stats = generate_fixture(n)
    print(f"Fixture: {FIXTURE_DIR / 'python_scaled.json'}")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print("Done.")


if __name__ == "__main__":
    main()
