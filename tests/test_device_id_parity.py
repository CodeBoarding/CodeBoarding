"""Cross-language parity for the anonymous device id.

The OSS Python port (``telemetry/device_id.py``) must produce the exact same
hash as the VSCode extension (``deviceFingerprint.ts``) so a standalone OSS run
can be matched to the same machine's VSCode usage. This runs both on the current
machine and asserts they agree. Skipped when ``node`` or the VSCode source isn't
available (e.g. PyPI-only checkouts or CI without the sibling repo).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from telemetry.device_id import generate_device_id

_FINGERPRINT_TS = (
    Path(__file__).resolve().parents[2] / "CodeBoarding-vscode" / "backend" / "services" / "deviceFingerprint.ts"
)


def _vscode_device_id() -> str:
    out = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            f"import({str(_FINGERPRINT_TS)!r}).then(m => process.stdout.write(m.computeDeviceId()))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return out.stdout.strip()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not _FINGERPRINT_TS.exists(), reason="VSCode deviceFingerprint.ts not available")
def test_python_and_vscode_device_id_match() -> None:
    try:
        vscode_id = _vscode_device_id()
    except subprocess.CalledProcessError as e:
        pytest.skip(f"node could not run deviceFingerprint.ts: {e.stderr}")

    assert generate_device_id() == vscode_id, "Python and VSCode device-id algorithms have drifted"
