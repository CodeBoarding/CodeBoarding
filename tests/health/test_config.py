import json
import tempfile
import unittest
from pathlib import Path

from health.config import initialize_health_dir, load_health_config


class TestInitializeHealthDir(unittest.TestCase):
    def test_creates_both_files(self):
        """Test that initialize_health_dir creates .healthignore and health_config.json."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            initialize_health_dir(health_dir)

            healthignore_path = health_dir / ".healthignore"
            self.assertTrue(healthignore_path.exists())
            content = healthignore_path.read_text()
            self.assertIn("Health Check Exclusion Patterns", content)

            config_path = health_dir / "health_config.json"
            self.assertTrue(config_path.exists())
            data = json.loads(config_path.read_text())
            self.assertIn("function_size_max", data)
            self.assertEqual(data["function_size_max"]["value"], 150)
            self.assertIn("description", data["function_size_max"])

    def test_idempotent(self):
        """Test that calling initialize multiple times doesn't overwrite existing files."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            initialize_health_dir(health_dir)

            # Modify both files
            healthignore_path = health_dir / ".healthignore"
            with open(healthignore_path, "a") as f:
                f.write("\nevals.*\n")
            custom_ignore = healthignore_path.read_text()

            config_path = health_dir / "health_config.json"
            data = json.loads(config_path.read_text())
            data["function_size_max"]["value"] = 200
            config_path.write_text(json.dumps(data))

            # Second initialization should not overwrite
            initialize_health_dir(health_dir)

            self.assertEqual(healthignore_path.read_text(), custom_ignore)
            data_after = json.loads(config_path.read_text())
            self.assertEqual(data_after["function_size_max"]["value"], 200)


class TestLoadHealthConfig(unittest.TestCase):
    def test_defaults_when_no_file(self):
        """Test that missing health_config.json returns default config."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            health_dir.mkdir(parents=True)

            config = load_health_config(health_dir)
            self.assertEqual(config.function_size_max, 150)
            self.assertEqual(config.fan_out_max, 10)
            self.assertAlmostEqual(config.instability_high, 0.8)

    def test_partial_overrides(self):
        """Test loading config with only some fields overridden."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            health_dir.mkdir(parents=True)

            config_path = health_dir / "health_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "function_size_max": {"description": "...", "value": 200},
                        "fan_out_max": {"description": "...", "value": 20},
                    }
                )
            )

            config = load_health_config(health_dir)
            self.assertEqual(config.function_size_max, 200)
            self.assertEqual(config.fan_out_max, 20)
            # Non-overridden fields should have defaults
            self.assertEqual(config.fan_in_max, 10)
            self.assertEqual(config.inheritance_depth_max, 5)

    def test_falls_back_on_invalid_json(self):
        """Test that malformed JSON falls back to defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            health_dir.mkdir(parents=True)

            config_path = health_dir / "health_config.json"
            config_path.write_text("{ invalid json !!!")

            config = load_health_config(health_dir)
            self.assertEqual(config.function_size_max, 150)

    def test_falls_back_on_invalid_values(self):
        """Test that invalid field values fall back to defaults."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            health_dir.mkdir(parents=True)

            config_path = health_dir / "health_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "function_size_max": {"description": "...", "value": "not_a_number"},
                    }
                )
            )

            config = load_health_config(health_dir)
            self.assertEqual(config.function_size_max, 150)

    def test_merges_healthignore_patterns(self):
        """Test that .healthignore patterns are merged into loaded config."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            health_dir.mkdir(parents=True)

            (health_dir / ".healthignore").write_text("evals.*\nutils.*\n")

            config_path = health_dir / "health_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "function_size_max": {"description": "...", "value": 300},
                    }
                )
            )

            config = load_health_config(health_dir)
            self.assertEqual(config.function_size_max, 300)
            self.assertEqual(config.orphan_exclude_patterns, ["evals.*", "utils.*"])

    def test_healthignore_patterns_without_config_json(self):
        """Test that .healthignore patterns load even without health_config.json."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            health_dir.mkdir(parents=True)

            (health_dir / ".healthignore").write_text("evals.*\n*.test\n")

            config = load_health_config(health_dir)
            self.assertEqual(config.function_size_max, 150)  # default
            self.assertEqual(config.orphan_exclude_patterns, ["evals.*", "*.test"])

    def test_with_template_file(self):
        """Test that the generated template file loads correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            health_dir = Path(tmp) / "health"
            initialize_health_dir(health_dir)

            config = load_health_config(health_dir)
            self.assertEqual(config.function_size_max, 150)
            self.assertEqual(config.fan_out_max, 10)
            self.assertEqual(config.fan_in_max, 10)
            self.assertEqual(config.god_class_method_count_max, 25)
            self.assertEqual(config.god_class_loc_max, 400)
            self.assertEqual(config.god_class_fan_out_max, 30)
            self.assertEqual(config.inheritance_depth_max, 5)
            self.assertEqual(config.max_cycles_reported, 50)
            self.assertAlmostEqual(config.instability_high, 0.8)
            self.assertAlmostEqual(config.cohesion_low, 0.1)


if __name__ == "__main__":
    unittest.main()
