from app.models import db, Channel, ChatMessage, StreamStats, AnalysisResult
from flask import Flask
from config import Config
import logging

def init_db():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    with app.app_context():
        try:
            db.create_all()
            print("Database tables created successfully.")
        except Exception as e:
            print(f"Error creating tables: {e}")

if __name__ == '__main__':
    init_db()
