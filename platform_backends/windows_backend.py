import sys

from platform_backends.base import PlatformBackend


class WindowsBackend(PlatformBackend):
    name = "windows"

    def __init__(self, simulator=None):
        if sys.platform != "win32":
            raise RuntimeError("Windows backend cannot execute on a non-Windows host")
        super().__init__(simulator)

    def get_platform_capabilities(self):
        managed = "SUPPORTED" if self.simulator is not None else "NOT_CONFIGURED"
        return {
            "host_metrics": {"status": "SUPPORTED", "reason": "psutil host measurements"},
            "managed_process_recovery": {"status": managed, "reason": "Application-owned children only"},
            "cpu_quota": {"status": "UNSUPPORTED", "reason": "No host quota adapter configured"},
            "memory_limit": {"status": "UNSUPPORTED", "reason": "No host memory controller configured"},
            "systemd": {"status": "NOT_APPLICABLE", "reason": "Linux-only service manager"},
        }
