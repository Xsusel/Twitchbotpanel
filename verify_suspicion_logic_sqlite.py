import sys
import os
from datetime import datetime, timedelta

# Add current directory to sys.path
sys.path.append(os.getcwd())

from app import create_app
from app.models import db, Channel, Viewer, Stream, StreamViewerStats, StreamStats
from config import Config

class TestConfig(Config):
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    TESTING = True
    WTF_CSRF_ENABLED = False

def verify():
    # Use Test Config
    app = create_app(TestConfig)

    stream_id = None

    with app.app_context():
        # Ensure DB is created
        db.create_all()

        # Create a test channel
        channel = Channel.query.filter_by(name="test_channel").first()
        if not channel:
            channel = Channel(name="test_channel")
            db.session.add(channel)
            db.session.commit()

        # Create a stream
        stream = Stream(channel_id=channel.id, title="Test Stream", is_live=True)
        db.session.add(stream)
        db.session.commit()
        stream_id = stream.id

        # Create a suspicious viewer
        viewer = Viewer(
            channel_id=channel.id,
            username="suspicious_bot_123",
            suspicion_score=80,
            suspicion_reason="Name Pattern"
        )
        db.session.add(viewer)
        db.session.commit()

        # Create StreamViewerStats
        sv_stats = StreamViewerStats(
            stream_id=stream.id,
            viewer_id=viewer.id,
            message_count=100,
            is_suspicious=True,
            suspicion_score=80
        )
        db.session.add(sv_stats)

        # Create Stream Stats for Growth
        now = datetime.utcnow()
        db.session.add(StreamStats(channel_id=channel.id, stream_id=stream.id, timestamp=now - timedelta(minutes=10), viewer_count=100))
        db.session.add(StreamStats(channel_id=channel.id, stream_id=stream.id, timestamp=now - timedelta(minutes=9), viewer_count=110))
        db.session.add(StreamStats(channel_id=channel.id, stream_id=stream.id, timestamp=now - timedelta(minutes=8), viewer_count=500)) # Spike
        db.session.add(StreamStats(channel_id=channel.id, stream_id=stream.id, timestamp=now - timedelta(minutes=7), viewer_count=510))

        db.session.commit()
        print("Created test data.")

    print("Verification script running API check...")
    with app.test_client() as client:
        # Mock session
        with client.session_transaction() as sess:
            sess['logged_in'] = True

        # Check Suspicious
        res = client.get(f'/api/stream/{stream_id}/suspicious')
        if res.status_code == 200:
            data = res.json
            print(f"Suspicious Viewers: {len(data)}")
            if len(data) > 0 and data[0]['username'] == 'suspicious_bot_123':
                 print("SUCCESS: Suspicious API verified.")
            else:
                 print("FAILURE: Suspicious API data mismatch.")
        else:
            print(f"FAILURE: Suspicious API {res.status_code}")

        # Check Growth
        res = client.get(f'/api/stream/{stream_id}/growth')
        if res.status_code == 200:
            data = res.json
            print(f"Growth Data: Spike={data.get('has_spike')}")
            if data.get('has_spike'):
                 print("SUCCESS: Growth API verified.")
            else:
                 print("FAILURE: Growth API failed to detect spike.")
        else:
             print(f"FAILURE: Growth API {res.status_code}")

if __name__ == "__main__":
    verify()
