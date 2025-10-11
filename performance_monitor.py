"""
Performance monitoring utilities for CodeBoarding
Provides comprehensive performance tracking and optimization features
"""

import time
import psutil
import logging
import threading
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from contextlib import contextmanager
from functools import wraps
import json
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetric:
    """Represents a single performance measurement"""
    operation: str
    duration: float
    timestamp: float
    memory_usage: float
    cpu_usage: float
    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class PerformanceMonitor:
    """Comprehensive performance monitoring for CodeBoarding operations"""
    
    def __init__(self, max_metrics: int = 1000):
        self.metrics: List[PerformanceMetric] = []
        self.max_metrics = max_metrics
        self.start_times: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        
    def start_timer(self, operation: str) -> None:
        """Start timing an operation"""
        with self._lock:
            self.start_times[operation] = time.time()
    
    def end_timer(self, operation: str, context: Optional[Dict[str, Any]] = None) -> float:
        """End timing an operation and record the result"""
        with self._lock:
            if operation not in self.start_times:
                logger.warning(f"Timer for operation '{operation}' was not started")
                return 0.0
            
            duration = time.time() - self.start_times[operation]
            del self.start_times[operation]
            
            # Get system metrics
            process = psutil.Process()
            memory_usage = process.memory_info().rss / 1024 / 1024  # MB
            cpu_usage = process.cpu_percent()
            
            metric = PerformanceMetric(
                operation=operation,
                duration=duration,
                timestamp=time.time(),
                memory_usage=memory_usage,
                cpu_usage=cpu_usage,
                context=context or {},
                metadata={
                    'thread_id': threading.get_ident(),
                    'process_id': process.pid
                }
            )
            
            self.metrics.append(metric)
            
            # Keep only recent metrics
            if len(self.metrics) > self.max_metrics:
                self.metrics = self.metrics[-self.max_metrics:]
            
            logger.debug(f"[PERF] {operation}: {duration:.3f}s (Memory: {memory_usage:.1f}MB)")
            return duration
    
    def get_stats(self, operation: Optional[str] = None) -> Dict[str, Any]:
        """Get performance statistics for an operation or all operations"""
        with self._lock:
            if operation:
                operation_metrics = [m for m in self.metrics if m.operation == operation]
            else:
                operation_metrics = self.metrics
            
            if not operation_metrics:
                return {}
            
            durations = [m.duration for m in operation_metrics]
            memory_usages = [m.memory_usage for m in operation_metrics]
            cpu_usages = [m.cpu_usage for m in operation_metrics]
            
            return {
                'count': len(operation_metrics),
                'duration': {
                    'total': sum(durations),
                    'average': sum(durations) / len(durations),
                    'min': min(durations),
                    'max': max(durations),
                    'median': sorted(durations)[len(durations) // 2]
                },
                'memory': {
                    'average': sum(memory_usages) / len(memory_usages),
                    'min': min(memory_usages),
                    'max': max(memory_usages),
                    'current': memory_usages[-1] if memory_usages else 0
                },
                'cpu': {
                    'average': sum(cpu_usages) / len(cpu_usages),
                    'min': min(cpu_usages),
                    'max': max(cpu_usages),
                    'current': cpu_usages[-1] if cpu_usages else 0
                }
            }
    
    def get_slow_operations(self, threshold: float = 1.0) -> List[PerformanceMetric]:
        """Get operations that took longer than threshold seconds"""
        with self._lock:
            return [m for m in self.metrics if m.duration > threshold]
    
    def get_memory_leaks(self, threshold: float = 100.0) -> List[PerformanceMetric]:
        """Detect potential memory leaks by finding operations with high memory usage"""
        with self._lock:
            return [m for m in self.metrics if m.memory_usage > threshold]
    
    def start_monitoring(self, interval: float = 5.0) -> None:
        """Start continuous system monitoring"""
        if self._monitoring:
            return
        
        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_system,
            args=(interval,),
            daemon=True
        )
        self._monitor_thread.start()
        logger.info("Started continuous performance monitoring")
    
    def stop_monitoring(self) -> None:
        """Stop continuous system monitoring"""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
        logger.info("Stopped continuous performance monitoring")
    
    def _monitor_system(self, interval: float) -> None:
        """Monitor system resources continuously"""
        while self._monitoring:
            try:
                process = psutil.Process()
                memory_usage = process.memory_info().rss / 1024 / 1024
                cpu_usage = process.cpu_percent()
                
                metric = PerformanceMetric(
                    operation="system_monitor",
                    duration=0.0,
                    timestamp=time.time(),
                    memory_usage=memory_usage,
                    cpu_usage=cpu_usage,
                    context={'monitoring': True}
                )
                
                with self._lock:
                    self.metrics.append(metric)
                    if len(self.metrics) > self.max_metrics:
                        self.metrics = self.metrics[-self.max_metrics:]
                
                time.sleep(interval)
            except Exception as e:
                logger.error(f"Error in system monitoring: {e}")
                time.sleep(interval)
    
    def export_metrics(self, filepath: Optional[Path] = None) -> Path:
        """Export metrics to JSON file"""
        if filepath is None:
            filepath = Path("performance_metrics.json")
        
        with self._lock:
            metrics_data = [
                {
                    'operation': m.operation,
                    'duration': m.duration,
                    'timestamp': m.timestamp,
                    'memory_usage': m.memory_usage,
                    'cpu_usage': m.cpu_usage,
                    'context': m.context,
                    'metadata': m.metadata
                }
                for m in self.metrics
            ]
        
        with open(filepath, 'w') as f:
            json.dump(metrics_data, f, indent=2)
        
        logger.info(f"Exported {len(metrics_data)} performance metrics to {filepath}")
        return filepath
    
    def reset(self) -> None:
        """Reset all metrics"""
        with self._lock:
            self.metrics.clear()
            self.start_times.clear()
        logger.info("Performance metrics reset")


class CodeBoardingPerformanceMonitor(PerformanceMonitor):
    """Specialized performance monitor for CodeBoarding operations"""
    
    def __init__(self, max_metrics: int = 1000):
        super().__init__(max_metrics)
        self.operation_counts: Dict[str, int] = {}
        self.error_counts: Dict[str, int] = {}
    
    def track_operation(self, operation: str, context: Optional[Dict[str, Any]] = None) -> None:
        """Track the start of a CodeBoarding operation"""
        self.start_timer(operation)
        self.operation_counts[operation] = self.operation_counts.get(operation, 0) + 1
    
    def complete_operation(self, operation: str, success: bool = True, 
                          context: Optional[Dict[str, Any]] = None) -> float:
        """Complete a CodeBoarding operation and record success/failure"""
        duration = self.end_timer(operation, context)
        
        if not success:
            self.error_counts[operation] = self.error_counts.get(operation, 0) + 1
            logger.warning(f"Operation '{operation}' failed after {duration:.3f}s")
        else:
            logger.info(f"Operation '{operation}' completed in {duration:.3f}s")
        
        return duration
    
    def get_operation_stats(self) -> Dict[str, Any]:
        """Get comprehensive operation statistics"""
        stats = self.get_stats()
        
        total_operations = sum(self.operation_counts.values())
        total_errors = sum(self.error_counts.values())
        
        return {
            'performance': stats,
            'operations': {
                'total': total_operations,
                'by_type': self.operation_counts,
                'success_rate': (total_operations - total_errors) / total_operations if total_operations > 0 else 0
            },
            'errors': {
                'total': total_errors,
                'by_type': self.error_counts
            }
        }


# Global performance monitor instance
_global_monitor: Optional[CodeBoardingPerformanceMonitor] = None


def get_global_monitor() -> CodeBoardingPerformanceMonitor:
    """Get the global performance monitor instance"""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = CodeBoardingPerformanceMonitor()
    return _global_monitor


@contextmanager
def performance_timer(operation: str, context: Optional[Dict[str, Any]] = None):
    """Context manager for timing operations"""
    monitor = get_global_monitor()
    monitor.track_operation(operation, context)
    try:
        yield monitor
    except Exception as e:
        monitor.complete_operation(operation, success=False, context=context)
        raise
    else:
        monitor.complete_operation(operation, success=True, context=context)


def performance_monitor(operation: Optional[str] = None, context: Optional[Dict[str, Any]] = None):
    """Decorator for monitoring function performance"""
    def decorator(func: Callable) -> Callable:
        op_name = operation or f"{func.__module__}.{func.__name__}"
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            monitor = get_global_monitor()
            monitor.track_operation(op_name, context)
            try:
                result = func(*args, **kwargs)
                monitor.complete_operation(op_name, success=True, context=context)
                return result
            except Exception as e:
                monitor.complete_operation(op_name, success=False, context=context)
                raise
        
        return wrapper
    return decorator


class DiagramGenerationPerformanceMonitor:
    """Specialized monitor for diagram generation operations"""
    
    def __init__(self):
        self.monitor = get_global_monitor()
        self.component_times: Dict[str, float] = {}
        self.agent_times: Dict[str, float] = {}
        self.file_processing_times: Dict[str, float] = {}
    
    def track_component_analysis(self, component_name: str) -> None:
        """Track the start of component analysis"""
        self.monitor.track_operation(f"component_analysis_{component_name}")
    
    def complete_component_analysis(self, component_name: str, success: bool = True) -> float:
        """Complete component analysis tracking"""
        duration = self.monitor.complete_operation(f"component_analysis_{component_name}", success)
        self.component_times[component_name] = duration
        return duration
    
    def track_agent_operation(self, agent_name: str, operation: str) -> None:
        """Track agent operations"""
        op_name = f"agent_{agent_name}_{operation}"
        self.monitor.track_operation(op_name)
    
    def complete_agent_operation(self, agent_name: str, operation: str, success: bool = True) -> float:
        """Complete agent operation tracking"""
        op_name = f"agent_{agent_name}_{operation}"
        duration = self.monitor.complete_operation(op_name, success)
        self.agent_times[f"{agent_name}_{operation}"] = duration
        return duration
    
    def track_file_processing(self, file_path: str) -> None:
        """Track file processing operations"""
        file_name = Path(file_path).name
        self.monitor.track_operation(f"file_processing_{file_name}")
    
    def complete_file_processing(self, file_path: str, success: bool = True) -> float:
        """Complete file processing tracking"""
        file_name = Path(file_path).name
        duration = self.monitor.complete_operation(f"file_processing_{file_name}", success)
        self.file_processing_times[file_name] = duration
        return duration
    
    def get_diagram_stats(self) -> Dict[str, Any]:
        """Get diagram generation specific statistics"""
        return {
            'component_analysis': {
                'count': len(self.component_times),
                'total_time': sum(self.component_times.values()),
                'average_time': sum(self.component_times.values()) / len(self.component_times) if self.component_times else 0,
                'times': self.component_times
            },
            'agent_operations': {
                'count': len(self.agent_times),
                'total_time': sum(self.agent_times.values()),
                'average_time': sum(self.agent_times.values()) / len(self.agent_times) if self.agent_times else 0,
                'times': self.agent_times
            },
            'file_processing': {
                'count': len(self.file_processing_times),
                'total_time': sum(self.file_processing_times.values()),
                'average_time': sum(self.file_processing_times.values()) / len(self.file_processing_times) if self.file_processing_times else 0,
                'times': self.file_processing_times
            }
        }


class StaticAnalysisPerformanceMonitor:
    """Specialized monitor for static analysis operations"""
    
    def __init__(self):
        self.monitor = get_global_monitor()
        self.scan_times: Dict[str, float] = {}
        self.analysis_times: Dict[str, float] = {}
    
    def track_repository_scan(self, repo_name: str) -> None:
        """Track repository scanning operations"""
        self.monitor.track_operation(f"repo_scan_{repo_name}")
    
    def complete_repository_scan(self, repo_name: str, success: bool = True) -> float:
        """Complete repository scan tracking"""
        duration = self.monitor.complete_operation(f"repo_scan_{repo_name}", success)
        self.scan_times[repo_name] = duration
        return duration
    
    def track_analysis(self, analysis_type: str) -> None:
        """Track analysis operations"""
        self.monitor.track_operation(f"analysis_{analysis_type}")
    
    def complete_analysis(self, analysis_type: str, success: bool = True) -> float:
        """Complete analysis tracking"""
        duration = self.monitor.complete_operation(f"analysis_{analysis_type}", success)
        self.analysis_times[analysis_type] = duration
        return duration
    
    def get_analysis_stats(self) -> Dict[str, Any]:
        """Get static analysis specific statistics"""
        return {
            'repository_scans': {
                'count': len(self.scan_times),
                'total_time': sum(self.scan_times.values()),
                'average_time': sum(self.scan_times.values()) / len(self.scan_times) if self.scan_times else 0,
                'times': self.scan_times
            },
            'analyses': {
                'count': len(self.analysis_times),
                'total_time': sum(self.analysis_times.values()),
                'average_time': sum(self.analysis_times.values()) / len(self.analysis_times) if self.analysis_times else 0,
                'times': self.analysis_times
            }
        }


# Utility functions for easy access
def start_performance_monitoring(interval: float = 5.0) -> None:
    """Start global performance monitoring"""
    monitor = get_global_monitor()
    monitor.start_monitoring(interval)


def stop_performance_monitoring() -> None:
    """Stop global performance monitoring"""
    monitor = get_global_monitor()
    monitor.stop_monitoring()


def get_performance_stats() -> Dict[str, Any]:
    """Get comprehensive performance statistics"""
    monitor = get_global_monitor()
    return monitor.get_operation_stats()


def export_performance_metrics(filepath: Optional[Path] = None) -> Path:
    """Export performance metrics to file"""
    monitor = get_global_monitor()
    return monitor.export_metrics(filepath)


def reset_performance_metrics() -> None:
    """Reset all performance metrics"""
    monitor = get_global_monitor()
    monitor.reset()
