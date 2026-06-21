"""
src/scaler.py
=============
ManualScaler — must be importable from both src/train.py and api/main.py
so that joblib can pickle/unpickle it correctly.
"""
import numpy as np


class ManualScaler:
    """
    Standardise features: z = (x - mean) / std
    Pure NumPy — no sklearn dependency.
    """
    def __init__(self):
        self.mean_ = None
        self.std_  = None

    def fit(self, X: np.ndarray) -> "ManualScaler":
        self.mean_ = X.mean(axis=0)
        self.std_  = X.std(axis=0)
        self.std_[self.std_ == 0] = 1.0   # avoid division by zero
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean_) / self.std_

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)
