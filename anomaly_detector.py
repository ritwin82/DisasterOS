"""Persistent, calibrated anomaly detection for host resource telemetry."""
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np

from config import (ANOMALY_FALSE_POSITIVE_RATE, BASELINE_SAMPLES,
                    MODEL_CALIBRATION_FRACTION, MODEL_VERSION)
from feature_engineering import FEATURE_NAMES, feature_vector, normalize_metrics


class RobustRandomProjectionDetector:
    """Portable unsupervised fallback that requires only NumPy native operations."""

    def __init__(self, projections=96, random_state=42):
        self.projections = projections
        self.random_state = random_state

    def fit(self, values):
        values = np.asarray(values, dtype=float)
        self.center = np.median(values, axis=0)
        q75, q25 = np.percentile(values, [75, 25], axis=0)
        self.scale = np.where(q75 - q25 > 1e-9, q75 - q25, 1.0)
        normalized = (values - self.center) / self.scale
        generator = np.random.default_rng(self.random_state)
        directions = generator.normal(size=(self.projections, values.shape[1]))
        lengths = np.linalg.norm(directions, axis=1, keepdims=True)
        self.directions = directions / np.where(lengths > 1e-9, lengths, 1.0)
        projected = normalized @ self.directions.T
        self.projected_center = np.median(projected, axis=0)
        deviation = np.median(np.abs(projected - self.projected_center), axis=0)
        self.projected_scale = np.where(deviation > 1e-9, deviation, 1.0)
        return self

    def decision_function(self, values):
        values = np.asarray(values, dtype=float)
        normalized = (values - self.center) / self.scale
        projected = normalized @ self.directions.T
        distance = np.abs(projected - self.projected_center) / self.projected_scale
        return -np.percentile(distance, 90, axis=1)


class AnomalyDetector:
    """Learn from clean real samples, while allowing a different sample to be scored."""

    HISTORY_WINDOW = 10

    def __init__(self, baseline_samples=BASELINE_SAMPLES, model_path=None,
                 calibration_fraction=MODEL_CALIBRATION_FRACTION,
                 false_positive_rate=ANOMALY_FALSE_POSITIVE_RATE):
        self.baseline_samples = max(4, int(baseline_samples))
        self.model_path = Path(model_path) if model_path else None
        self.calibration_fraction = min(max(float(calibration_fraction), 0.1), 0.5)
        self.false_positive_rate = min(max(float(false_positive_rate), 0.001), 0.2)
        self.baseline = []
        self.recent = deque(maxlen=self.HISTORY_WINDOW)
        self.model = None
        self.threshold = None
        self.metadata = {}
        self.source = "learning"
        self.error = None
        if self.model_path and self.model_path.exists():
            self._load()

    @staticmethod
    def features(sample, history=None):
        return feature_vector(sample, history)

    @property
    def sample_count(self):
        return max(len(self.baseline), int(self.metadata.get("sample_count", 0)))

    def _load(self):
        try:
            payload = joblib.load(self.model_path)
            if payload.get("version") != MODEL_VERSION:
                return
            if tuple(payload.get("feature_names", ())) != tuple(FEATURE_NAMES):
                return
            self.model = payload["model"]
            self.threshold = float(payload["threshold"])
            self.metadata = {key: value for key, value in payload.items() if key != "model"}
            self.source = "loaded"
        except Exception as exc:
            self.error = f"Could not load saved model: {exc}"

    def _save(self):
        if not self.model_path:
            return
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.model_path.with_suffix(self.model_path.suffix + ".tmp")
        payload = {**self.metadata, "model": self.model}
        try:
            joblib.dump(payload, temporary)
            temporary.replace(self.model_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _fit(self):
        vectors = np.asarray(self.baseline, dtype=float)
        split = int(round(len(vectors) * (1.0 - self.calibration_fraction)))
        split = min(max(split, 2), len(vectors) - 1)
        training, calibration = vectors[:split], vectors[split:]
        algorithm = "Robust random-projection ensemble"
        backend_note = None
        try:
            from sklearn.ensemble import IsolationForest
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import RobustScaler
            model = Pipeline([
                ("scale", RobustScaler()),
                ("forest", IsolationForest(n_estimators=200, contamination="auto",
                                           random_state=42, n_jobs=-1)),
            ])
            algorithm = "Isolation Forest"
        except (ImportError, OSError) as exc:
            model = RobustRandomProjectionDetector()
            backend_note = f"scikit-learn unavailable; portable fallback active: {exc}"
        model.fit(training)
        calibration_scores = model.decision_function(calibration)
        threshold = float(np.quantile(calibration_scores, self.false_positive_rate))
        self.model = model
        self.threshold = threshold
        self.metadata = {
            "version": MODEL_VERSION,
            "feature_names": list(FEATURE_NAMES),
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sample_count": len(vectors),
            "train_count": len(training),
            "calibration_count": len(calibration),
            "calibration_false_positive_rate": float(np.mean(calibration_scores < threshold)),
            "false_positive_target": self.false_positive_rate,
            "threshold": threshold,
            "algorithm": algorithm,
            "backend_note": backend_note,
        }
        self.source = "trained"
        self._save()

    def bootstrap(self, samples):
        """Use persisted clean observations after restart without learning from simulations."""
        clean = [normalize_metrics(sample) for sample in samples]
        if self.model is not None:
            self.recent.extend(clean[-self.HISTORY_WINDOW:])
            return
        for sample in clean[-self.baseline_samples:]:
            self.baseline.append(self.features(sample, self.recent))
            self.recent.append(sample)
        if len(self.baseline) >= self.baseline_samples:
            self._fit()

    def assess(self, sample, simulated=False, score_sample=None):
        """Score observed data, but add only uncontaminated real data to training."""
        try:
            clean_sample = normalize_metrics(sample)
            scoring_sample = normalize_metrics(score_sample if score_sample is not None else sample)
            training_vector = self.features(clean_sample, self.recent)
            scoring_vector = self.features(scoring_sample, self.recent)

            if not simulated:
                if self.model is None:
                    self.baseline.append(training_vector)
                    if len(self.baseline) >= self.baseline_samples:
                        self._fit()
                self.recent.append(clean_sample)

            common = {
                "samples": self.sample_count,
                "required_samples": self.baseline_samples,
                "threshold": self.threshold,
                "model_version": self.metadata.get("version", MODEL_VERSION),
                "model_source": self.source,
                "algorithm": self.metadata.get("algorithm", "Learning"),
                "backend_note": self.metadata.get("backend_note"),
                "trained_at": self.metadata.get("trained_at"),
                "feature_count": len(FEATURE_NAMES),
                "train_count": self.metadata.get("train_count"),
                "calibration_count": self.metadata.get("calibration_count"),
                "calibration_false_positive_rate": self.metadata.get("calibration_false_positive_rate"),
                "scored_simulated": bool(simulated),
            }
            if self.model is None:
                return {"status": "LEARNING", "score": None, "is_anomaly": False, **common}
            score = float(self.model.decision_function([scoring_vector])[0])
            unusual = score < self.threshold
            return {"status": "UNUSUAL" if unusual else "NORMAL", "score": score,
                    "is_anomaly": unusual, **common}
        except Exception as exc:
            self.error = str(exc)
            return {"status": "UNAVAILABLE", "score": None, "is_anomaly": False,
                    "samples": self.sample_count, "required_samples": self.baseline_samples,
                    "threshold": self.threshold, "model_version": MODEL_VERSION,
                    "model_source": self.source, "trained_at": self.metadata.get("trained_at"),
                    "feature_count": len(FEATURE_NAMES), "scored_simulated": bool(simulated),
                    "error": self.error}
