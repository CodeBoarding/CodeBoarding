"""
Enhanced error handling utilities for CodeBoarding
Provides comprehensive error management and user feedback
"""

import logging
import traceback
import json
from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
import functools
import sys

logger = logging.getLogger(__name__)


@dataclass
class ErrorInfo:
    """Represents detailed error information"""
    error_type: str
    message: str
    timestamp: datetime
    traceback: str
    context: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    severity: str = "error"  # error, warning, info
    recoverable: bool = True


class CodeBoardingError(Exception):
    """Base exception class for CodeBoarding errors"""
    
    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None, 
                 severity: str = "error", recoverable: bool = True):
        super().__init__(message)
        self.message = message
        self.context = context or {}
        self.severity = severity
        self.recoverable = recoverable
        self.timestamp = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary"""
        return {
            'error_type': self.__class__.__name__,
            'message': self.message,
            'timestamp': self.timestamp.isoformat(),
            'context': self.context,
            'severity': self.severity,
            'recoverable': self.recoverable
        }


class DiagramGenerationError(CodeBoardingError):
    """Error during diagram generation"""
    pass


class StaticAnalysisError(CodeBoardingError):
    """Error during static analysis"""
    pass


class AgentError(CodeBoardingError):
    """Error in AI agent operations"""
    pass


class RepositoryError(CodeBoardingError):
    """Error in repository operations"""
    pass


class ConfigurationError(CodeBoardingError):
    """Error in configuration"""
    pass


class ValidationError(CodeBoardingError):
    """Error in validation"""
    pass


class NetworkError(CodeBoardingError):
    """Error in network operations"""
    pass


class FileSystemError(CodeBoardingError):
    """Error in file system operations"""
    pass


class ErrorHandler:
    """Comprehensive error handling for CodeBoarding operations"""
    
    def __init__(self, max_errors: int = 1000):
        self.errors: List[ErrorInfo] = []
        self.max_errors = max_errors
        self.error_counts: Dict[str, int] = {}
        self.recovery_strategies: Dict[str, Callable] = {}
        self.error_filters: List[Callable[[ErrorInfo], bool]] = []
        
    def register_recovery_strategy(self, error_type: str, strategy: Callable) -> None:
        """Register a recovery strategy for a specific error type"""
        self.recovery_strategies[error_type] = strategy
        logger.info(f"Registered recovery strategy for {error_type}")
    
    def add_error_filter(self, filter_func: Callable[[ErrorInfo], bool]) -> None:
        """Add an error filter to exclude certain errors from logging"""
        self.error_filters.append(filter_func)
    
    def handle_error(self, error: Exception, context: Optional[Dict[str, Any]] = None,
                    severity: str = "error", recoverable: bool = True) -> ErrorInfo:
        """Handle an error and return error information"""
        
        # Create error info
        error_info = ErrorInfo(
            error_type=type(error).__name__,
            message=str(error),
            timestamp=datetime.now(),
            traceback=traceback.format_exc(),
            context=context or {},
            severity=severity,
            recoverable=recoverable,
            metadata={
                'module': getattr(error, '__module__', 'unknown'),
                'function': getattr(error, '__name__', 'unknown')
            }
        )
        
        # Apply filters
        if any(filter_func(error_info) for filter_func in self.error_filters):
            return error_info
        
        # Add to error log
        self.errors.append(error_info)
        
        # Update error counts
        self.error_counts[error_info.error_type] = self.error_counts.get(error_info.error_type, 0) + 1
        
        # Keep only recent errors
        if len(self.errors) > self.max_errors:
            self.errors = self.errors[-self.max_errors:]
        
        # Log the error
        self._log_error(error_info)
        
        # Attempt recovery if possible
        if recoverable and error_info.error_type in self.recovery_strategies:
            try:
                self.recovery_strategies[error_info.error_type](error_info)
                logger.info(f"Recovery strategy executed for {error_info.error_type}")
            except Exception as recovery_error:
                logger.error(f"Recovery strategy failed: {recovery_error}")
        
        return error_info
    
    def _log_error(self, error_info: ErrorInfo) -> None:
        """Log error information"""
        log_message = f"[{error_info.severity.upper()}] {error_info.error_type}: {error_info.message}"
        
        if error_info.context:
            log_message += f" | Context: {error_info.context}"
        
        if error_info.severity == "error":
            logger.error(log_message)
        elif error_info.severity == "warning":
            logger.warning(log_message)
        else:
            logger.info(log_message)
        
        # Log traceback for errors
        if error_info.severity == "error":
            logger.debug(f"Traceback: {error_info.traceback}")
    
    def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics"""
        total_errors = len(self.errors)
        error_types = {}
        severity_counts = {}
        recoverable_count = 0
        
        for error in self.errors:
            error_types[error.error_type] = error_types.get(error.error_type, 0) + 1
            severity_counts[error.severity] = severity_counts.get(error.severity, 0) + 1
            if error.recoverable:
                recoverable_count += 1
        
        return {
            'total_errors': total_errors,
            'error_types': error_types,
            'severity_counts': severity_counts,
            'recoverable_count': recoverable_count,
            'recovery_rate': recoverable_count / total_errors if total_errors > 0 else 0,
            'recent_errors': self.errors[-10:] if self.errors else []
        }
    
    def get_errors_by_type(self, error_type: str) -> List[ErrorInfo]:
        """Get all errors of a specific type"""
        return [error for error in self.errors if error.error_type == error_type]
    
    def get_errors_by_severity(self, severity: str) -> List[ErrorInfo]:
        """Get all errors of a specific severity"""
        return [error for error in self.errors if error.severity == severity]
    
    def export_errors(self, filepath: Optional[Path] = None) -> Path:
        """Export errors to JSON file"""
        if filepath is None:
            filepath = Path("error_log.json")
        
        errors_data = []
        for error in self.errors:
            errors_data.append({
                'error_type': error.error_type,
                'message': error.message,
                'timestamp': error.timestamp.isoformat(),
                'traceback': error.traceback,
                'context': error.context,
                'metadata': error.metadata,
                'severity': error.severity,
                'recoverable': error.recoverable
            })
        
        with open(filepath, 'w') as f:
            json.dump(errors_data, f, indent=2)
        
        logger.info(f"Exported {len(errors_data)} errors to {filepath}")
        return filepath
    
    def clear_errors(self) -> None:
        """Clear all errors"""
        self.errors.clear()
        self.error_counts.clear()
        logger.info("Error log cleared")
    
    def get_recovery_suggestions(self, error_type: str) -> List[str]:
        """Get recovery suggestions for a specific error type"""
        suggestions = {
            'DiagramGenerationError': [
                "Check if the repository structure is valid",
                "Verify that all required dependencies are installed",
                "Ensure sufficient disk space for temporary files",
                "Check network connectivity for external resources"
            ],
            'StaticAnalysisError': [
                "Verify that the codebase is accessible",
                "Check if the programming language is supported",
                "Ensure that analysis tools are properly configured",
                "Verify file permissions for the repository"
            ],
            'AgentError': [
                "Check API key configuration",
                "Verify network connectivity to AI services",
                "Ensure sufficient API quota",
                "Check if the AI service is available"
            ],
            'RepositoryError': [
                "Verify repository URL and access permissions",
                "Check if the repository exists and is accessible",
                "Ensure Git is properly configured",
                "Verify authentication credentials"
            ],
            'ConfigurationError': [
                "Check configuration file format",
                "Verify all required configuration parameters",
                "Ensure configuration file is readable",
                "Check for typos in configuration values"
            ],
            'ValidationError': [
                "Verify input data format",
                "Check if all required fields are provided",
                "Ensure data types match expected formats",
                "Validate data ranges and constraints"
            ],
            'NetworkError': [
                "Check internet connectivity",
                "Verify firewall settings",
                "Check if the target service is available",
                "Retry the operation after a delay"
            ],
            'FileSystemError': [
                "Check file and directory permissions",
                "Ensure sufficient disk space",
                "Verify file paths are correct",
                "Check if files are not locked by other processes"
            ]
        }
        
        return suggestions.get(error_type, ["No specific suggestions available"])


# Global error handler instance
_global_error_handler: Optional[ErrorHandler] = None


def get_global_error_handler() -> ErrorHandler:
    """Get the global error handler instance"""
    global _global_error_handler
    if _global_error_handler is None:
        _global_error_handler = ErrorHandler()
    return _global_error_handler


def handle_error(error: Exception, context: Optional[Dict[str, Any]] = None,
                severity: str = "error", recoverable: bool = True) -> ErrorInfo:
    """Handle an error using the global error handler"""
    handler = get_global_error_handler()
    return handler.handle_error(error, context, severity, recoverable)


def error_handler(severity: str = "error", recoverable: bool = True, 
                 context: Optional[Dict[str, Any]] = None):
    """Decorator for automatic error handling"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_context = context or {}
                error_context.update({
                    'function': func.__name__,
                    'module': func.__module__,
                    'args': str(args)[:200],  # Limit args length
                    'kwargs': str(kwargs)[:200]  # Limit kwargs length
                })
                
                handle_error(e, error_context, severity, recoverable)
                raise
        
        return wrapper
    return decorator


def safe_execute(func: Callable, *args, **kwargs) -> tuple[Optional[Any], Optional[ErrorInfo]]:
    """Safely execute a function and return result and error info"""
    try:
        result = func(*args, **kwargs)
        return result, None
    except Exception as e:
        error_info = handle_error(e, {
            'function': func.__name__,
            'module': func.__module__,
            'args': str(args)[:200],
            'kwargs': str(kwargs)[:200]
        })
        return None, error_info


class DiagramGenerationErrorHandler:
    """Specialized error handler for diagram generation operations"""
    
    def __init__(self):
        self.handler = get_global_error_handler()
        self.component_errors: Dict[str, List[ErrorInfo]] = {}
        self.agent_errors: Dict[str, List[ErrorInfo]] = {}
    
    def handle_component_error(self, component_name: str, error: Exception, 
                              context: Optional[Dict[str, Any]] = None) -> ErrorInfo:
        """Handle component-specific errors"""
        error_context = context or {}
        error_context['component'] = component_name
        error_context['operation'] = 'component_analysis'
        
        error_info = self.handler.handle_error(error, error_context, "error", True)
        
        if component_name not in self.component_errors:
            self.component_errors[component_name] = []
        self.component_errors[component_name].append(error_info)
        
        return error_info
    
    def handle_agent_error(self, agent_name: str, operation: str, error: Exception,
                          context: Optional[Dict[str, Any]] = None) -> ErrorInfo:
        """Handle agent-specific errors"""
        error_context = context or {}
        error_context['agent'] = agent_name
        error_context['operation'] = operation
        
        error_info = self.handler.handle_error(error, error_context, "error", True)
        
        agent_key = f"{agent_name}_{operation}"
        if agent_key not in self.agent_errors:
            self.agent_errors[agent_key] = []
        self.agent_errors[agent_key].append(error_info)
        
        return error_info
    
    def get_diagram_error_stats(self) -> Dict[str, Any]:
        """Get diagram generation specific error statistics"""
        return {
            'component_errors': {
                'count': sum(len(errors) for errors in self.component_errors.values()),
                'by_component': {name: len(errors) for name, errors in self.component_errors.items()}
            },
            'agent_errors': {
                'count': sum(len(errors) for errors in self.agent_errors.values()),
                'by_agent': {name: len(errors) for name, errors in self.agent_errors.items()}
            }
        }


class StaticAnalysisErrorHandler:
    """Specialized error handler for static analysis operations"""
    
    def __init__(self):
        self.handler = get_global_error_handler()
        self.scan_errors: Dict[str, List[ErrorInfo]] = {}
        self.analysis_errors: Dict[str, List[ErrorInfo]] = {}
    
    def handle_scan_error(self, repo_name: str, error: Exception,
                         context: Optional[Dict[str, Any]] = None) -> ErrorInfo:
        """Handle repository scan errors"""
        error_context = context or {}
        error_context['repository'] = repo_name
        error_context['operation'] = 'repository_scan'
        
        error_info = self.handler.handle_error(error, error_context, "error", True)
        
        if repo_name not in self.scan_errors:
            self.scan_errors[repo_name] = []
        self.scan_errors[repo_name].append(error_info)
        
        return error_info
    
    def handle_analysis_error(self, analysis_type: str, error: Exception,
                             context: Optional[Dict[str, Any]] = None) -> ErrorInfo:
        """Handle analysis errors"""
        error_context = context or {}
        error_context['analysis_type'] = analysis_type
        error_context['operation'] = 'analysis'
        
        error_info = self.handler.handle_error(error, error_context, "error", True)
        
        if analysis_type not in self.analysis_errors:
            self.analysis_errors[analysis_type] = []
        self.analysis_errors[analysis_type].append(error_info)
        
        return error_info
    
    def get_analysis_error_stats(self) -> Dict[str, Any]:
        """Get static analysis specific error statistics"""
        return {
            'scan_errors': {
                'count': sum(len(errors) for errors in self.scan_errors.values()),
                'by_repository': {name: len(errors) for name, errors in self.scan_errors.items()}
            },
            'analysis_errors': {
                'count': sum(len(errors) for errors in self.analysis_errors.values()),
                'by_type': {name: len(errors) for name, errors in self.analysis_errors.items()}
            }
        }


# Utility functions for easy access
def get_error_stats() -> Dict[str, Any]:
    """Get comprehensive error statistics"""
    handler = get_global_error_handler()
    return handler.get_error_stats()


def export_error_log(filepath: Optional[Path] = None) -> Path:
    """Export error log to file"""
    handler = get_global_error_handler()
    return handler.export_errors(filepath)


def clear_error_log() -> None:
    """Clear error log"""
    handler = get_global_error_handler()
    handler.clear_errors()


def get_recovery_suggestions(error_type: str) -> List[str]:
    """Get recovery suggestions for an error type"""
    handler = get_global_error_handler()
    return handler.get_recovery_suggestions(error_type)


def setup_error_handling() -> None:
    """Setup global error handling"""
    handler = get_global_error_handler()
    
    # Register recovery strategies
    handler.register_recovery_strategy('DiagramGenerationError', _recover_diagram_generation)
    handler.register_recovery_strategy('StaticAnalysisError', _recover_static_analysis)
    handler.register_recovery_strategy('AgentError', _recover_agent_error)
    handler.register_recovery_strategy('RepositoryError', _recover_repository_error)
    handler.register_recovery_strategy('NetworkError', _recover_network_error)
    
    # Add error filters
    handler.add_error_filter(lambda e: e.severity == "info")  # Filter out info level errors
    
    logger.info("Error handling setup completed")


def _recover_diagram_generation(error_info: ErrorInfo) -> None:
    """Recovery strategy for diagram generation errors"""
    logger.info("Attempting to recover from diagram generation error")
    # Add specific recovery logic here


def _recover_static_analysis(error_info: ErrorInfo) -> None:
    """Recovery strategy for static analysis errors"""
    logger.info("Attempting to recover from static analysis error")
    # Add specific recovery logic here


def _recover_agent_error(error_info: ErrorInfo) -> None:
    """Recovery strategy for agent errors"""
    logger.info("Attempting to recover from agent error")
    # Add specific recovery logic here


def _recover_repository_error(error_info: ErrorInfo) -> None:
    """Recovery strategy for repository errors"""
    logger.info("Attempting to recover from repository error")
    # Add specific recovery logic here


def _recover_network_error(error_info: ErrorInfo) -> None:
    """Recovery strategy for network errors"""
    logger.info("Attempting to recover from network error")
    # Add specific recovery logic here
