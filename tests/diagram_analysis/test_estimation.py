import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import logging
import io

from static_analyzer.programming_language import ProgrammingLanguage

class TestEstimationLogging(unittest.TestCase):
    @patch("static_analyzer.scanner.ProjectScanner")
    def test_logging_format(self, mock_scanner_class):
        """Test that the logging format doesn't change for estimation."""
        # Clear any cached modules to ensure we get a fresh import
        import sys
        if "diagram_analysis.estimation" in sys.modules:
            del sys.modules["diagram_analysis.estimation"]
            
        # Mock diagram_analysis.__init__ content by patching its entries in sys.modules
        mock_pkg = MagicMock()
        mock_pkg.__path__ = ["diagram_analysis"]
        with patch.dict(sys.modules, {
            "diagram_analysis.diagram_generator": MagicMock(),
            "diagram_analysis": mock_pkg
        }):
            from diagram_analysis.estimation import estimate_pipeline_time
            
            # Setup mock for scanner.scan()
            mock_scanner = MagicMock()
            mock_scanner_class.return_value = mock_scanner
            
            # Create some mock programming languages
            py_lang = ProgrammingLanguage(language="python", size=1000, percentage=100.0, suffixes=[".py"])
            mock_scanner.scan.return_value = [py_lang]
            
            # Patch the logger object in the module - need to use the real module now
            import diagram_analysis.estimation
            with patch.object(diagram_analysis.estimation, "logger") as mock_logger:
                # Call the function
                estimate_pipeline_time(source=Path("/tmp/fake_repo"), depth_level=2)
                
                # Verify logger.info was called with the expected format
                mock_logger.info.assert_called_once()
                log_message = mock_logger.info.call_args[0][0]
                
                # Expected: "Estimated pipeline time: X.X minutes (based on 1,000 LOC, depth level: 2, effective multiplier: 1.00)"
                expected_start = "Estimated pipeline time: "
                self.assertTrue(log_message.startswith(expected_start), f"Log message '{log_message}' does not start with expected prefix")
                
                self.assertIn("minutes (based on 1,000 LOC, depth level: 2, effective multiplier: 1.00)", log_message)
                
                import re
                time_pattern = r"Estimated pipeline time: \d+\.\d+ minutes"
                self.assertTrue(re.search(time_pattern, log_message), f"Log message '{log_message}' does not match expected time pattern")

    @patch("static_analyzer.scanner.ProjectScanner")
    def test_logging_format_different_values(self, mock_scanner_class):
        """Test logging format with different LOC and depth values."""
        # Clear any cached modules to ensure we get a fresh import
        import sys
        if "diagram_analysis.estimation" in sys.modules:
            del sys.modules["diagram_analysis.estimation"]
            
        # Mock diagram_analysis.__init__ content by patching its entries in sys.modules
        mock_pkg = MagicMock()
        mock_pkg.__path__ = ["diagram_analysis"]
        with patch.dict(sys.modules, {
            "diagram_analysis.diagram_generator": MagicMock(),
            "diagram_analysis": mock_pkg
        }):
            from diagram_analysis.estimation import estimate_pipeline_time
            
            # Setup mock for scanner.scan() with different values to check formatting
            mock_scanner = MagicMock()
            mock_scanner_class.return_value = mock_scanner
            
            # Java has a 2.0 multiplier
            java_lang = ProgrammingLanguage(language="java", size=50000, percentage=100.0, suffixes=[".java"])
            mock_scanner.scan.return_value = [java_lang]
            
            # Patch the logger object in the module
            import diagram_analysis.estimation
            with patch.object(diagram_analysis.estimation, "logger") as mock_logger:
                # Call with depth level 3 (multiplier 2.0)
                estimate_pipeline_time(source=Path("/tmp/fake_repo"), depth_level=3)
                
                # Verify logger.info was called with the expected format
                mock_logger.info.assert_called_once()
                log_message = mock_logger.info.call_args[0][0]
                
                self.assertIn("(based on 50,000 LOC, depth level: 3, effective multiplier: 2.00)", log_message)

if __name__ == "__main__":
    unittest.main()
