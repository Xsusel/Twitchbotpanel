from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

db = SQLAlchemy()

# Use JSONB for Postgres, JSON for others (SQLite)
JSON_VARIANT = JSON().with_variant(JSONB, 'postgresql')

class Channel(db.Model):
    __tablename__ = 'channels'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    messages = db.relationship('ChatMessage', backref='channel', lazy='dynamic', cascade="all, delete-orphan")
    stats = db.relationship('StreamStats', backref='channel', lazy='dynamic', cascade="all, delete-orphan")
    analyses = db.relationship('AnalysisResult', backref='channel', lazy='dynamic', cascade="all, delete-orphan")
    streams = db.relationship('Stream', backref='channel', lazy='dynamic', cascade="all, delete-orphan")
    viewers = db.relationship('Viewer', backref='channel', lazy='dynamic', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Channel {self.name}>'

class Viewer(db.Model):
    __tablename__ = 'viewers'
    __table_args__ = (db.UniqueConstraint('channel_id', 'username', name='_channel_viewer_uc'),)

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False)
    username = db.Column(db.String(128), nullable=False, index=True)
    twitch_id = db.Column(db.String(64), nullable=True)
    first_seen = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    message_count = db.Column(db.Integer, default=1)
    is_subscriber = db.Column(db.Boolean, default=False)
    sub_tier = db.Column(db.String(32), nullable=True) # e.g. "1000", "2000", "3000" or "Prime"
    follow_duration = db.Column(db.Integer, nullable=True) # Seconds following
    is_mod = db.Column(db.Boolean, default=False)
    color = db.Column(db.String(32), nullable=True)

    # Extended Analytics
    nick_history = db.Column(JSON_VARIANT, default=[]) # List of past usernames
    account_created_at = db.Column(db.DateTime, nullable=True) # From Helix API
    suspicion_score = db.Column(db.Integer, default=0) # 0-100
    suspicion_reason = db.Column(db.String(255), nullable=True)

    stream_stats = db.relationship('StreamViewerStats', backref='viewer', lazy='dynamic', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Viewer {self.username} in {self.channel_id}>'

class StreamViewerStats(db.Model):
    __tablename__ = 'stream_viewer_stats'
    __table_args__ = (db.UniqueConstraint('stream_id', 'viewer_id', name='_stream_viewer_uc'),)

    id = db.Column(db.Integer, primary_key=True)
    stream_id = db.Column(db.Integer, db.ForeignKey('streams.id'), nullable=False)
    viewer_id = db.Column(db.Integer, db.ForeignKey('viewers.id'), nullable=False)

    message_count = db.Column(db.Integer, default=0)
    first_seen = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)

    suspicion_score = db.Column(db.Integer, default=0)
    is_suspicious = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f'<StreamViewerStats S:{self.stream_id} V:{self.viewer_id}>'

class Stream(db.Model):
    __tablename__ = 'streams'

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False)
    title = db.Column(db.String(255))
    game_name = db.Column(db.String(128))
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    is_live = db.Column(db.Boolean, default=True)

    messages = db.relationship('ChatMessage', backref='stream', lazy='dynamic', cascade="all, delete-orphan")
    stats = db.relationship('StreamStats', backref='stream', lazy='dynamic', cascade="all, delete-orphan")
    analyses = db.relationship('AnalysisResult', backref='stream', lazy='dynamic', cascade="all, delete-orphan")
    viewer_stats = db.relationship('StreamViewerStats', backref='stream', lazy='dynamic', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Stream {self.id} {self.channel_id}>'

class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False)
    stream_id = db.Column(db.Integer, db.ForeignKey('streams.id'), nullable=True)
    username = db.Column(db.String(128), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    badges = db.Column(JSON_VARIANT, default={})  # Store badges as JSON
    meta = db.Column(JSON_VARIANT, default={}) # Store extra metadata

    def __repr__(self):
        return f'<Message {self.username}: {self.message[:20]}>'

class StreamStats(db.Model):
    __tablename__ = 'stream_stats'

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False)
    stream_id = db.Column(db.Integer, db.ForeignKey('streams.id'), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    viewer_count = db.Column(db.Integer, default=0)
    chatter_count = db.Column(db.Integer, default=0) # Connected to IRC
    active_chatter_count = db.Column(db.Integer, default=0) # Actually speaking

    def __repr__(self):
        return f'<Stats {self.channel_id} V:{self.viewer_count} C:{self.chatter_count} A:{self.active_chatter_count}>'

class AnalysisResult(db.Model):
    __tablename__ = 'analysis_results'

    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channels.id'), nullable=False)
    stream_id = db.Column(db.Integer, db.ForeignKey('streams.id'), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    bot_score = db.Column(db.Float, default=0.0)
    details = db.Column(JSON_VARIANT, default={}) # Store metrics details (entropy, ratio, etc.)

    def __repr__(self):
        return f'<Analysis {self.channel_id} Score:{self.bot_score}>'

class SystemConfig(db.Model):
    __tablename__ = 'system_config'

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(255), nullable=True)

    def __repr__(self):
        return f'<Config {self.key}: {self.value}>'
