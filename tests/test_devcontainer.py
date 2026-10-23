import json
import re
import shutil
import subprocess
import tomllib
import unittest
from pathlib import Path

import yaml

from infra.constants import CODEBOARDING_DIR_NAME
from infra.tool_registry.paths import MINIMUM_NODE_MAJOR_VERSION
from infra.tool_registry.registry import PINNED_NODE_VERSION

REPO_ROOT = Path(__file__).parent.parent
DEVCONTAINER_DIR = REPO_ROOT / ".devcontainer"
LOCAL_DEVCONTAINER_DIR = DEVCONTAINER_DIR / "local"
DEVCONTAINER_JSON = DEVCONTAINER_DIR / "devcontainer.json"
POST_CREATE_SH = DEVCONTAINER_DIR / "post-create.sh"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "devcontainer.yml"


def _load_ci_workflow() -> dict:
    with CI_WORKFLOW.open() as f:
        return yaml.safe_load(f)


def _required_python_minor() -> int:
    """Return the minor version from pyproject.toml requires-python (e.g. 12 for '>=3.12,<3.13')."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        requires = tomllib.load(f)["project"]["requires-python"]
    match = re.search(r">=3\.(\d+)", requires)
    assert match is not None, f"Unexpected requires-python: {requires}"
    return int(match.group(1))


def _load_devcontainer() -> dict:
    return json.loads(DEVCONTAINER_JSON.read_text(encoding="utf-8"))


def _load_local_devcontainer() -> dict:
    with open(LOCAL_DEVCONTAINER_DIR / "devcontainer.json") as f:
        return json.load(f)


def _bind_sources(config: dict) -> list[str]:
    """Return the source of every type=bind mount, whether written as an object or a key=value string."""
    sources = []
    for m in config.get("mounts", []):
        if isinstance(m, str):
            parsed = dict(p.split("=", 1) for p in m.split(",") if "=" in p)
            if parsed.get("type") == "bind":
                sources.append(parsed["source"])
        elif m.get("type") == "bind":
            sources.append(m["source"])
    return sources


class TestDevcontainer(unittest.TestCase):
    def test_config_is_strict_json(self):
        self.assertIsInstance(_load_devcontainer(), dict)

    def test_image_matches_required_python_and_bookworm(self):
        image = _load_devcontainer()["image"]
        self.assertIn(f"-3.{_required_python_minor()}-", image)
        self.assertTrue(image.endswith("-bookworm"), image)

    def test_node_feature_matches_pinned_runtime(self):
        features = _load_devcontainer()["features"]
        node_key = next(key for key in features if key.startswith("ghcr.io/devcontainers/features/node:"))
        node = features[node_key]
        major = int(node["version"].split(".")[0])
        self.assertEqual(major, int(PINNED_NODE_VERSION.split(".")[0]))
        self.assertGreaterEqual(major, MINIMUM_NODE_MAJOR_VERSION)

    def test_uv_feature_present(self):
        features = _load_devcontainer()["features"]
        self.assertTrue(any(key.startswith("ghcr.io/devcontainers-extra/features/uv") for key in features))

    def test_user_data_dir_is_volume(self):
        """Named volume persists language servers and config.toml across container rebuilds."""
        mounts = _load_devcontainer()["mounts"]
        self.assertTrue(
            any(
                isinstance(m, dict) and m["type"] == "volume" and m["target"].endswith(CODEBOARDING_DIR_NAME)
                for m in mounts
            ),
            mounts,
        )

    def test_uv_project_environment(self):
        env = _load_devcontainer()["containerEnv"]
        self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], "/home/vscode/.venv")

    def test_container_env(self):
        env = _load_devcontainer()["containerEnv"]
        self.assertEqual(env["DO_NOT_TRACK"], "1")
        self.assertEqual(env["UV_LINK_MODE"], "copy")

    def test_post_create_command_points_at_existing_script(self):
        command = _load_devcontainer()["postCreateCommand"]
        self.assertIn(".devcontainer/post-create.sh", command)
        self.assertTrue(POST_CREATE_SH.is_file())

    def test_post_create_script_order(self):
        script = POST_CREATE_SH.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", script)
        self.assertIn(f"sys.version_info >= (3, {_required_python_minor()})", script)
        safe_dir = script.index("safe.directory")
        uv_sync = script.index("uv sync")
        setup = script.index("uv run codeboarding-setup")
        hook = script.index("uv run pre-commit install")
        self.assertLess(safe_dir, uv_sync)
        self.assertLess(uv_sync, setup)
        self.assertLess(setup, hook)
        hook_check_idx = script.index('if [[ ! -s "$hook" ]]')
        self.assertLess(hook, hook_check_idx)
        exit1_idx = script.find("exit 1", hook_check_idx)
        self.assertNotEqual(exit1_idx, -1, "hook check must exit 1 on failure")

    @unittest.skipIf(shutil.which("bash") is None, "bash not available")
    def test_post_create_script_bash_syntax(self):
        result = subprocess.run(["bash", "-n", str(POST_CREATE_SH)], check=False)
        self.assertEqual(result.returncode, 0)

    def test_recommended_extensions(self):
        extensions = _load_devcontainer()["customizations"]["vscode"]["extensions"]
        self.assertIn("ms-python.black-formatter", extensions)
        self.assertIn("ms-python.mypy-type-checker", extensions)


def test_ci_workflow_builds_devcontainer_with_light_setup() -> None:
    workflow = _load_ci_workflow()
    steps = workflow["jobs"]["build"]["steps"]
    ci_step = next(s for s in steps if str(s.get("uses", "")).startswith("devcontainers/ci@"))
    assert ci_step["with"]["push"] == "never"
    assert "CODEBOARDING_DEVCONTAINER_CI=1" in ci_step["with"]["env"]
    assert "pytest tests/test_devcontainer.py" in ci_step["with"]["runCmd"]
    assert "command -v claude" in ci_step["with"]["runCmd"]


def test_default_config_has_no_host_bind_mounts() -> None:
    config = _load_devcontainer()
    assert _bind_sources(config) == [], "default config must have no host bind mounts (Codespaces-safe)"
    assert "initializeCommand" not in config, "default config must not have initializeCommand"


def test_local_config_mounts_claude_and_known_hosts_from_host() -> None:
    config = _load_local_devcontainer()
    sources = _bind_sources(config)
    assert "${localEnv:HOME}/.claude" in sources
    assert "${localEnv:HOME}/.ssh/known_hosts" in sources
    known_hosts_mounts = [m for m in config.get("mounts", []) if isinstance(m, str) and "known_hosts" in m]
    assert any("readonly" in m for m in known_hosts_mounts), "known_hosts bind must be readonly"
    assert any(
        "/etc/ssh/ssh_known_hosts" in m for m in known_hosts_mounts
    ), "known_hosts must be mounted at /etc/ssh/ssh_known_hosts"
    init_cmd = config.get("initializeCommand", "")
    assert isinstance(init_cmd, str), "initializeCommand must be a shell string"
    for source in sources:
        assert f'"{source}"' in init_cmd, f"{source} must appear quoted in initializeCommand"


def test_local_config_does_not_mount_ssh_private_keys() -> None:
    ssh_sources = [s for s in _bind_sources(_load_local_devcontainer()) if "/.ssh" in s]
    assert ssh_sources == [
        "${localEnv:HOME}/.ssh/known_hosts"
    ], "only known_hosts may be mounted from ~/.ssh; use agent forwarding for private keys"


def test_local_config_matches_default_except_host_mounts() -> None:
    default = _load_devcontainer()
    local = _load_local_devcontainer()
    ignored = {"name", "mounts", "initializeCommand"}
    assert {k: v for k, v in default.items() if k not in ignored} == {
        k: v for k, v in local.items() if k not in ignored
    }, "local config must match default except for name/mounts/initializeCommand"
    default_mounts = default.get("mounts", [])
    local_mounts = local.get("mounts", [])
    for m in default_mounts:
        assert m in local_mounts, f"default mount {m!r} must also be in local config"
    default_lock = (DEVCONTAINER_DIR / "devcontainer-lock.json").read_text()
    local_lock = (LOCAL_DEVCONTAINER_DIR / "devcontainer-lock.json").read_text()
    assert default_lock == local_lock, "lockfiles must be identical"


def test_ci_workflow_triggers_on_devcontainer_changes() -> None:
    workflow = _load_ci_workflow()
    # PyYAML parses 'on:' as bool True (YAML 1.1)
    triggers = workflow[True]
    for event in ("pull_request", "push"):
        paths = triggers[event]["paths"]
        for required in (
            ".devcontainer/**",
            "tests/test_devcontainer.py",
            "infra/tool_registry/registry.py",
            "infra/tool_registry/paths.py",
        ):
            assert required in paths, f"{event} paths missing {required}"


def test_ci_workflow_actions_pinned_to_sha() -> None:
    steps = _load_ci_workflow()["jobs"]["build"]["steps"]
    for step in steps:
        if "uses" in step:
            assert re.search(
                r"^[^@]+@[0-9a-f]{40}", step["uses"]
            ), f"{step['uses']} must be pinned to a full commit SHA"


def test_post_create_gates_heavy_downloads_on_ci_flag() -> None:
    script = (DEVCONTAINER_DIR / "post-create.sh").read_text()
    gate = 'if [[ "${CODEBOARDING_DEVCONTAINER_CI:-}" != "1" ]]; then'
    for guarded in ("uv run codeboarding-setup", "npm install -g @anthropic-ai/claude-code"):
        gate_pos = script.find(
            gate, script.find(gate) + 1 if guarded == "npm install -g @anthropic-ai/claude-code" else 0
        )
        assert gate_pos != -1, f"CI gate not found before: {guarded}"
        gate_end = script.find("\nfi\n", gate_pos)
        if gate_end == -1:
            gate_end = script.find("\nfi", gate_pos)
        assert gate_end != -1, f"Closing 'fi' not found after gate for: {guarded}"
        assert guarded in script[gate_pos:gate_end], f"{guarded!r} not found inside its CI gate body"
    gate_spans = []
    start = script.find(gate)
    while start != -1:
        end = script.find("\nfi\n", start)
        if end == -1:
            end = script.find("\nfi", start)
        assert end != -1, f"No closing fi found for gate at {start}"
        gate_spans.append((start, end))
        start = script.find(gate, end)
    for ungated in ("uv sync --dev --locked", "uv run pre-commit install --install-hooks"):
        pos = script.find(ungated)
        assert pos != -1, f"{ungated!r} not found in post-create.sh"
        assert not any(s <= pos < e for s, e in gate_spans), f"{ungated!r} must not be inside a CI gate"


def test_claude_cli_version_is_pinned() -> None:
    script = (DEVCONTAINER_DIR / "post-create.sh").read_text()
    assert re.search(
        r'^CLAUDE_CODE_VERSION="\d+\.\d+\.\d+"$', script, re.M
    ), "CLAUDE_CODE_VERSION pin missing from post-create.sh"
    assert (
        'npm install -g @anthropic-ai/claude-code@"${CLAUDE_CODE_VERSION}"' in script
    ), "Claude CLI install must use the pinned CLAUDE_CODE_VERSION variable"
