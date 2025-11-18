import functools
import logging
import os
import time
from typing import Callable

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.scanner import ProjectScanner

logger = logging.getLogger(__name__)


class StaticAnalysisMetrics:
    def __init__(self):
        self.timing = {}
        self.file_counts = {}
        self.errors = {}
    
    def add_scanner_timing(self, duration: float) -> None:
        self.timing['scanner'] = duration
    
    def add_language_timing(self, language: str, duration: float) -> None:
        self.timing[language] = duration
    
    def add_file_metrics(self, language: str, total_files: int, error_count: int) -> None:
        metrics = {
            'total_files': total_files,
            'errors': error_count
        }
        self.file_counts[language] = metrics
        self.errors[language] = metrics
    
    def add_language_error(self, language: str, error_message: str) -> None:
        self.errors[language] = {
            'total_files': 0,
            'errors': 1,
            'error_message': error_message
        }
        self.file_counts[language] = {
            'total_files': 0,
            'errors': 1
        }
    
    def to_dict(self) -> dict:
        return {
            'timing': self.timing,
            'file_counts': self.file_counts,
            'errors': self.errors
        }


class StaticAnalysisPerformanceTracker:
    def __init__(self, enabled: bool = None):
        if enabled is None:
            enabled = os.getenv("ENABLE_MONITORING", "").lower() in ("true", "1", "yes", "on")
        self.enabled = enabled
    
    def __call__(self, func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(analyzer_self, *args, **kwargs):
            if not self.enabled:
                return func(analyzer_self, *args, **kwargs)
            
            logger.info("Performance tracking enabled for static analysis")
            
            metrics = StaticAnalysisMetrics()
            
            scanner_start = time.time()
            scanner = ProjectScanner(analyzer_self.repository_path)
            programming_langs = scanner.scan()
            scanner_duration = time.time() - scanner_start
            metrics.add_scanner_timing(scanner_duration)
            logger.info(f"Scanner completed in {scanner_duration:.2f}s")
            
            from static_analyzer import create_clients
            analyzer_self.clients = create_clients(programming_langs, analyzer_self.repository_path)
            
            results = StaticAnalysisResults()
            
            for client in analyzer_self.clients:
                lang_name = client.language.language
                lang_start = time.time()
                
                try:
                    logger.info(f"Starting tracked analysis for {lang_name} in {analyzer_self.repository_path}")
                    client.start()
                    
                    analysis = client.build_static_analysis()
                    
                    lang_duration = time.time() - lang_start
                    metrics.add_language_timing(lang_name, lang_duration)
                    logger.info(f"{lang_name} analysis completed in {lang_duration:.2f}s")
                    
                    source_files = analysis.get('source_files', [])
                    error_count = sum(1 for f in source_files if hasattr(f, 'error') and f.error)
                    metrics.add_file_metrics(lang_name, len(source_files), error_count)
                    logger.info(f"{lang_name}: {len(source_files)} files, {error_count} errors")
                    
                    results.add_references(lang_name, analysis.get("references", []))
                    results.add_cfg(lang_name, analysis.get("call_graph", []))
                    results.add_class_hierarchy(lang_name, analysis.get("class_hierarchies", []))
                    results.add_package_dependencies(lang_name, analysis.get("package_relations", []))
                    results.add_source_files(lang_name, source_files)
                    
                except Exception as e:
                    logger.error(f"Error during analysis with {lang_name}: {e}")
                    metrics.add_language_error(lang_name, str(e))
            
            analyzer_self.performance_metrics = metrics.to_dict()
            
            total_time = sum(t for k, t in metrics.timing.items() if k != 'scanner')
            total_files = sum(m['total_files'] for m in metrics.file_counts.values())
            logger.info(f"Performance tracking complete: {total_time:.2f}s, {total_files} files processed")
            
            return results
        
        return wrapper


track_static_analysis_performance = StaticAnalysisPerformanceTracker()

