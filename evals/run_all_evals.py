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
from evals.report_generator import generate_header, generate_static_section, generate_e2e_section, write_report

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
        
        # Unified markdown report intentionally not generated
        logger.info("Unified markdown report is disabled by configuration.")
        
        total_time = time.time() - start_time
        
        # Log summary
        logger.info("UNIFIED EVALUATION SUMMARY")
        logger.info(f"Total execution time: {total_time:.2f} seconds")
        logger.info(f"Static analysis time: {static_results.get('total_eval_time_seconds', 0):.2f} seconds")
        logger.info(f"End-to-end time: {e2e_results.get('total_eval_time_seconds', 0):.2f} seconds")
        
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
    
    logger.info("CodeBoarding Unified Evaluation Runner")
    logger.info("Running all evaluation types:")
    logger.info("  - Static Analysis Performance Evaluation")
    logger.info("  - End-to-End Pipeline Evaluation")
    
    try:
        run_all_evals()
    except KeyboardInterrupt:
        logger.info("Evaluation interrupted by user")
    except Exception as e:
        logger.error(f"Evaluation failed: {e}")
        raise


if __name__ == "__main__":
    main()
