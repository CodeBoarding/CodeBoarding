import tempfile
import unittest
from pathlib import Path

from health.config import load_health_exclude_patterns, initialize_healthignore


class TestHealthConfig(unittest.TestCase):
    def test_initialize_healthignore(self):
        """Test that .healthignore is created with the template."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            initialize_healthignore(health_dir)

            healthignore_path = health_dir / ".healthignore"
            self.assertTrue(healthignore_path.exists())

            content = healthignore_path.read_text()
            self.assertIn("Health Check Exclusion Patterns", content)
            self.assertIn("This file is automatically loaded", content)

    def test_load_health_exclude_patterns(self):
        """Test loading exclusion patterns from .healthignore."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            health_dir.mkdir(parents=True)

            # Create a .healthignore file with some patterns
            healthignore_path = health_dir / ".healthignore"
            healthignore_path.write_text("# Comment line\nevals.*\n\n  utils.get_*  \n*.test\n")

            patterns = load_health_exclude_patterns(health_dir)

            # Should skip comments and empty lines, and strip whitespace
            self.assertEqual(len(patterns), 3)
            self.assertIn("evals.*", patterns)
            self.assertIn("utils.get_*", patterns)
            self.assertIn("*.test", patterns)

    def test_load_health_exclude_patterns_nonexistent_dir(self):
        """Test that missing .healthignore returns empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            patterns = load_health_exclude_patterns(health_dir)
            self.assertEqual(patterns, [])

    def test_load_health_exclude_patterns_empty_file(self):
        """Test that empty .healthignore returns empty list."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            health_dir.mkdir(parents=True)

            healthignore_path = health_dir / ".healthignore"
            healthignore_path.write_text("# Only comments\n# More comments\n")

            patterns = load_health_exclude_patterns(health_dir)
            self.assertEqual(patterns, [])

    def test_initialize_healthignore_idempotent(self):
        """Test that calling initialize multiple times doesn't overwrite."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"

            # First initialization
            initialize_healthignore(health_dir)
            healthignore_path = health_dir / ".healthignore"
            original_content = healthignore_path.read_text()

            # Add custom content
            with open(healthignore_path, "a") as f:
                f.write("\n# Custom patterns\nevals.*\n")

            custom_content = healthignore_path.read_text()

            # Second initialization (should not overwrite)
            initialize_healthignore(health_dir)
            final_content = healthignore_path.read_text()

            self.assertEqual(final_content, custom_content)
            self.assertNotEqual(final_content, original_content)


if __name__ == "__main__":
    unittest.main()
