"""Application-owned bounded child workload. Never targets host processes."""
import argparse
import time


def run(mode: str, seconds: int, memory_mb: int):
    end = time.monotonic() + min(max(seconds, 1), 30)
    reserved = bytearray(min(max(memory_mb, 1), 64) * 1024 * 1024) if mode in ("memory", "combined") else None
    while time.monotonic() < end:
        if mode in ("cpu", "combined"):
            burst = time.monotonic() + 0.08
            while time.monotonic() < burst:
                sum(i * i for i in range(300))
            time.sleep(0.08)
        else:
            time.sleep(0.2)
    del reserved


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["cpu", "memory", "combined", "service"])
    parser.add_argument("--seconds", type=int, default=25)
    parser.add_argument("--memory-mb", type=int, default=64)
    args = parser.parse_args()
    if args.mode == "service":
        while True:
            time.sleep(1)
    else:
        run(args.mode, args.seconds, args.memory_mb)
