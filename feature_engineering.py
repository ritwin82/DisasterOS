"""Stable cross-platform feature schema for the shared intelligence layer."""
import math


FEATURE_NAMES = ("cpu", "ram", "swap", "disk")


def normalize_metrics(sample):
    output = dict(sample)
    for name in FEATURE_NAMES:
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


def feature_vector(sample):
    normalized = normalize_metrics(sample)
    return [normalized[name] for name in FEATURE_NAMES]
