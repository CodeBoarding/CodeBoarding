#!/usr/bin/env python3
"""
Setup script for real repository integration test.

This script helps identify suitable commit hashes and expected values
for the real repository integration test.
"""

import subprocess
import sys
from pathlib import Path


def get_recent_commits(repo_path: Path, count: int = 10) -> list[tuple[str, str]]:
    """Get recent commits with hash and message."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{count}"], cwd=repo_path, capture_output=True, text=True, check=True
        )

        commits = []
        for line in result.stdout.strip().split("\n"):
            if line:
                parts = line.split(" ", 1)
                commit_hash = parts[0]
                message = parts[1] if len(parts) > 1 else ""
                commits.append((commit_hash, message))

        return commits
    except subprocess.CalledProcessError as e:
        print(f"Error getting commits: {e}")
        return []


def get_changed_files_between_commits(repo_path: Path, commit1: str, commit2: str) -> list[str]:
    """Get files changed between two commits."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", commit1, commit2], cwd=repo_path, capture_output=True, text=True, check=True
        )

        return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    except subprocess.CalledProcessError as e:
        print(f"Error getting changed files: {e}")
        return []


def count_python_files(repo_path: Path) -> int:
    """Count Python files in repository."""
    return len(list(repo_path.glob("**/*.py")))


def analyze_commit_for_testing(repo_path: Path, commit_hash: str) -> dict:
    """Analyze a commit to get statistics for testing."""
    try:
        # Checkout the commit
        subprocess.run(["git", "checkout", commit_hash], cwd=repo_path, check=True, capture_output=True)

        # Count files and basic statistics
        python_files = list(repo_path.glob("**/*.py"))
        total_lines = 0
        total_functions = 0
        total_classes = 0

        for py_file in python_files:
            try:
                content = py_file.read_text(encoding="utf-8")
                lines = content.split("\n")
                total_lines += len(lines)

                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("def "):
                        total_functions += 1
                    elif stripped.startswith("class "):
                        total_classes += 1
            except Exception:
                continue  # Skip files that can't be read

        return {
            "commit": commit_hash,
            "python_files": len(python_files),
            "total_lines": total_lines,
            "functions": total_functions,
            "classes": total_classes,
        }
    except subprocess.CalledProcessError as e:
        print(f"Error analyzing commit {commit_hash}: {e}")
        return {}


def main():
    """Main function to help setup the real repository test."""
    repo_path = Path.cwd()

    print("=== CodeBoarding Repository Analysis for Integration Test ===")
    print(f"Repository path: {repo_path}")

    # Verify we're in a git repository
    if not (repo_path / ".git").exists():
        print("❌ Not in a git repository!")
        sys.exit(1)

    print("✅ Git repository detected")

    # Get recent commits
    print("\n--- Recent Commits ---")
    commits = get_recent_commits(repo_path, 20)

    if not commits:
        print("❌ Could not get commit history")
        sys.exit(1)

    for i, (commit_hash, message) in enumerate(commits):
        print(f"{i+1:2d}. {commit_hash} - {message}")

    # Analyze a few commits to suggest good candidates
    print("\n--- Analyzing Commits for Testing ---")

    # Analyze the most recent 5 commits
    commit_analyses = []
    for i in range(min(5, len(commits))):
        commit_hash = commits[i][0]
        print(f"Analyzing commit {commit_hash}...")
        analysis = analyze_commit_for_testing(repo_path, commit_hash)
        if analysis:
            commit_analyses.append(analysis)

    # Return to the latest commit
    if commits:
        subprocess.run(["git", "checkout", commits[0][0]], cwd=repo_path, check=True, capture_output=True)

    # Display analysis results
    print("\n--- Commit Analysis Results ---")
    for analysis in commit_analyses:
        print(f"Commit {analysis['commit']}:")
        print(f"  - Python files: {analysis['python_files']}")
        print(f"  - Total lines: {analysis['total_lines']}")
        print(f"  - Functions: {analysis['functions']}")
        print(f"  - Classes: {analysis['classes']}")
        print()

    # Suggest commit pairs
    print("--- Suggested Commit Pairs for Testing ---")

    if len(commit_analyses) >= 2:
        # Suggest pairs with meaningful differences
        for i in range(len(commit_analyses) - 1):
            older = commit_analyses[i + 1]
            newer = commit_analyses[i]

            file_diff = newer["python_files"] - older["python_files"]
            func_diff = newer["functions"] - older["functions"]
            class_diff = newer["classes"] - older["classes"]

            if abs(file_diff) > 0 or abs(func_diff) > 5 or abs(class_diff) > 0:
                print(f"Pair {i+1}: {older['commit']} → {newer['commit']}")
                print(f"  - File change: {file_diff:+d}")
                print(f"  - Function change: {func_diff:+d}")
                print(f"  - Class change: {class_diff:+d}")

                # Get changed files
                changed_files = get_changed_files_between_commits(repo_path, older["commit"], newer["commit"])
                print(f"  - Changed files: {len(changed_files)}")
                if len(changed_files) <= 5:
                    for file in changed_files:
                        print(f"    * {file}")
                print()

    # Generate test configuration
    print("--- Test Configuration ---")

    if len(commit_analyses) >= 2:
        older = commit_analyses[-1]  # Oldest analyzed
        newer = commit_analyses[0]  # Newest analyzed

        print("Add these values to your test:")
        print(
            f"""
# Replace in test_real_repository_incremental_analysis method:
self.first_commit = "{older['commit']}"   # Older commit
self.second_commit = "{newer['commit']}"  # Newer commit

# Replace expected stats based on analysis:
self.expected_first_commit_stats = {{
    'min_files': {max(1, older['python_files'] - 10)},
    'min_nodes': {max(1, older['functions'] - 50)},
    'min_references': {max(1, older['functions'] - 50)},
    'min_classes': {max(1, older['classes'] - 10)},
}}

self.expected_second_commit_stats = {{
    'min_files': {max(1, newer['python_files'] - 10)},
    'min_nodes': {max(1, newer['functions'] - 50)},
    'min_references': {max(1, newer['functions'] - 50)},
    'min_classes': {max(1, newer['classes'] - 10)},
}}

# Expected exact values (update after running test once):
# TODO: Run the test once and update these with actual values
# self.assertEqual(second_stats['nodes'], {newer['functions']})
# self.assertEqual(second_stats['classes'], {newer['classes']})
"""
        )

    print("\n=== Setup Complete ===")
    print("1. Update the test file with the suggested commit hashes and expected values")
    print("2. Run the test once to see actual values")
    print("3. Update the test with exact expected values for validation")


if __name__ == "__main__":
    main()
