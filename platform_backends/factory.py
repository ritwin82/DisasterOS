import os
import sys

from platform_backends.linux_backend import LinuxBackend
from platform_backends.windows_backend import WindowsBackend


def create_backend(simulator=None, selection=None, host=None):
    host = host or sys.platform
    expected = "windows" if host == "win32" else "linux" if host.startswith("linux") else None
    if expected is None:
        raise RuntimeError(f"Unsupported host platform: {host}")
    chosen = (selection or os.getenv("DISASTEROS_BACKEND") or expected).lower()
    if chosen != expected:
        raise RuntimeError(f"Backend '{chosen}' is incompatible with host '{host}'; refusing to fall back")
    return WindowsBackend(simulator) if chosen == "windows" else LinuxBackend(simulator)
