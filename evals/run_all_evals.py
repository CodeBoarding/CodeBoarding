#!/usr/bin/env python3
"""
Unified Evaluation Runner

Orchestrates all evaluation types (static analysis and end-to-end pipeline)
and generates a comprehensive SECURITY.md report at the repository root.
"""

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

# Add the parent directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Import evaluation functions
from evals.static_analysis_eval import run_static_analysis_eval
from evals.end_to_end_eval import run_end_to_end_eval

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared project list (DRY principle)
PROJECTS = [
    {
        "name": "markitdown",
        "url": "https://github.com/microsoft/markitdown",
        "expected_language": "Python"
    },
    {
        "name": "tsoa",
        "url": "https://github.com/lukeautry/tsoa",
        "expected_language": "TypeScript"
    },
    {
        "name": "cobra",
        "url": "https://github.com/spf13/cobra",
        "expected_language": "Go"
    }
]


def generate_security_md(static_results: Dict[str, Any], e2e_results: Dict[str, Any], output_path: Path) -> None:
    """Generate unified SECURITY.md with all evaluation results."""
    
    with open(output_path, 'w') as f:
        # Header
        f.write("# CodeBoarding Evaluation Report\n\n")
        f.write(f"**Last Updated:** {datetime.utcnow().isoformat()}\n\n")
        
        # Calculate total evaluation time
        total_time = static_results.get('total_eval_time_seconds', 0) + e2e_results.get('total_eval_time_seconds', 0)
        f.write(f"**Total Evaluation Duration:** {total_time:.2f} seconds\n\n")
        
        # Overview section
        f.write("## Overview\n\n")
        
        # Calculate aggregate metrics
        static_projects = static_results.get('projects', [])
        e2e_projects = e2e_results.get('projects', [])
        
        total_projects = len(PROJECTS)
        static_success = sum(1 for p in static_projects if p.get('success', False))
        e2e_success = sum(1 for p in e2e_projects if p.get('success', False))
        
        # Calculate aggregate metrics
        total_tokens = 0
        total_tool_calls = 0
        total_files = 0
        total_errors = 0
        
        for project in e2e_projects:
            if project.get('success'):
                monitoring = project.get('monitoring', {})
                token_usage = monitoring.get('token_usage', {})
                tool_usage = monitoring.get('tool_usage', {})
                
                total_tokens += token_usage.get('total_tokens', 0)
                tool_counts = tool_usage.get('counts', {})
                total_tool_calls += sum(tool_counts.values()) if tool_counts else 0
        
        for project in static_projects:
            if project.get('success'):
                metrics = project.get('metrics', {})
                error_data = metrics.get('errors', {})
                for lang_data in error_data.values():
                    total_files += lang_data.get('total_files', 0)
                    total_errors += lang_data.get('errors', 0)
        
        f.write(f"- **Total Projects Tested:** {total_projects}\n")
        f.write(f"- **Static Analysis Success Rate:** {static_success}/{total_projects} ({100*static_success/total_projects:.1f}%)\n")
        f.write(f"- **End-to-End Success Rate:** {e2e_success}/{total_projects} ({100*e2e_success/total_projects:.1f}%)\n")
        f.write(f"- **Total Tokens Consumed:** {total_tokens:,}\n")
        f.write(f"- **Total Tool Calls:** {total_tool_calls}\n")
        f.write(f"- **Total Files Analyzed:** {total_files}\n")
        f.write(f"- **Total Errors Found:** {total_errors}\n\n")
        
        f.write("---\n\n")
        
        # Static Analysis Evaluation section
        f.write("## 1. Static Analysis Performance Evaluation\n\n")
        f.write(f"**Timestamp:** {static_results.get('timestamp', 'Unknown')}\n\n")
        f.write(f"**Evaluation Time:** {static_results.get('total_eval_time_seconds', 0):.2f} seconds\n\n")
        
        # Static analysis summary table
        f.write("### Summary\n\n")
        f.write("| Project | Language | Status | Time (s) | Files | Errors |\n")
        f.write("|---------|----------|--------|----------|-------|--------|\n")
        
        for project in static_projects:
            status = "✅ Success" if project.get('success', False) else "❌ Failed"
            time_taken = f"{project.get('total_time_seconds', 0):.2f}"
            lang = project.get('expected_language', 'Unknown')
            
            files_read = 0
            errors = 0
            
            if project.get('success'):
                metrics = project.get('metrics', {})
                error_data = metrics.get('errors', {})
                for lang_data in error_data.values():
                    files_read += lang_data.get('total_files', 0)
                    errors += lang_data.get('errors', 0)
            
            f.write(f"| {project.get('project', 'Unknown')} | {lang} | {status} | {time_taken} | {files_read} | {errors} |\n")
        
        # Static analysis detailed results
        f.write("\n### Detailed Results\n\n")
        for project in static_projects:
            if project.get('success'):
                f.write(f"#### {project.get('project', 'Unknown')}\n\n")
                f.write(f"- **URL:** {project.get('url', 'Unknown')}\n")
                f.write(f"- **Expected Language:** {project.get('expected_language', 'Unknown')}\n")
                f.write(f"- **Total Time:** {project.get('total_time_seconds', 0):.2f}s\n")
                f.write(f"- **Status:** ✅ Success\n\n")
                
                metrics = project.get('metrics', {})
                timing = metrics.get('timing', {})
                error_data = metrics.get('errors', {})
                
                if timing or error_data:
                    f.write("**Performance Metrics:**\n\n")
                    f.write("| Language | Time (s) | Files | Errors |\n")
                    f.write("|----------|----------|-------|--------|\n")
                    
                    for lang_key, time_taken in timing.items():
                        if lang_key != 'scanner':
                            files = error_data.get(lang_key, {}).get('total_files', 0)
                            errors = error_data.get(lang_key, {}).get('errors', 0)
                            f.write(f"| {lang_key} | {time_taken:.2f} | {files} | {errors} |\n")
                    f.write("\n")
        
        f.write("---\n\n")
        
        # End-to-End Pipeline Evaluation section
        f.write("## 2. End-to-End Pipeline Evaluation\n\n")
        f.write(f"**Timestamp:** {e2e_results.get('timestamp', 'Unknown')}\n\n")
        f.write(f"**Evaluation Time:** {e2e_results.get('total_eval_time_seconds', 0):.2f} seconds\n\n")
        
        # E2E summary table
        f.write("### Summary\n\n")
        f.write("| Project | Language | Status | Time (s) | Total Tokens | Tool Calls |\n")
        f.write("|---------|----------|--------|----------|--------------|------------|\n")
        
        for project in e2e_projects:
            status = "✅ Success" if project.get('success', False) else "❌ Failed"
            time_taken = f"{project.get('total_time_seconds', 0):.2f}"
            lang = project.get('expected_language', 'Unknown')
            
            if project.get('success'):
                monitoring = project.get('monitoring', {})
                token_usage = monitoring.get('token_usage', {})
                tool_usage = monitoring.get('tool_usage', {})
                
                total_tokens = token_usage.get('total_tokens', 0)
                tool_counts = tool_usage.get('counts', {})
                total_tool_calls = sum(tool_counts.values()) if tool_counts else 0
            else:
                total_tokens = 0
                total_tool_calls = 0
            
            f.write(f"| {project.get('project', 'Unknown')} | {lang} | {status} | {time_taken} | {total_tokens} | {total_tool_calls} |\n")
        
        # E2E project details
        f.write("\n### Project Details\n\n")
        for project in e2e_projects:
            if project.get('success'):
                f.write(f"#### {project.get('project', 'Unknown')}\n\n")
                
                # Tool usage details
                monitoring = project.get('monitoring', {})
                tool_usage = monitoring.get('tool_usage', {})
                tool_counts = tool_usage.get('counts', {})
                
                if tool_counts:
                    f.write("**Tool Usage:**\n\n")
                    f.write("| Tool | Calls |\n")
                    f.write("|------|-------|\n")
                    
                    # Sort tools by call count (descending)
                    sorted_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)
                    for tool_name, count in sorted_tools:
                        f.write(f"| {tool_name} | {count} |\n")
                    f.write("\n")
                
                # Architecture diagram
                if project.get('mermaid_diagram'):
                    f.write("**Architecture Diagram:**\n\n")
                    f.write("```mermaid\n")
                    f.write(project['mermaid_diagram'])
                    f.write("\n```\n\n")
        
        f.write("---\n\n")
        
        # Aggregate metrics section
        f.write("## Aggregate Metrics\n\n")
        f.write(f"- **Total Evaluation Time:** {total_time:.2f} seconds\n")
        f.write(f"- **Total Projects Tested:** {total_projects}\n")
        f.write(f"- **Overall Success Rate:** {100*(static_success + e2e_success)/(2*total_projects):.1f}%\n")
        f.write(f"- **Total Tokens Consumed:** {total_tokens:,}\n")
        f.write(f"- **Total Tool Calls:** {total_tool_calls}\n")
        f.write(f"- **Total Files Analyzed:** {total_files}\n")
        f.write(f"- **Total Errors Found:** {total_errors}\n")
        f.write(f"- **Average Time per Project:** {total_time/total_projects:.2f} seconds\n")
        f.write(f"- **Average Tokens per Project:** {total_tokens/total_projects:.0f}\n")
        f.write(f"- **Average Tool Calls per Project:** {total_tool_calls/total_projects:.1f}\n")
    
    logger.info(f"Unified SECURITY.md saved to {output_path}")


def run_all_evals():
    """Run all evaluations and generate unified SECURITY.md."""
    
    logger.info("Starting unified evaluation run")
    logger.info(f"Testing {len(PROJECTS)} projects: {[p['name'] for p in PROJECTS]}")
    
    start_time = time.time()
    
    try:
        # Run static analysis evaluation
        logger.info("\n" + "="*60)
        logger.info("Running Static Analysis Evaluation")
        logger.info("="*60)
        static_results = run_static_analysis_eval()
        
        # Run end-to-end evaluation
        logger.info("\n" + "="*60)
        logger.info("Running End-to-End Pipeline Evaluation")
        logger.info("="*60)
        e2e_results = run_end_to_end_eval()
        
        # Generate unified SECURITY.md
        logger.info("\n" + "="*60)
        logger.info("Generating Unified SECURITY.md")
        logger.info("="*60)
        
        output_path = Path("SECURITY.md")
        generate_security_md(static_results, e2e_results, output_path)
        
        total_time = time.time() - start_time
        
        # Print summary
        print("\n" + "="*80)
        print("UNIFIED EVALUATION SUMMARY")
        print("="*80)
        print(f"Total execution time: {total_time:.2f} seconds")
        print(f"Static analysis time: {static_results.get('total_eval_time_seconds', 0):.2f} seconds")
        print(f"End-to-end time: {e2e_results.get('total_eval_time_seconds', 0):.2f} seconds")
        print(f"SECURITY.md generated at: {output_path.absolute()}")
        print("="*80)
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "total_eval_time_seconds": total_time,
            "static_results": static_results,
            "e2e_results": e2e_results
        }
        
    except Exception as e:
        logger.error(f"Unified evaluation failed: {e}")
        raise


def main():
    """Main evaluation function."""
    load_dotenv()
    
    # Setup environment variables if not set
    if not os.getenv("REPO_ROOT"):
        os.environ["REPO_ROOT"] = "repos"
    
    print("CodeBoarding Unified Evaluation Runner")
    print("="*60)
    print("Running all evaluation types:")
    print("  - Static Analysis Performance Evaluation")
    print("  - End-to-End Pipeline Evaluation")
    print("="*60)
    
    try:
        run_all_evals()
    except KeyboardInterrupt:
        logger.info("Evaluation interrupted by user")
    except Exception as e:
        logger.error(f"Evaluation failed: {e}")
        raise


if __name__ == "__main__":
    main()
