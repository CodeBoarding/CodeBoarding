"""Anonymous device id, byte-for-byte compatible with the VSCode extension.

This is a Python port of ``LicenseService.generateDeviceId`` in the VSCode
extension (``backend/services/deviceFingerprint.ts``). Keeping the two in
lockstep lets a standalone OSS run be matched to the same machine's VSCode usage
in analytics. The parity test (in the VSCode project) runs both implementations
and asserts they produce the same hash.

Algorithm (must not drift from the TS side):
    device_id = sha256( strip_whitespace(anchor).lower() )
    anchor    = f"{system_uuid}-{disk_serial}-{raw_cpu_model}"
where ``system_uuid`` and ``disk_serial`` are themselves sha256 hex digests of
platform fingerprints, and ``raw_cpu_model`` is the *unhashed* CPU brand string.

On Linux everything is read natively (files + /proc/cpuinfo). On macOS/Windows
the identifiers live in IOKit/WMI, which have no Python-stdlib equivalent, so we
read them from the same OS tools the extension uses (ioreg/sysctl, PowerShell)
to keep the hashes identical.
"""

import getpass
import hashlib
import platform
import re
import socket
import subprocess
from pathlib import Path


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _shell(cmd: str) -> str:
    """Run an OS tool and return stdout (macOS/Windows only — see module docs)."""
    return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)


def _linux_machine_id() -> str:
    """Mirror ``cat /var/lib/dbus/machine-id || cat /etc/machine-id``."""
    try:
        return Path("/var/lib/dbus/machine-id").read_text()
    except OSError:
        return Path("/etc/machine-id").read_text()


def _system_uuid() -> str:
    try:
        system = platform.system()
        if system == "Windows":
            result = _shell(
                "powershell -NoProfile -NonInteractive -Command "
                '"Get-CimInstance Win32_ComputerSystemProduct | Select-Object -ExpandProperty UUID"'
            ).strip()
        elif system == "Darwin":
            result = _shell("ioreg -rd1 -c IOPlatformExpertDevice | grep -E '(UUID | serial-number)'").strip()
        else:  # Linux
            result = _linux_machine_id().strip()
        clean = re.sub(r"UUID", "", result, flags=re.IGNORECASE).strip()
        return _sha256(clean)
    except Exception:
        return _sha256("default-system-uuid")


def _disk_serial() -> str:
    try:
        system = platform.system()
        if system == "Windows":
            result = _shell(
                "powershell -NoProfile -NonInteractive -Command "
                '"Get-CimInstance Win32_DiskDrive | Select-Object -First 1 -ExpandProperty SerialNumber"'
            )
        elif system == "Darwin":
            result = _shell("ioreg -l | grep IOPlatformSerialNumber | awk '{print $4}'")
        else:  # Linux
            result = Path("/etc/machine-id").read_text()
        clean = re.sub(r"SerialNumber|UUID", "", result, flags=re.IGNORECASE).strip()
        return _sha256(clean)
    except Exception:
        return "fallback-disk-" + socket.gethostname()


def _raw_cpu_model() -> str:
    """Match Node's ``os.cpus()[0].model`` (the raw, unhashed brand string)."""
    try:
        system = platform.system()
        if system == "Windows":
            out = _shell(
                "powershell -NoProfile -NonInteractive -Command "
                '"(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)"'
            ).strip()
            return out or "unknown"
        if system == "Darwin":
            return _shell("sysctl -n machdep.cpu.brand_string").strip() or "unknown"
        # Linux: libuv reads the first "model name" line from /proc/cpuinfo
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        return "unknown"
    except Exception:
        return "unknown"


def generate_device_id() -> str:
    """Reproduce VSCode's ``LicenseService.generateDeviceId`` exactly."""
    try:
        anchor = f"{_system_uuid()}-{_disk_serial()}-{_raw_cpu_model()}"
        return _sha256(re.sub(r"\s", "", anchor).lower())
    except Exception:
        return _sha256(socket.gethostname() + getpass.getuser())
