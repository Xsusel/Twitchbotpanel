from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import JSONB

db = SQLAlchemy()

class Channel(db.Model):
    __tablename__ = 'channels'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    messages = db.relationship('ChatMessage', backref='channel', lazy='dynamic', cascade="all, delete-orphan")
    stats = db.relationship('StreamStats', backref='channel', lazy='dynamic', cascade="all, delete-orphan")
    analyses = db.relationship('AnalysisResult', backref='channel', lazy='dynamic', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Channel {self.name}>'

class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False)
    username = db.Column(db.String(128), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    badges = db.Column(JSONB, default={})  # Store badges as JSON
    meta = db.Column(JSONB, default={}) # Store extra metadata

    def __repr__(self):
        return f'<Message {self.username}: {self.message[:20]}>'

class StreamStats(db.Model):
    __tablename__ = 'stream_stats'

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    viewer_count = db.Column(db.Integer, default=0)
    chatter_count = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<Stats {self.channel_id} V:{self.viewer_count} C:{self.chatter_count}>'

class AnalysisResult(db.Model):
    __tablename__ = 'analysis_results'

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    bot_score = db.Column(db.Float, default=0.0)
    details = db.Column(JSONB, default={}) # Store metrics details (entropy, ratio, etc.)

    def __repr__(self):
        return f'<Analysis {self.channel_id} Score:{self.bot_score}>'
