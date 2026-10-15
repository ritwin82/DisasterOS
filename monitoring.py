import time
from datetime import datetime, timezone

import psutil


class Monitor:
    def __init__(self):
        self.previous_network = None
        self.previous_time = None
        psutil.cpu_percent(interval=None)

    def sample(self):
        now = time.monotonic()
        counters = psutil.net_io_counters()
        up = down = 0.0
        if counters and self.previous_network and self.previous_time:
            elapsed = max(now - self.previous_time, 0.001)
            up = max(0, counters.bytes_sent - self.previous_network.bytes_sent) / elapsed / 1024
            down = max(0, counters.bytes_recv - self.previous_network.bytes_recv) / elapsed / 1024
        self.previous_network, self.previous_time = counters, now
        memory = psutil.virtual_memory()
        return {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cpu": psutil.cpu_percent(interval=None),
            "ram": memory.percent,
            "available_mb": memory.available / 1024**2,
            "swap": psutil.swap_memory().percent,
            "disk": psutil.disk_usage(str(__import__('pathlib').Path.home().anchor)).percent,
            "network_up": up,
            "network_down": down,
        }
