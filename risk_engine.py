from collections import deque


LEVELS = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


class RiskEngine:
    def __init__(self):
        self.ram_history = deque(maxlen=12)
        self.cpu_history = deque(maxlen=12)
        self.disk_history = deque(maxlen=12)

    def assess(self, metrics, anomaly=None):
        cpu, ram, disk = (float(metrics[key]) for key in ("cpu", "ram", "disk"))
        self.ram_history.append(ram)
        self.cpu_history.append(cpu)
        self.disk_history.append(disk)
        resource = {
            "CPU": "CRITICAL" if cpu >= 95 else "HIGH" if cpu >= 85 else "MEDIUM" if cpu >= 70 else "LOW",
            "Memory": "CRITICAL" if ram >= 95 else "HIGH" if ram >= 85 else "MEDIUM" if ram >= 75 else "LOW",
            "Disk": "CRITICAL" if disk >= 97 else "HIGH" if disk >= 90 else "MEDIUM" if disk >= 80 else "LOW",
        }
        reasons = [f"{name} at {metrics[name.lower() if name != 'Memory' else 'ram']:.1f}%" for name, level in resource.items() if LEVELS[level] >= 1]
        if len(self.ram_history) >= 4 and ram >= 65 and ram - self.ram_history[0] >= 8 and LEVELS[resource["Memory"]] < 2:
            resource["Memory"] = "HIGH"
            reasons.append("Memory is rising rapidly")
        if len(self.cpu_history) >= 4 and sum(self.cpu_history) / len(self.cpu_history) >= 80 and LEVELS[resource["CPU"]] < 2:
            resource["CPU"] = "HIGH"
            reasons.append("Rolling CPU average is high")
        if len(self.disk_history) >= 4 and disk >= 75 and disk - self.disk_history[0] >= 5 and LEVELS[resource["Disk"]] < 2:
            resource["Disk"] = "HIGH"
            reasons.append("Disk usage is rising rapidly")
        worst = max(LEVELS[level] for level in resource.values())
        if sum(LEVELS[level] >= 2 for level in resource.values()) >= 2:
            worst = min(3, worst + 1)
            reasons.append("Multiple resources are under pressure")
        if anomaly and anomaly.get("is_anomaly"):
            reasons.append("ML model found unusual metric combination")
            worst = max(worst, 1)
        if anomaly and anomaly.get("score") is not None and anomaly["score"] < -0.08:
            worst = max(worst, 2)
            reasons.append("Strong anomaly score raises review priority")
        category = next(name for name, value in LEVELS.items() if value == worst)
        return {"category": category, "resources": resource, "reasons": reasons or ["Resources are within policy limits"],
                "method": "Rule-based risk estimate; ML anomaly is supporting evidence"}
