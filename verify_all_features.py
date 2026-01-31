import sys
import os
from datetime import datetime, timedelta

# Add current directory to sys.path
sys.path.append(os.getcwd())

from app import create_app
from app.models import db, Channel, Viewer, Stream, StreamViewerStats, StreamStats, ChatMessage
from config import Config

class TestConfig(Config):
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    TESTING = True
    WTF_CSRF_ENABLED = False

def verify():
    print("Initializing Test Environment...")
    app = create_app(TestConfig)

    with app.app_context():
        db.create_all()

        # 1. Setup Base Data (Channel & Stream)
        channel1 = Channel(name="channel_1")
        channel2 = Channel(name="channel_2") # For Zombie test
        db.session.add_all([channel1, channel2])
        db.session.commit()

        # Active Streams
        stream1 = Stream(channel_id=channel1.id, title="Main Stream", is_live=True, started_at=datetime.utcnow()-timedelta(hours=1))
        stream2 = Stream(channel_id=channel2.id, title="Other Stream", is_live=True, started_at=datetime.utcnow()-timedelta(hours=1))
        db.session.add_all([stream1, stream2])
        db.session.commit()

        stream_id = stream1.id
        other_stream_id = stream2.id

        print(f"Created Streams: {stream_id} (Target), {other_stream_id} (Other)")

        # 2. Setup Users
        user_suspicious = Viewer(username="sus_user", channel_id=channel1.id, suspicion_score=85, suspicion_reason="Bot Behavior")
        user_chatter = Viewer(username="chatter_box", channel_id=channel1.id, suspicion_score=10)
        user_zombie = Viewer(username="zombie_user", channel_id=channel1.id, suspicion_score=50) # In both streams, silent
        user_lurker = Viewer(username="lurker_user", channel_id=channel1.id, suspicion_score=0, account_created_at=datetime.utcnow()-timedelta(days=10))

        db.session.add_all([user_suspicious, user_chatter, user_zombie, user_lurker])
        db.session.commit()

        # 3. Setup Stats (StreamViewerStats)
        # Suspicious User (Active but sus)
        db.session.add(StreamViewerStats(stream_id=stream_id, viewer_id=user_suspicious.id, message_count=50, is_suspicious=True, suspicion_score=85))

        # Chatter
        db.session.add(StreamViewerStats(stream_id=stream_id, viewer_id=user_chatter.id, message_count=100, is_suspicious=False, suspicion_score=10))

        # Lurker (No messages)
        db.session.add(StreamViewerStats(stream_id=stream_id, viewer_id=user_lurker.id, message_count=0, is_suspicious=False, suspicion_score=0))

        # Zombie (No messages in BOTH streams)
        # Note: Zombie user needs to exist in Viewer table for channel 2 as well if code joins purely on Viewer
        # But typically Viewer is unique by (username, channel). Let's see models.
        # Assuming Viewer is per channel.
        user_zombie_ch2 = Viewer(username="zombie_user", channel_id=channel2.id)
        db.session.add(user_zombie_ch2)
        db.session.commit()

        db.session.add(StreamViewerStats(stream_id=stream_id, viewer_id=user_zombie.id, message_count=0))
        db.session.add(StreamViewerStats(stream_id=other_stream_id, viewer_id=user_zombie_ch2.id, message_count=0))

        db.session.commit()

        # 4. Setup Chat Messages (For Heatmap, Network, Logs)
        now = datetime.utcnow()
        messages = []

        # Heatmap: Spread messages over time
        # 30 mins ago
        messages.append(ChatMessage(stream_id=stream_id, channel_id=channel1.id, username="chatter_box", message="Hello world", timestamp=now-timedelta(minutes=30)))
        messages.append(ChatMessage(stream_id=stream_id, channel_id=channel1.id, username="chatter_box", message="Hype!", timestamp=now-timedelta(minutes=30)))

        # 10 mins ago
        messages.append(ChatMessage(stream_id=stream_id, channel_id=channel1.id, username="sus_user", message="Buy followers cheap", timestamp=now-timedelta(minutes=10)))

        # Network: Mentions
        messages.append(ChatMessage(stream_id=stream_id, channel_id=channel1.id, username="chatter_box", message="Hey @sus_user what is that?", timestamp=now-timedelta(minutes=5)))
        messages.append(ChatMessage(stream_id=stream_id, channel_id=channel1.id, username="sus_user", message="@chatter_box nothing", timestamp=now-timedelta(minutes=4)))

        db.session.add_all(messages)
        db.session.commit()

    # --- VERIFICATION PHASE ---
    print("\nStarting Verification...")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True

    all_passed = True

    # 1. Verify Suspicious Viewers
    print("1. Checking 'Podejrzani Widzowie'...")
    res = client.get(f'/api/stream/{stream_id}/suspicious')
    data = res.json
    if len(data) >= 1 and data[0]['username'] == 'sus_user':
        print("   [PASS] Found suspicious user.")
    else:
        print(f"   [FAIL] Expected 'sus_user', got {data}")
        all_passed = False

    # 2. Verify Lurkers
    print("2. Checking 'Analiza Lurkerów'...")
    res = client.get(f'/api/stream/{stream_id}/lurkers')
    data = res.json
    # Expecting 2 lurkers (user_lurker, user_zombie)
    if data['count'] == 2:
        print(f"   [PASS] Found {data['count']} lurkers.")
    else:
        print(f"   [FAIL] Expected 2 lurkers, got {data['count']}")
        all_passed = False

    # 3. Verify Global Zombies
    print("3. Checking 'Globalne Zombie'...")
    res = client.get(f'/api/stream/{stream_id}/zombies')
    data = res.json
    # Expecting zombie_user
    if len(data) >= 1 and data[0]['username'] == 'zombie_user':
        print("   [PASS] Found zombie user active in other stream.")
    else:
        print(f"   [FAIL] Zombie detection failed. Data: {data}")
        all_passed = False

    # 4. Verify Heatmap
    print("4. Checking 'Heatmapa Aktywności'...")
    res = client.get(f'/api/stream/{stream_id}/heatmap')
    data = res.json
    if len(data['times']) > 0 and sum(data['counts']) == 5: # 5 messages total
        print("   [PASS] Heatmap data generated.")
    else:
        print(f"   [FAIL] Heatmap empty or incorrect. Data: {data}")
        all_passed = False

    # 5. Verify Network
    print("5. Checking 'Sieć Interakcji'...")
    res = client.get(f'/api/stream/{stream_id}/network')
    data = res.json
    # Expect edges between chatter_box and sus_user
    has_edge = any((e['from'] == 'chatter_box' and e['to'] == 'sus_user') or (e['from'] == 'sus_user' and e['to'] == 'chatter_box') for e in data['edges'])
    if has_edge:
        print("   [PASS] Network edges found.")
    else:
        print(f"   [FAIL] No edges found. Data: {data}")
        all_passed = False

    # 6. Verify Logs (Player)
    print("6. Checking 'Odtwarzacz Logów' (Logs API)...")
    res = client.get(f'/api/stream/{stream_id}/logs')
    data = res.json
    if len(data) == 5:
        print("   [PASS] 5 log entries retrieved.")
    else:
        print(f"   [FAIL] Expected 5 logs, got {len(data)}")
        all_passed = False

    if all_passed:
        print("\n=== ALL FEATURES VERIFIED SUCCESSFULLY ===")
    else:
        print("\n=== SOME VERIFICATIONS FAILED ===")

if __name__ == "__main__":
    verify()
