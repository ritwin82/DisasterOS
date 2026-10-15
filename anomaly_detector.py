import numpy as np
from sklearn.ensemble import IsolationForest

from config import BASELINE_SAMPLES
from feature_engineering import feature_vector


class AnomalyDetector:
    def __init__(self, baseline_samples=BASELINE_SAMPLES):
        self.baseline_samples = baseline_samples
        self.baseline = []
        self.model = None
        self.error = None

    @staticmethod
    def features(sample):
        return feature_vector(sample)

    def assess(self, sample, simulated=False):
        try:
            vector = self.features(sample)
            if not simulated and self.model is None:
                self.baseline.append(vector)
                if len(self.baseline) >= self.baseline_samples:
                    self.model = IsolationForest(n_estimators=60, contamination=0.1, random_state=42)
                    self.model.fit(np.asarray(self.baseline))
            if self.model is None:
                return {"status": "LEARNING", "score": None, "is_anomaly": False, "samples": len(self.baseline)}
            score = float(self.model.decision_function([vector])[0])
            return {"status": "UNUSUAL" if score < 0 else "NORMAL", "score": score,
                    "is_anomaly": score < 0, "samples": len(self.baseline)}
        except Exception as exc:
            self.error = str(exc)
            return {"status": "UNAVAILABLE", "score": None, "is_anomaly": False,
                    "samples": len(self.baseline), "error": self.error}
