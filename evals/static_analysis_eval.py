#!/usr/bin/env python3
"""
Static Analysis Performance Evaluation

Runs static analysis on multiple projects with different languages and tracks
performance metrics like errors, timing, and file counts.
"""

import json
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
from evals.report_generator import generate_header, generate_static_section, write_report
from diagram_analysis import DiagramGenerator
from repo_utils import clone_repository
from utils import create_temp_repo_folder, remove_temp_repo_folder

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def analyze_project_static_analysis(project_info: Dict[str, str]) -> Dict[str, Any]:
    """Analyze a single project and return static analysis metrics."""
    repo_url = project_info["url"]
    project_name = project_info["name"]
    temp_folder = create_temp_repo_folder()
    
    logger.info(f"Starting static analysis for {project_name} ({repo_url})")
    
    # Start timing for this project
    project_start_time = time.time()
    
    try:
        # Clone the repository
        repo_name = clone_repository(repo_url, Path(os.getenv("REPO_ROOT")))
        repo_path = Path(os.getenv("REPO_ROOT")) / repo_name
        
        logger.info(f"Repository cloned to {repo_path}")
        
        # Run only static analysis (not full pipeline)
        generator = DiagramGenerator(
            repo_location=repo_path,
            temp_folder=temp_folder,
            repo_name=repo_name,
            output_dir=temp_folder,
            depth_level=1,
            enable_monitoring=True
        )
        
        # Just run static analysis, not full generation
        static_analysis = generator.generate_static_analysis()
        
        # Get the metrics that were collected
        metrics = getattr(generator, 'static_analysis_metrics', {})
        
        # Calculate total time for this project
        project_total_time = time.time() - project_start_time
        
        logger.info(f"Static analysis completed for {project_name}")
        logger.info(f"Total time: {project_total_time:.2f}s")
        logger.info(f"Timing: {metrics.get('timing', {})}")
        logger.info(f"File counts: {metrics.get('errors', {})}")
        
        return {
            "project": project_name,
            "url": repo_url,
            "expected_language": project_info.get("expected_language"),
            "total_time_seconds": project_total_time,
            "metrics": metrics,
            "success": True
        }
        
    except Exception as e:
        project_total_time = time.time() - project_start_time
        logger.error(f"Static analysis failed for {project_name}: {e}")
        return {
            "project": project_name,
            "url": repo_url,
            "expected_language": project_info.get("expected_language"),
            "total_time_seconds": project_total_time,
            "error": str(e),
            "success": False
        }
    finally:
        remove_temp_repo_folder(temp_folder)


def run_static_analysis_eval(projects=None):
    """Run static analysis on multiple projects and track performance."""
    
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
    
    logger.info("Starting static analysis performance evaluation")
    logger.info(f"Testing {len(projects)} projects: {[p['name'] for p in projects]}")
    
    results = []
    start_time = time.time()
    
    for i, project in enumerate(projects, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"Project {i}/{len(projects)}: {project['name']}")
        logger.info(f"{'='*60}")
        
        result = analyze_project_static_analysis(project)
        results.append(result)
        
        if result["success"]:
            logger.info(f"✅ {project['name']} completed successfully")
        else:
            logger.error(f"❌ {project['name']} failed: {result.get('error', 'Unknown error')}")
    
    total_time = time.time() - start_time
    
    # Create final results structure
    eval_results = {
        "timestamp": datetime.utcnow().isoformat(),
        "total_eval_time_seconds": total_time,
        "projects": results
    }
    
    # Save results
    save_static_analysis_results(eval_results)
    print_static_analysis_summary(eval_results)
    
    return eval_results


def save_static_analysis_results(results: Dict[str, Any]) -> None:
    """Save static analysis results to a JSON file."""
    output_dir = Path("evals/monitoring_results")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / "static_analysis_eval.json"
    
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"Results saved to {output_file}")


def print_static_analysis_summary(results: Dict[str, Any]) -> None:
    """Print a summary of static analysis results."""
    print("\n" + "="*80)
    print("STATIC ANALYSIS PERFORMANCE EVALUATION SUMMARY")
    print("="*80)
    
    print(f"Total evaluation time: {results['total_eval_time_seconds']:.2f} seconds")
    print(f"Timestamp: {results['timestamp']}")
    print()
    
    for project in results['projects']:
        print(f"Project: {project['project']}")
        print(f"URL: {project['url']}")
        print(f"Expected Language: {project.get('expected_language', 'Unknown')}")
        print(f"Total Time: {project.get('total_time_seconds', 0):.2f}s")
        
        if project['success']:
            metrics = project.get('metrics', {})
            timing = metrics.get('timing', {})
            errors = metrics.get('errors', {})
            
            print("✅ SUCCESS")
            print(f"  Scanner time: {timing.get('scanner', 0):.2f}s")
            
            for lang, time_taken in timing.items():
                if lang != 'scanner':
                    file_count = errors.get(lang, {}).get('total_files', 0)
                    error_count = errors.get(lang, {}).get('errors', 0)
                    print(f"  {lang} analysis: {time_taken:.2f}s ({file_count} files, {error_count} errors)")
        else:
            print("❌ FAILED")
            print(f"  Error: {project.get('error', 'Unknown error')}")
        
        print("-" * 40)
    
    # Calculate totals
    total_files = 0
    total_errors = 0
    total_analysis_time = 0
    
    for project in results['projects']:
        if project['success']:
            metrics = project.get('metrics', {})
            timing = metrics.get('timing', {})
            errors = metrics.get('errors', {})
            
            for lang, time_taken in timing.items():
                if lang != 'scanner':
                    total_analysis_time += time_taken
                    total_files += errors.get(lang, {}).get('total_files', 0)
                    total_errors += errors.get(lang, {}).get('errors', 0)
    
    print(f"\nTOTALS:")
    print(f"  Total analysis time: {total_analysis_time:.2f}s")
    print(f"  Total files processed: {total_files}")
    print(f"  Total errors: {total_errors}")
    print(f"  Average time per file: {total_analysis_time/max(total_files, 1):.3f}s")
    print("="*80)


def main():
    """Main evaluation function."""
    load_dotenv()
    
    # Setup environment variables if not set
    if not os.getenv("REPO_ROOT"):
        os.environ["REPO_ROOT"] = "repos"
    
    print("CodeBoarding Static Analysis Performance Evaluation")
    print("="*60)
    print("Testing static analysis performance on:")
    print("  - markitdown (Python)")
    print("  - tsoa (TypeScript)")
    print("  - cobra (Go)")
    print("="*60)
    
    try:
        results = run_static_analysis_eval()
        # Write standalone markdown report (no SECURITY.md)
        header = generate_header(
            title="Static Analysis Performance Evaluation",
        )
        body = generate_static_section(results)
        report_md = "\n".join([header, body])
        write_report(report_md, Path("evals/reports/static-analysis-report.md"))
    except KeyboardInterrupt:
        logger.info("Evaluation interrupted by user")
    except Exception as e:
        logger.error(f"Evaluation failed: {e}")
        raise


if __name__ == "__main__":
    main()
