"""Launch Streamlit inside an explicitly delegated user cgroup v2 scope.

Run under: systemd-run --user --scope -p Delegate=yes -- python linux_cgroup_launcher.py
This helper moves only itself into a child cgroup so cpu/memory controllers can
be enabled on the empty delegated parent. It never requests root privileges.
"""
import os
import subprocess
import sys
from pathlib import Path


MOUNT = Path("/sys/fs/cgroup")


def delegated_root():
    if not sys.platform.startswith("linux"):
        raise RuntimeError("This launcher runs only on Linux")
    entries = Path("/proc/self/cgroup").read_text().splitlines()
    paths = [line.split("::", 1)[1] for line in entries if line.startswith("0::")]
    if len(paths) != 1:
        raise RuntimeError("cgroups v2 membership was not found")
    root = (MOUNT / paths[0].lstrip("/")).resolve(strict=True)
    if root == MOUNT or not root.is_relative_to(MOUNT):
        raise RuntimeError("Current process is not inside a dedicated cgroup scope")
    if not (root / "cgroup.controllers").exists():
        raise RuntimeError("Delegated cgroups v2 controllers are unavailable")
    return root


def main():
    root = delegated_root()
    available = set((root / "cgroup.controllers").read_text().split())
    if not {"cpu", "memory"}.issubset(available):
        raise RuntimeError("This scope does not delegate both cpu and memory controllers")
    manager = root / "disasteros-manager"
    if manager.exists():
        raise RuntimeError("Manager cgroup already exists; use a fresh delegated scope")
    manager.mkdir()
    try:
        (manager / "cgroup.procs").write_text(str(os.getpid()))
        if str(os.getpid()) not in (manager / "cgroup.procs").read_text().split():
            raise RuntimeError("Could not move launcher into its manager cgroup")
        (root / "cgroup.subtree_control").write_text("+cpu +memory")
        enabled = set((root / "cgroup.subtree_control").read_text().split())
        if not {"cpu", "memory"}.issubset(enabled):
            raise RuntimeError("Could not enable delegated controllers")
        environment = dict(os.environ, DISASTEROS_BACKEND="linux", DISASTEROS_CGROUP_ROOT=str(root))
        return subprocess.run([sys.executable, "-m", "streamlit", "run", "app.py"],
                              env=environment, check=False).returncode
    finally:
        try:
            manager.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(f"DisasterOS delegated launch unavailable: {error}", file=sys.stderr)
        raise SystemExit(1)
