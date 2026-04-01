"""Benchmark: measure is_file_cosmetic timing on real deepface edits."""

import time
import subprocess
from pathlib import Path

import git

from diagram_analysis.semantic_diff import (
    is_file_cosmetic,
    _get_parser,
    _to_structural_tuple,
    _to_normalized_tuple,
    _LANG_CONFIGS,
    EXTENSION_TO_LANGUAGE,
)
from static_analyzer.constants import Language

DEEPFACE = Path("/Users/brovatten/Documents/github_folder/deepface")

# ---------------------------------------------------------------------------
# Edits to apply
# ---------------------------------------------------------------------------

EDITS: dict[str, dict] = {
    # 1. Cosmetic: add/change comments + whitespace in logger.py
    "deepface/commons/logger.py": {
        "type": "cosmetic",
        "original": None,  # filled at runtime
        "modified": None,
    },
    # 2. Rename: rename local variables in image_utils.py
    "deepface/commons/image_utils.py": {
        "type": "rename",
        "original": None,
        "modified": None,
    },
    # 3. Semantic: change a value in confidence.py
    "deepface/config/confidence.py": {
        "type": "semantic",
        "original": None,
        "modified": None,
    },
}


def save_originals():
    for fp in EDITS:
        EDITS[fp]["original"] = (DEEPFACE / fp).read_text()


def prepare_cosmetic_edit():
    """Add comments and blank lines to logger.py."""
    original = EDITS["deepface/commons/logger.py"]["original"]
    modified = original.replace(
        "import os\nfrom typing import Any\nimport logging",
        "# Standard library imports\nimport os\nfrom typing import Any\nimport logging\n",
    ).replace(
        "class Logger:",
        "# Main logger class\n\nclass Logger:",
    )
    EDITS["deepface/commons/logger.py"]["modified"] = modified


def prepare_rename_edit():
    """Rename local variables in image_utils.py (first function only)."""
    original = EDITS["deepface/commons/image_utils.py"]["original"]
    # Target only the list_images function (has `images = []` before the loop)
    old_block = (
        "    images = []\n"
        "    for r, _, f in os.walk(path):\n"
        "        for file in f:\n"
        "            if os.path.splitext(file)[1].lower() in IMAGE_EXTS:\n"
        "                exact_path = os.path.join(r, file)\n"
    )
    new_block = (
        "    images = []\n"
        "    for root_dir, _, filenames in os.walk(path):\n"
        "        for file in filenames:\n"
        "            if os.path.splitext(file)[1].lower() in IMAGE_EXTS:\n"
        "                exact_path = os.path.join(root_dir, file)\n"
    )
    modified = original.replace(old_block, new_block, 1)
    EDITS["deepface/commons/image_utils.py"]["modified"] = modified


def prepare_semantic_edit():
    """Change a confidence weight value."""
    original = EDITS["deepface/config/confidence.py"]["original"]
    modified = original.replace(
        '"w": -6.61254026479904,',
        '"w": -7.12345678901234,',
    )
    EDITS["deepface/config/confidence.py"]["modified"] = modified


def apply_edits():
    for fp, data in EDITS.items():
        (DEEPFACE / fp).write_text(data["modified"])


def restore_originals():
    for fp, data in EDITS.items():
        (DEEPFACE / fp).write_text(data["original"])


def benchmark_git_diff(repo: git.Repo, base_ref: str, files: list[str]) -> float:
    """Measure time for git diff subprocess calls (current approach: one per file)."""
    start = time.perf_counter()
    for fp in files:
        subprocess.run(
            ["git", "diff", "-U3", base_ref, "--", fp],
            cwd=DEEPFACE,
            capture_output=True,
            text=True,
        )
    return time.perf_counter() - start


def benchmark_semantic_diff(base_ref: str, files: list[str]) -> dict[str, dict]:
    """Measure is_file_cosmetic per file and total."""
    results = {}
    total_start = time.perf_counter()
    for fp in files:
        start = time.perf_counter()
        is_cosmetic = is_file_cosmetic(DEEPFACE, base_ref, fp)
        elapsed = time.perf_counter() - start
        results[fp] = {"cosmetic": is_cosmetic, "time_ms": elapsed * 1000}
    results["__total__"] = {"time_ms": (time.perf_counter() - total_start) * 1000}
    return results


def main():
    repo = git.Repo(DEEPFACE)
    assert not repo.is_dirty(), "deepface repo has uncommitted changes — please commit or stash first"

    base_ref = repo.head.commit.hexsha
    print(f"Base ref: {base_ref[:10]}")
    print()

    # Save originals and prepare edits
    save_originals()
    prepare_cosmetic_edit()
    prepare_rename_edit()
    prepare_semantic_edit()

    try:
        # Apply edits
        apply_edits()
        files = list(EDITS.keys())

        # Warm up tree-sitter parsers (exclude from timing)
        for fp in files:
            ext = Path(fp).suffix
            lang = EXTENSION_TO_LANGUAGE.get(ext)
            if lang:
                _get_parser(lang, ext)

        print("=" * 70)
        print("BENCHMARK: Semantic Diff on deepface repo")
        print("=" * 70)
        print()

        # Run multiple iterations for stable timing
        N = 10
        git_times = []
        sd_times = []

        for i in range(N):
            gt = benchmark_git_diff(repo, base_ref, files)
            git_times.append(gt)

            sd = benchmark_semantic_diff(base_ref, files)
            sd_times.append(sd["__total__"]["time_ms"])

        # Print per-file results from last run
        sd = benchmark_semantic_diff(base_ref, files)
        print(f"{'File':<45} {'Type':<12} {'Cosmetic?':<10} {'Time (ms)':<10}")
        print("-" * 77)
        for fp in files:
            edit_type = EDITS[fp]["type"]
            r = sd[fp]
            print(f"{fp:<45} {edit_type:<12} {str(r['cosmetic']):<10} {r['time_ms']:.2f}")
        print("-" * 77)
        print()

        avg_git = sum(git_times) / N * 1000
        avg_sd = sum(sd_times) / N
        print(f"Average over {N} runs:")
        print(f"  git diff (3 files, subprocess per file):  {avg_git:.2f} ms")
        print(f"  semantic diff (3 files, tree-sitter):     {avg_sd:.2f} ms")
        print()

        # What the LLM tracer would see
        cosmetic_files = [fp for fp in files if sd[fp]["cosmetic"]]
        semantic_files = [fp for fp in files if not sd[fp]["cosmetic"]]
        print(f"Files skipped (cosmetic): {len(cosmetic_files)}")
        for fp in cosmetic_files:
            print(f"  - {fp} ({EDITS[fp]['type']})")
        print(f"Files sent to LLM:        {len(semantic_files)}")
        for fp in semantic_files:
            print(f"  - {fp} ({EDITS[fp]['type']})")
        print()
        print(f"LLM calls avoided: {len(cosmetic_files)}/{len(files)} files")
        if cosmetic_files:
            est_savings = len(cosmetic_files) * 4  # ~4s per file of LLM time
            print(f"Estimated LLM time saved: ~{est_savings}s (at ~4s per file)")

    finally:
        restore_originals()
        print()
        print("(originals restored)")


if __name__ == "__main__":
    main()
