import unittest
from pathlib import Path

from agents.tools import CodeReferenceReader
from static_analyzer import StaticAnalyzer


class TestReadSourceTool(unittest.TestCase):
    def setUp(self):
        # Set up any necessary state or mocks before each test
        analyzer = StaticAnalyzer(Path("./repos/django"))
        static_analysis = analyzer.analyze()
        self.tool = CodeReferenceReader(static_analysis=static_analysis)

    def test_read_method(self):
        # Test the _run method with a valid package
        content = self.tool._run("repos.django.django.core.servers.basehttp.ThreadedWSGIServer.process_request_thread")
        self.assertIn("97:    def process_request_thread(self, request, client_address):", content)
        self.assertIn("103:", content)
        self.assertNotIn("104:", content)

    def test_read_class(self):
        content = self.tool._run("django.django.core.handlers.base.BaseHandler")
        self.assertIsInstance(content, str)
        self.assertIn("20:class BaseHandler:", content)
        self.assertIn("365:", content)
        self.assertNotIn("366:", content)

    def test_read_function(self):
        # Test reading a file for an existing package
        content = self.tool._run("django.django.core.files.images.get_image_dimensions")
        self.assertIsInstance(content, str)
        self.assertIn("35:def get_image_dimensions(file_or_path, close=False):", content)
        self.assertIn("89:", content)

    def test_read_invalid_reference(self):
        # Test reading a file for a non-existing package
        error_msgs = self.tool._run("repos.non_existing_package")
        self.assertIn(
            "Error: The specified python element 'repos.non_existing_package' was not found in the indexed source files.",
            error_msgs,
        )
