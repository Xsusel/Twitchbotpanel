from sklearn.ensemble import IsolationForest
import numpy as np
import pandas as pd
import re

class MLDetector:
    def __init__(self):
        self.model = IsolationForest(contamination=0.1, random_state=42)
        self.is_trained = False

    def extract_features(self, messages):
        """
        Extract numerical features from messages.
        Features:
        1. Length
        2. Uppercase Ratio
        3. Digit Ratio
        4. Special Char Ratio
        5. Unique Char Ratio
        """
        features = []

        for msg in messages:
            text = msg.message if hasattr(msg, 'message') else msg.get('message', '')
            length = len(text)
            if length == 0:
                features.append([0, 0, 0, 0, 0])
                continue

            uppercase = sum(1 for c in text if c.isupper())
            digits = sum(1 for c in text if c.isdigit())
            special = sum(1 for c in text if not c.isalnum() and not c.isspace())
            unique = len(set(text))

            features.append([
                length,
                uppercase / length,
                digits / length,
                special / length,
                unique / length
            ])

        return np.array(features)

    def detect_anomalies(self, messages):
        """
        Returns a score (percentage of anomalous messages) and indices of anomalies.
        """
        if not messages or len(messages) < 10:
            return 0.0, []

        X = self.extract_features(messages)

        # Train on this batch (unsupervised)
        # In a real system, we might pre-train on "normal" data
        self.model.fit(X)
        preds = self.model.predict(X) # -1 for anomaly, 1 for normal

        anomaly_indices = [i for i, x in enumerate(preds) if x == -1]
        score = (len(anomaly_indices) / len(messages)) * 100

        return score, anomaly_indices
