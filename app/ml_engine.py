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

from sklearn.feature_extraction.text import CountVectorizer
from sklearn.decomposition import LatentDirichletAllocation

class TopicModeler:
    def __init__(self, n_topics=3):
        self.n_topics = n_topics
        self.vectorizer = CountVectorizer(stop_words='english', max_features=1000)
        self.lda = LatentDirichletAllocation(n_components=n_topics, random_state=42)

    def extract_topics(self, messages):
        """
        Extracts topics from a list of messages.
        Returns a list of topic strings (top words).
        """
        if not messages or len(messages) < 10:
            return []

        texts = [msg.message if hasattr(msg, 'message') else msg.get('message', '') for msg in messages]
        # Remove empty strings
        texts = [t for t in texts if t.strip()]

        if not texts:
            return []

        try:
            X = self.vectorizer.fit_transform(texts)
            self.lda.fit(X)

            feature_names = self.vectorizer.get_feature_names_out()
            topics = []

            for topic_idx, topic in enumerate(self.lda.components_):
                top_features_ind = topic.argsort()[:-6:-1]
                topic_words = [feature_names[i] for i in top_features_ind]
                topics.append(" ".join(topic_words))

            return topics
        except ValueError:
            return []
