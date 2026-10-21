"""Stable cross-platform feature schema for the shared intelligence layer."""
import math

import numpy as np


BASE_FEATURE_NAMES = ("cpu", "ram", "swap", "disk")
FEATURE_NAMES = BASE_FEATURE_NAMES + (
    "cpu_mean", "cpu_std", "cpu_delta",
    "ram_mean", "ram_std", "ram_delta",
    "disk_mean", "disk_delta",
)


def normalize_metrics(sample):
    output = dict(sample)
    for name in BASE_FEATURE_NAMES:
        value = sample.get(name, 0.0)
        if value is None:
            raise ValueError(f"{name} is missing a numeric value")
        number = float(value)
        if not math.isfinite(number) or not 0 <= number <= 100:
            raise ValueError(f"{name} must be a finite percentage")
        output[name] = number
    for name in ("available_mb", "network_up", "network_down"):
        value = sample.get(name)
        output[name] = None if value is None else max(0.0, float(value))
    return output


def feature_vector(sample, history=None, window=10):
    normalized = normalize_metrics(sample)
    previous = [normalize_metrics(row) for row in list(history or [])[-max(1, int(window)):]]
    series = previous + [normalized]

    def values(name):
        return np.asarray([row[name] for row in series], dtype=float)

    cpu, ram, disk = values("cpu"), values("ram"), values("disk")
    prior = previous[-1] if previous else normalized
    derived = (
        float(cpu.mean()), float(cpu.std()), normalized["cpu"] - prior["cpu"],
        float(ram.mean()), float(ram.std()), normalized["ram"] - prior["ram"],
        float(disk.mean()), normalized["disk"] - prior["disk"],
    )
    return [normalized[name] for name in BASE_FEATURE_NAMES] + list(derived)
