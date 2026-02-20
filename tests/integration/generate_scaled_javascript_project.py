#!/usr/bin/env python3
"""Generate a scaled-up JavaScript edge cases project.

Duplicates the base javascript_edge_cases_project N times with unique directory
suffixes (models_00, models_01, ...), creates entry_XX.js files (one per copy
of src/index.js), and a mega src/index.js that imports all entries.
Also generates the corresponding fixture JSON.

Usage:
    uv run python tests/integration/generate_scaled_javascript_project.py
    uv run python tests/integration/generate_scaled_javascript_project.py --copies 50
"""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SOURCE_PROJECT = SCRIPT_DIR / "projects" / "javascript_edge_cases_project"
TARGET_PROJECT = SCRIPT_DIR / "projects" / "javascript_scaled_project"
FIXTURE_DIR = SCRIPT_DIR / "fixtures"
SOURCE_FIXTURE = FIXTURE_DIR / "javascript_edge_cases.json"

# Subdirectories under src/ that get duplicated with a suffix
PACKAGES = ["models", "services", "utils", "unused"]

# Files within each package (relative to src/)
PACKAGE_FILES = [
    "models/index.js",
    "models/base.js",
    "models/entities.js",
    "services/index.js",
    "services/builder.js",
    "services/processor.js",
    "utils/index.js",
    "utils/constants.js",
    "utils/helpers.js",
    "unused/orphan.js",
]

_width = 2


def sfx(i: int) -> str:
    return f"_{i:0{_width}d}"


def rewrite_imports(content: str, i: int) -> str:
    """Replace cross-directory import paths with suffixed versions.

    Within-directory imports (e.g., './base.js', './helpers.js') are left unchanged.
    Cross-directory imports (e.g., '../utils/helpers.js', './models/index.js') get the suffix.
    """
    s = sfx(i)
    for pkg in PACKAGES:
        # "../models/" → "../models_00/"  (from services/ referencing siblings)
        content = content.replace(f'"../{pkg}/', f'"../{pkg}{s}/')
        # "./models/" → "./models_00/"  (from src/index.js referencing subdirs)
        content = content.replace(f'"./{pkg}/', f'"./{pkg}{s}/')
    return content


def rewrite_js_name(name: str, i: int) -> str:
    """Rewrite a dotted reference/edge/class name for copy i.

    src.index.main       → src.entry_00.main
    src.models.base.Entity → src.models_00.base.Entity
    src.utils.helpers.add  → src.utils_00.helpers.add
    """
    s = sfx(i)

    # src.index.xxx → src.entry_XX.xxx
    if name.startswith("src.index."):
        return f"src.entry{s}.{name[len('src.index.'):]}"
    if name == "src.index":
        return f"src.entry{s}"

    # src.PACKAGE.xxx → src.PACKAGE_XX.xxx
    for pkg in PACKAGES:
        prefix = f"src.{pkg}."
        if name.startswith(prefix):
            return f"src.{pkg}{s}.{name[len(prefix):]}"
        if name == f"src.{pkg}":
            return f"src.{pkg}{s}"

    return name


def rewrite_js_pkg(pkg: str, i: int) -> str:
    """Rewrite a package name for copy i."""
    s = sfx(i)

    if pkg == "src":
        # Root src package stays as-is (the mega index.js lives here)
        return "src"

    # src.models → src.models_00
    for p in PACKAGES:
        if pkg == f"src.{p}":
            return f"src.{p}{s}"

    return pkg


def rewrite_js_file(filepath: str, i: int) -> str:
    """Rewrite a source file path for copy i.

    src/index.js       → src/entry_00.js
    src/models/base.js → src/models_00/base.js
    """
    s = sfx(i)

    if filepath == "src/index.js":
        return f"src/entry{s}.js"

    for pkg in PACKAGES:
        prefix = f"src/{pkg}/"
        if filepath.startswith(prefix):
            return f"src/{pkg}{s}/{filepath[len(prefix):]}"

    return filepath


def generate_project(n: int) -> int:
    """Generate the scaled project directory. Returns number of .js files created."""
    if TARGET_PROJECT.exists():
        shutil.rmtree(TARGET_PROJECT)
    TARGET_PROJECT.mkdir(parents=True)

    src_dir = TARGET_PROJECT / "src"
    src_dir.mkdir()

    for i in range(n):
        s = sfx(i)
        # Copy package files with renamed directories
        for rel in PACKAGE_FILES:
            pkg_dir, filename = rel.split("/", 1)
            dst = src_dir / f"{pkg_dir}{s}" / filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            content = (SOURCE_PROJECT / "src" / rel).read_text()
            dst.write_text(rewrite_imports(content, i))

        # Copy src/index.js as src/entry_XX.js (remove trailing main() call)
        content = (SOURCE_PROJECT / "src" / "index.js").read_text()
        content = rewrite_imports(content, i)
        # Remove the standalone main() invocation at module level
        # (the mega index.js will call it instead)
        content = content.replace("\nmain();\n", "\n")
        (src_dir / f"entry{s}.js").write_text(content)

    # Mega src/index.js
    lines = []
    for i in range(n):
        lines.append(f'import {{ main as main{sfx(i)} }} from "./entry{sfx(i)}.js";')
    lines += ["", "export function main() {"]
    for i in range(n):
        lines.append(f"    main{sfx(i)}();")
    lines += ["}", "", "main();", ""]
    (src_dir / "index.js").write_text("\n".join(lines))

    # Copy jsconfig.json and package.json
    shutil.copy2(SOURCE_PROJECT / "jsconfig.json", TARGET_PROJECT / "jsconfig.json")
    shutil.copy2(SOURCE_PROJECT / "package.json", TARGET_PROJECT / "package.json")

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

    file_count = sum(1 for _ in (TARGET_PROJECT / "src").rglob("*.js"))
    return file_count


def generate_fixture(n: int) -> dict:
    """Generate the fixture JSON for the scaled project. Returns summary stats."""
    original = json.loads(SOURCE_FIXTURE.read_text())
    fixture: dict = {"language": "JavaScript"}

    # --- References ---
    refs = []
    for i in range(n):
        for r in original["sample_references"]:
            refs.append(rewrite_js_name(r, i))
    # Add the mega main
    refs.append("src.index.main")
    fixture["sample_references"] = refs

    # --- Classes ---
    classes = []
    for i in range(n):
        for c in original["sample_classes"]:
            classes.append(rewrite_js_name(c, i))
    fixture["sample_classes"] = classes

    # --- Hierarchy ---
    hierarchy = {}
    for i in range(n):
        for cls_name, exp in original.get("sample_hierarchy", {}).items():
            new_exp = {}
            for key in ("superclasses_contain", "subclasses_contain"):
                if key in exp:
                    new_exp[key] = [rewrite_js_name(s, i) for s in exp[key]]
            hierarchy[rewrite_js_name(cls_name, i)] = new_exp
    fixture["sample_hierarchy"] = hierarchy

    # --- Edges ---
    edges = []
    for i in range(n):
        for src, dst in original["sample_edges"]:
            edges.append([rewrite_js_name(src, i), rewrite_js_name(dst, i)])
    # Mega main → each entry's main
    for i in range(n):
        edges.append(["src.index.main", f"src.entry{sfx(i)}.main"])
    fixture["sample_edges"] = edges

    # --- Package deps ---
    deps: dict = {}
    for i in range(n):
        for pkg_name, exp in original.get("sample_package_deps", {}).items():
            new_pkg = rewrite_js_pkg(pkg_name, i)
            if new_pkg == "src":
                # Skip — we handle the mega src package separately below
                continue
            new_exp: dict = {}
            if "imports_contain" in exp:
                new_exp["imports_contain"] = [rewrite_js_pkg(p, i) for p in exp["imports_contain"]]
            if "imported_by_contain" in exp:
                new_exp["imported_by_contain"] = [rewrite_js_pkg(p, i) for p in exp["imported_by_contain"]]
            deps[new_pkg] = new_exp
    # Mega src package
    deps["src"] = {}
    fixture["sample_package_deps"] = deps

    # --- Source files ---
    files = []
    for i in range(n):
        for f in original["expected_source_files_contain"]:
            files.append(rewrite_js_file(f, i))
    # Mega index.js
    files.append("src/index.js")
    fixture["expected_source_files_contain"] = sorted(files)

    output = FIXTURE_DIR / "javascript_scaled.json"
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
    print(f"  JavaScript files: {file_count}")

    stats = generate_fixture(n)
    print(f"Fixture: {FIXTURE_DIR / 'javascript_scaled.json'}")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print("Done.")


if __name__ == "__main__":
    main()
