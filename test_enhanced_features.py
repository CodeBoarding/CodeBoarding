#!/usr/bin/env python3
"""
Test script for CodeBoarding enhanced features
Demonstrates performance monitoring, error handling, and health status features
"""

import time
import logging
import json
from pathlib import Path
from typing import Dict, Any

# Import enhanced utilities
from performance_monitor import (
    DiagramGenerationPerformanceMonitor, 
    performance_timer, 
    performance_monitor,
    get_performance_stats,
    start_performance_monitoring,
    stop_performance_monitoring
)
from error_handler import (
    DiagramGenerationErrorHandler, 
    DiagramGenerationError, 
    StaticAnalysisError,
    AgentError,
    handle_error, 
    error_handler,
    safe_execute,
    get_error_stats,
    get_recovery_suggestions
)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class MockComponent:
    """Mock component for testing"""
    def __init__(self, name: str):
        self.name = name


class MockAgent:
    """Mock agent for testing"""
    def __init__(self, name: str):
        self.name = name
    
    def run(self, component):
        """Mock agent operation"""
        time.sleep(0.1)  # Simulate work
        if component.name == "failing_component":
            raise AgentError(f"Agent {self.name} failed to process {component.name}")
        return f"Processed {component.name}"


def test_performance_monitoring():
    """Test performance monitoring features"""
    logger.info("Testing performance monitoring features...")
    
    # Initialize performance monitor
    monitor = DiagramGenerationPerformanceMonitor()
    
    # Test component analysis tracking
    components = ["component1", "component2", "component3", "slow_component"]
    
    for component_name in components:
        monitor.track_component_analysis(component_name)
        
        # Simulate work
        if component_name == "slow_component":
            time.sleep(2.0)  # Slow operation
        else:
            time.sleep(0.5)   # Normal operation
        
        monitor.complete_component_analysis(component_name, success=True)
    
    # Test agent operation tracking
    agents = ["meta_agent", "details_agent", "abstraction_agent"]
    operations = ["analyze", "process", "validate"]
    
    for agent_name in agents:
        for operation in operations:
            monitor.track_agent_operation(agent_name, operation)
            time.sleep(0.2)  # Simulate work
            monitor.complete_agent_operation(agent_name, operation, success=True)
    
    # Test file processing tracking
    files = ["file1.py", "file2.py", "file3.py"]
    
    for file_name in files:
        monitor.track_file_processing(f"path/to/{file_name}")
        time.sleep(0.1)  # Simulate work
        monitor.complete_file_processing(f"path/to/{file_name}", success=True)
    
    # Get statistics
    stats = monitor.get_diagram_stats()
    
    logger.info("Performance monitoring test completed")
    logger.info(f"Component analysis: {stats['component_analysis']['count']} operations")
    logger.info(f"Agent operations: {stats['agent_operations']['count']} operations")
    logger.info(f"File processing: {stats['file_processing']['count']} operations")
    
    return stats


def test_error_handling():
    """Test error handling features"""
    logger.info("Testing error handling features...")
    
    # Initialize error handler
    handler = DiagramGenerationErrorHandler()
    
    # Test component errors
    components = ["component1", "failing_component", "component3"]
    
    for component_name in components:
        try:
            if component_name == "failing_component":
                raise DiagramGenerationError(f"Failed to process {component_name}")
            else:
                logger.info(f"Successfully processed {component_name}")
        except Exception as e:
            handler.handle_component_error(component_name, e, {
                'operation': 'test_processing',
                'context': 'test'
            })
    
    # Test agent errors
    agents = ["meta_agent", "failing_agent", "details_agent"]
    
    for agent_name in agents:
        try:
            if agent_name == "failing_agent":
                raise AgentError(f"Agent {agent_name} failed")
            else:
                logger.info(f"Agent {agent_name} completed successfully")
        except Exception as e:
            handler.handle_agent_error(agent_name, "test_operation", e, {
                'operation': 'test_agent',
                'context': 'test'
            })
    
    # Get error statistics
    stats = handler.get_diagram_error_stats()
    
    logger.info("Error handling test completed")
    logger.info(f"Component errors: {stats['component_errors']['count']}")
    logger.info(f"Agent errors: {stats['agent_errors']['count']}")
    
    return stats


def test_performance_decorators():
    """Test performance monitoring decorators"""
    logger.info("Testing performance monitoring decorators...")
    
    @performance_monitor(operation="test_decorated_function")
    def test_function(duration: float = 0.5):
        """Test function with performance monitoring"""
        time.sleep(duration)
        return f"Slept for {duration} seconds"
    
    # Test the decorated function
    result = test_function(0.3)
    logger.info(f"Decorated function result: {result}")
    
    # Test context manager
    with performance_timer("test_context_manager") as monitor:
        time.sleep(0.2)
        logger.info("Context manager test completed")
    
    # Get performance stats
    stats = get_performance_stats()
    logger.info(f"Performance decorators test completed")
    
    return stats


def test_error_decorators():
    """Test error handling decorators"""
    logger.info("Testing error handling decorators...")
    
    @error_handler(severity="error", recoverable=True)
    def test_function(should_fail: bool = False):
        """Test function with error handling"""
        if should_fail:
            raise DiagramGenerationError("Test error from decorated function")
        return "Function completed successfully"
    
    # Test successful execution
    result = test_function(False)
    logger.info(f"Successful execution: {result}")
    
    # Test error handling
    try:
        result = test_function(True)
    except DiagramGenerationError as e:
        logger.info(f"Error handled: {e.message}")
    
    # Test safe execution
    result, error_info = safe_execute(test_function, True)
    if error_info:
        logger.info(f"Safe execution caught error: {error_info.message}")
    else:
        logger.info(f"Safe execution result: {result}")
    
    logger.info("Error decorators test completed")


def test_recovery_suggestions():
    """Test error recovery suggestions"""
    logger.info("Testing error recovery suggestions...")
    
    error_types = [
        "DiagramGenerationError",
        "StaticAnalysisError", 
        "AgentError",
        "RepositoryError",
        "ConfigurationError",
        "ValidationError",
        "NetworkError",
        "FileSystemError"
    ]
    
    for error_type in error_types:
        suggestions = get_recovery_suggestions(error_type)
        logger.info(f"Recovery suggestions for {error_type}: {len(suggestions)} suggestions")
        for i, suggestion in enumerate(suggestions[:2], 1):  # Show first 2 suggestions
            logger.info(f"  {i}. {suggestion}")
    
    logger.info("Recovery suggestions test completed")


def test_health_status():
    """Test health status monitoring"""
    logger.info("Testing health status monitoring...")
    
    # Mock health status data
    component_stats = {
        "component1": {"status": "completed", "duration": 1.5},
        "component2": {"status": "completed", "duration": 2.0},
        "component3": {"status": "failed", "duration": 0.5, "error": "Test error"},
        "component4": {"status": "skipped", "duration": 0.1}
    }
    
    # Calculate health score
    total_components = len(component_stats)
    successful_components = len([c for c in component_stats.values() if c['status'] == 'completed'])
    failed_components = len([c for c in component_stats.values() if c['status'] == 'failed'])
    skipped_components = len([c for c in component_stats.values() if c['status'] == 'skipped'])
    
    health_score = (successful_components / total_components * 100) if total_components > 0 else 0
    
    health_status = {
        'health_score': health_score,
        'status': 'healthy' if health_score >= 80 else 'degraded' if health_score >= 60 else 'unhealthy',
        'components': {
            'total': total_components,
            'successful': successful_components,
            'failed': failed_components,
            'skipped': skipped_components
        }
    }
    
    logger.info(f"Health Status: {health_status['status']} (Score: {health_status['health_score']:.1f}%)")
    logger.info(f"Components: {successful_components}/{total_components} successful")
    
    return health_status


def test_export_functionality():
    """Test export functionality"""
    logger.info("Testing export functionality...")
    
    # Test performance metrics export
    perf_stats = get_performance_stats()
    perf_file = Path("test_performance_metrics.json")
    
    with open(perf_file, 'w') as f:
        json.dump(perf_stats, f, indent=2)
    
    logger.info(f"Performance metrics exported to {perf_file}")
    
    # Test error log export
    error_stats = get_error_stats()
    error_file = Path("test_error_log.json")
    
    with open(error_file, 'w') as f:
        json.dump(error_stats, f, indent=2)
    
    logger.info(f"Error log exported to {error_file}")
    
    # Clean up test files
    perf_file.unlink()
    error_file.unlink()
    
    logger.info("Export functionality test completed")


def test_continuous_monitoring():
    """Test continuous monitoring"""
    logger.info("Testing continuous monitoring...")
    
    # Start continuous monitoring
    start_performance_monitoring(interval=1.0)  # Monitor every second
    
    # Let it run for a few seconds
    time.sleep(3)
    
    # Stop monitoring
    stop_performance_monitoring()
    
    logger.info("Continuous monitoring test completed")


def run_comprehensive_test():
    """Run comprehensive test of all enhanced features"""
    logger.info("Starting comprehensive test of CodeBoarding enhanced features...")
    
    try:
        # Test performance monitoring
        perf_stats = test_performance_monitoring()
        
        # Test error handling
        error_stats = test_error_handling()
        
        # Test performance decorators
        decorator_stats = test_performance_decorators()
        
        # Test error decorators
        test_error_decorators()
        
        # Test recovery suggestions
        test_recovery_suggestions()
        
        # Test health status
        health_status = test_health_status()
        
        # Test export functionality
        test_export_functionality()
        
        # Test continuous monitoring
        test_continuous_monitoring()
        
        logger.info("All tests completed successfully!")
        
        # Summary
        logger.info("=== TEST SUMMARY ===")
        logger.info(f"Performance monitoring: {perf_stats['component_analysis']['count']} component operations")
        logger.info(f"Error handling: {error_stats['component_errors']['count']} component errors")
        logger.info(f"Health status: {health_status['status']} (Score: {health_status['health_score']:.1f}%)")
        
        return True
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        return False


if __name__ == "__main__":
    success = run_comprehensive_test()
    exit(0 if success else 1)
