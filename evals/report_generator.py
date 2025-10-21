#!/usr/bin/env python3
"""
Shared Markdown report generator for evaluation outputs.

Generates standalone markdown for:
- Static Analysis results
- End-to-End pipeline results

All writers intentionally avoid touching SECURITY.md. Callers should write
reports into evals/reports/.
"""

from __future__ import annotations

import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def generate_header(title: str, timestamp: str | None = None, extra_lines: list[str] | None = None) -> str:
    ts = timestamp or datetime.utcnow().isoformat()
    lines = [f"# {title}", "", f"**Generated:** {ts}", ""]
    if extra_lines:
        lines.extend(extra_lines)
        lines.append("")
    return "\n".join(lines)


def generate_static_section(static_results: Dict[str, Any]) -> str:
    projects = static_results.get("projects", [])
    total_eval_time = static_results.get("total_eval_time_seconds", 0)

    # Aggregate totals
    total_files = 0
    total_errors = 0
    for project in projects:
        if project.get("success"):
            metrics = project.get("metrics", {})
            error_data = metrics.get("errors", {})
            for lang_data in error_data.values():
                total_files += lang_data.get("total_files", 0)
                total_errors += lang_data.get("errors", 0)

    lines = [
        f"**Evaluation Time:** {total_eval_time:.2f} seconds",
        "",
        "### Summary",
        "",
        "| Project | Language | Status | Time (s) | Files | Errors |",
        "|---------|----------|--------|----------|-------|--------|",
    ]

    for project in projects:
        status = "✅ Success" if project.get("success", False) else "❌ Failed"
        time_taken = f"{project.get('total_time_seconds', 0):.2f}"
        lang = project.get("expected_language", "Unknown")

        files_read = 0
        errors = 0
        if project.get("success"):
            metrics = project.get("metrics", {})
            error_data = metrics.get("errors", {})
            for lang_data in error_data.values():
                files_read += lang_data.get("total_files", 0)
                errors += lang_data.get("errors", 0)

        lines.append(
            f"| {project.get('project', 'Unknown')} | {lang} | {status} | {time_taken} | {files_read} | {errors} |"
        )


    return "\n".join(lines)


def generate_e2e_section(e2e_results: Dict[str, Any]) -> str:
    projects = e2e_results.get("projects", [])
    total_eval_time = e2e_results.get("total_eval_time_seconds", 0)

    lines = [
        f"**Evaluation Time:** {total_eval_time:.2f} seconds",
        "",
        "### Summary",
        "",
        "| Project | Language | Status | Time (s) | Total Tokens | Tool Calls |",
        "|---------|----------|--------|----------|--------------|------------|",
    ]

    for project in projects:
        status = "✅ Success" if project.get("success", False) else "❌ Failed"
        time_taken = f"{project.get('total_time_seconds', 0):.2f}"
        lang = project.get("expected_language", "Unknown")

        if project.get("success"):
            monitoring = project.get("monitoring", {})
            token_usage = monitoring.get("token_usage", {})
            tool_usage = monitoring.get("tool_usage", {})
            total_tokens = token_usage.get("total_tokens", 0)
            tool_counts = tool_usage.get("counts", {})
            total_tool_calls = sum(tool_counts.values()) if tool_counts else 0
        else:
            total_tokens = 0
            total_tool_calls = 0

        lines.append(
            f"| {project.get('project', 'Unknown')} | {lang} | {status} | {time_taken} | {total_tokens} | {total_tool_calls} |"
        )


    return "\n".join(lines)


def generate_system_specs() -> str:
    """Generate system specifications section for reports."""
    lines = [
        "## System Specifications",
        "",
    ]
    
    # Get OS information
    os_name = platform.system()
    os_version = platform.platform()
    lines.append(f"**Operating System:** {os_name} ({os_version})")
    
    # Get CPU information
    processor = platform.processor()
    if not processor or processor == "":
        processor = platform.machine()
    lines.append(f"**Processor:** {processor}")
    
    # Get number of cores
    cpu_count = os.cpu_count()
    lines.append(f"**CPU Cores:** {cpu_count}")
    
    # Get git user information (with graceful fallback)
    try:
        git_name_result = subprocess.run(
            ["git", "config", "user.name"], 
            capture_output=True, 
            text=True, 
            timeout=5
        )
        
        if git_name_result.returncode == 0:
            git_name = git_name_result.stdout.strip()
            lines.append(f"**Git User:** {git_name}")
        else:
            lines.append("**Git User:** Not configured")
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        lines.append("**Git User:** Not available")
    
    lines.append("")
    return "\n".join(lines)


def write_report(markdown: str, output_path: Path) -> None:
    _ensure_parent_dir(output_path)
    
    # Append system specifications to the markdown
    system_specs = generate_system_specs()
    full_markdown = f"{markdown}\n{system_specs}"
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_markdown)


