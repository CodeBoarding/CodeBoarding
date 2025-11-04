#!/usr/bin/env python3
"""
End-to-End Pipeline Evaluation

Runs the complete CodeBoarding analysis pipeline on multiple projects
and tracks performance metrics including token usage and tool calls.
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

# Add the parent directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from codeboarding.evals.report_generator import generate_header, generate_e2e_section, write_report

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# Get project root from environment variable
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT"))


def read_monitoring_results(project_name: str) -> Dict[str, Any]:
    """Read monitoring results from the generated JSON file."""
    monitoring_file = PROJECT_ROOT / "evals/artifacts/monitoring_results" / f"{project_name}_monitoring.json"
    
    if not monitoring_file.exists():
        logger.warning(f"Monitoring file not found: {monitoring_file}")
        return {}
    
    try:
        with open(monitoring_file, 'r') as f:
            data = json.load(f)
        
        # Aggregate token usage across all agents
        total_tokens = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        
        # Aggregate tool usage across all agents
        tool_counts = {}
        tool_errors = {}
        
        agents = data.get("agents", {})
        for agent_name, agent_data in agents.items():
            token_usage = agent_data.get("token_usage", {})
            total_tokens += token_usage.get("total_tokens", 0)
            total_prompt_tokens += token_usage.get("prompt_tokens", 0)
            total_completion_tokens += token_usage.get("completion_tokens", 0)
            
            tool_usage = agent_data.get("tool_usage", {})
            counts = tool_usage.get("counts", {})
            errors = tool_usage.get("errors", {})
            
            for tool, count in counts.items():
                tool_counts[tool] = tool_counts.get(tool, 0) + count
            for tool, error_count in errors.items():
                tool_errors[tool] = tool_errors.get(tool, 0) + error_count
        
        return {
            "token_usage": {
                "total_tokens": total_tokens,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens
            },
            "tool_usage": {
                "counts": tool_counts,
                "errors": tool_errors
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to read monitoring results for {project_name}: {e}")
        return {}


def extract_mermaid_from_overview(project_name: str, output_dir: Path) -> str:
    """Extract Mermaid diagram from the generated overview.md file."""
    overview_file = output_dir / "on_boarding.md"
    
    if not overview_file.exists():
        logger.warning(f"Overview file not found: {overview_file}")
        return ""
    
    try:
        with open(overview_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Look for Mermaid code blocks
        import re
        mermaid_pattern = r'```mermaid\n(.*?)\n```'
        matches = re.findall(mermaid_pattern, content, re.DOTALL)
        
        if matches:
            # Return the first Mermaid diagram found
            return matches[0].strip()
        else:
            logger.info(f"No Mermaid diagram found in {overview_file}")
            return ""
            
    except Exception as e:
        logger.error(f"Failed to extract Mermaid from {overview_file}: {e}")
        return ""


def run_pipeline_for_project(project_info: Dict[str, str], output_base_dir: Path) -> Dict[str, Any]:
    """Run the full CodeBoarding pipeline for a single project using subprocess."""
    repo_url = project_info["url"]
    project_name = project_info["name"]
    output_dir = output_base_dir / Path("artifacts") / project_name
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Starting end-to-end pipeline for {project_name} ({repo_url})")
    
    # Start timing
    start_time = time.time()
    
    try:
        # Set environment variable for monitoring
        env = os.environ.copy()
        env["ENABLE_MONITORING"] = "true"
        
        # Run main.py as subprocess
        cmd = [
            sys.executable,  # Use the same Python interpreter
            "main.py",
            repo_url,
            "--output-dir",
            str(output_dir)
        ]
        
        logger.info(f"Running command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout
            env=env,
            cwd=PROJECT_ROOT  # Run main.py from project root to resolve relative paths correctly
        )
        
        elapsed_time = time.time() - start_time
        
        if result.returncode == 0:
            # Count generated files
            json_files = list(output_dir.glob("*.json"))
            md_files = list(output_dir.glob("*.md"))
            
            # Read monitoring results
            monitoring_data = read_monitoring_results(project_name)
            
            # Extract Mermaid diagram from overview.md
            mermaid_diagram = extract_mermaid_from_overview(project_name, output_dir)
            
            logger.info(f"✅ {project_name} completed successfully")
            logger.info(f"Generated {len(json_files)} JSON files and {len(md_files)} MD files")
            
            return {
                "project": project_name,
                "url": repo_url,
                "expected_language": project_info.get("expected_language"),
                "total_time_seconds": elapsed_time,
                "files_generated": {
                    "json": len(json_files),
                    "markdown": len(md_files)
                },
                "monitoring": monitoring_data,
                "mermaid_diagram": mermaid_diagram,
                "success": True,
                "stdout": (result.stdout or "")[-500:] if result.stdout and len(result.stdout) > 500 else (result.stdout or ""),  # Last 500 chars
            }
        else:
            logger.error(f"❌ {project_name} failed with return code {result.returncode}")
            logger.error(f"Error output: {result.stderr}")
            
            return {
                "project": project_name,
                "url": repo_url,
                "expected_language": project_info.get("expected_language"),
                "total_time_seconds": elapsed_time,
                "error": (result.stderr or "")[-500:] if result.stderr and len(result.stderr) > 500 else (result.stderr or ""),
                "return_code": result.returncode,
                "success": False
            }
            
    except subprocess.TimeoutExpired:
        elapsed_time = time.time() - start_time
        logger.error(f"❌ {project_name} timed out after {elapsed_time:.2f}s")
        return {
            "project": project_name,
            "url": repo_url,
            "expected_language": project_info.get("expected_language"),
            "total_time_seconds": elapsed_time,
            "error": "Pipeline execution timed out (30 minutes)",
            "success": False
        }
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(f"❌ {project_name} failed: {e}")
        return {
            "project": project_name,
            "url": repo_url,
            "expected_language": project_info.get("expected_language"),
            "total_time_seconds": elapsed_time,
            "error": str(e),
            "success": False
        }


def run_end_to_end_eval(projects=None):
    """Run end-to-end pipeline evaluation on multiple projects."""
    
    if projects is None:
        # Default project list for backward compatibility
        projects = [
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
    
    logger.info("Starting end-to-end pipeline evaluation")
    logger.info(f"Testing {len(projects)} projects: {[p['name'] for p in projects]}")
    
    output_base_dir = PROJECT_ROOT / "evals"
    results = []
    start_time = time.time()
    
    for i, project in enumerate(projects, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"Project {i}/{len(projects)}: {project['name']}")
        logger.info(f"{'='*60}")
        
        result = run_pipeline_for_project(project, output_base_dir)
        results.append(result)
    
    total_time = time.time() - start_time
    
    # Create final results structure
    eval_results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_eval_time_seconds": total_time,
        "projects": results
    }
    
    # Save results
    save_results(eval_results)
    print_summary(eval_results)
    
    return eval_results


def save_results(results: Dict[str, Any]) -> None:
    """Save evaluation results to a JSON file."""
    output_dir = PROJECT_ROOT / "evals/artifacts/monitoring_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / "end_to_end_eval.json"
    
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"Results saved to {output_file}")


def print_summary(results: Dict[str, Any]) -> None:
    """Log evaluation summary."""
    logger.info("END-TO-END PIPELINE EVALUATION SUMMARY")
    logger.info(f"Total evaluation time: {results['total_eval_time_seconds']:.2f} seconds")
    logger.info(f"Timestamp: {results['timestamp']}")
    
    total_tokens = 0
    total_tool_calls = 0
    
    for project in results['projects']:
        logger.info(f"Project: {project['project']} ({project['url']})")
        logger.info(f"Expected Language: {project.get('expected_language', 'Unknown')}")
        logger.info(f"Total Time: {project.get('total_time_seconds', 0):.2f}s")
        
        if project['success']:
            monitoring = project.get('monitoring', {})
            token_usage = monitoring.get('token_usage', {})
            tool_usage = monitoring.get('tool_usage', {})
            
            logger.info("✅ SUCCESS")
            logger.info(f"  Total tokens: {token_usage.get('total_tokens', 0)}")
            
            tool_counts = tool_usage.get('counts', {})
            if tool_counts:
                logger.info("  Tool calls:")
                for tool, count in tool_counts.items():
                    logger.info(f"    {tool}: {count}")
                    total_tool_calls += count
            
            total_tokens += token_usage.get('total_tokens', 0)
        else:
            logger.error("❌ FAILED")
            error = project.get('error', 'Unknown error')
            # Truncate long errors
            if len(error) > 200:
                error = error[:200] + "..."
            logger.error(f"  Error: {error}")
    
    # Calculate success rate
    successful = sum(1 for p in results['projects'] if p['success'])
    total = len(results['projects'])
    logger.info(f"Success Rate: {successful}/{total} ({100*successful/total:.1f}%)")
    logger.info(f"Total Tokens Used: {total_tokens}")
    logger.info(f"Total Tool Calls: {total_tool_calls}")


def main():
    """Main evaluation function."""
    
    # Setup environment variables if not set
    if not os.getenv("REPO_ROOT"):
        os.environ["REPO_ROOT"] = "repos"
    
    logger.info("CodeBoarding End-to-End Pipeline Evaluation")
    logger.info("Running full pipeline on:")
    logger.info("  - markitdown (Python)")
    logger.info("  - tsoa (TypeScript)")
    logger.info("  - cobra (Go)")
    
    try:
        results = run_end_to_end_eval()
        # Write standalone markdown report (no SECURITY.md)
        header = generate_header(
            title="End-to-End Pipeline Evaluation",
        )
        body = generate_e2e_section(results)
        report_md = "\n".join([header, body])
        write_report(report_md, PROJECT_ROOT / "evals/reports/end-to-end-report.md")
    except KeyboardInterrupt:
        logger.info("Evaluation interrupted by user")
    except Exception as e:
        logger.error(f"Evaluation failed: {e}")
        raise


if __name__ == "__main__":
    main()