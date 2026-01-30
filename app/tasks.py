from app import celery, db
from app.models import Channel, ChatMessage, StreamStats, AnalysisResult
from app.analysis import calculate_bot_score, get_language_distribution, analyze_sentiment
from datetime import datetime, timedelta
import requests
import json
from config import Config
from sqlalchemy import func
from app.ml_engine import MLDetector, TopicModeler

ml_detector = MLDetector()
topic_modeler = TopicModeler()

def get_twitch_users_info(usernames):
    if not usernames or not Config.TWITCH_CLIENT_ID or not Config.TWITCH_CLIENT_SECRET:
        return []

    # Get App Access Token
    try:
        token_url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": Config.TWITCH_CLIENT_ID,
            "client_secret": Config.TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials"
        }
        resp = requests.post(token_url, params=params)
        if resp.status_code != 200:
            return []
        token = resp.json().get("access_token")

        # Get Users
        headers = {
            "Client-ID": Config.TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}"
        }

        # Split into chunks of 100 (Twitch limit)
        users_data = []
        chunk_size = 100
        username_list = list(usernames)

        for i in range(0, len(username_list), chunk_size):
            chunk = username_list[i:i+chunk_size]
            query = "&".join([f"login={u}" for u in chunk])
            url = f"https://api.twitch.tv/helix/users?{query}"

            u_resp = requests.get(url, headers=headers)
            if u_resp.status_code == 200:
                users_data.extend(u_resp.json().get("data", []))

        return users_data
    except Exception as e:
        print(f"Error fetching Twitch users: {e}")
        return []

def calculate_account_age_stats(users_data):
    if not users_data:
        return {}

    now = datetime.utcnow()
    total_age_days = 0
    new_accounts = 0 # < 30 days

    for user in users_data:
        created_at_str = user.get("created_at") # 2019-11-03T18:23:44Z
        if created_at_str:
            try:
                # Python 3.9+ handles Z, but let's be safe
                created_at = datetime.strptime(created_at_str.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
                age_days = (now - created_at).days
                total_age_days += age_days
                if age_days < 30:
                    new_accounts += 1
            except ValueError:
                pass

    avg_age = total_age_days / len(users_data) if users_data else 0
    percent_new = (new_accounts / len(users_data)) * 100 if users_data else 0

    return {
        "avg_age_days": round(avg_age, 1),
        "percent_new": round(percent_new, 1),
        "sample_size": len(users_data)
    }

def send_discord_alert(channel, score, viewers, age_stats):
    try:
        data = {
            "content": f"🚨 **XSUS Sentinel Alert** 🚨\nHigh Bot Probability Detected!",
            "embeds": [{
                "title": f"Channel: {channel.name}",
                "color": 15158332, # Red
                "fields": [
                    {"name": "Bot Score", "value": f"{score}%", "inline": True},
                    {"name": "Viewers", "value": str(viewers), "inline": True},
                    {"name": "Avg Account Age", "value": f"{age_stats.get('avg_age_days', 'N/A')} days", "inline": True},
                    {"name": "New Accounts (<30d)", "value": f"{age_stats.get('percent_new', 'N/A')}%", "inline": True}
                ],
                "timestamp": datetime.utcnow().isoformat()
            }]
        }
        requests.post(Config.DISCORD_WEBHOOK_URL, json=data)
    except Exception as e:
        print(f"Discord webhook error: {e}")

@celery.task
def analyze_channel(channel_id):
    try:
        channel = Channel.query.get(channel_id)
        if not channel:
            return

        # Get latest stats (within last 10 mins)
        stats = StreamStats.query.filter_by(channel_id=channel_id).order_by(StreamStats.timestamp.desc()).first()
        if not stats or (datetime.utcnow() - stats.timestamp).total_seconds() > 600:
            # Stats too old or missing
            return

        # Get recent messages (last 10 mins)
        since = datetime.utcnow() - timedelta(minutes=10)
        messages = ChatMessage.query.filter_by(channel_id=channel_id).filter(ChatMessage.timestamp >= since).all()

        score = calculate_bot_score(stats.viewer_count, stats.chatter_count, messages)
        langs = get_language_distribution(messages)
        sentiment = analyze_sentiment(messages)

        # Account Age Analysis (Sample top 50 unique users)
        current_users = list(set([m.username for m in messages]))
        unique_users_sample = current_users[:50]
        users_data = get_twitch_users_info(unique_users_sample)
        age_stats = calculate_account_age_stats(users_data)

        # Adjust score based on age stats
        if age_stats.get("percent_new", 0) > 50:
            score += 20
        if age_stats.get("avg_age_days", 100) < 7: # Very fresh accounts on avg
            score += 20

        # Hive Mind Check (Cross-Channel)
        # Find users in this batch who have been active in OTHER channels recently
        hive_mind_stats = {"cross_channel_users": 0, "total_tracked_users": len(current_users)}
        if current_users:
            try:
                # Users active in last 10 mins in any channel
                recent_active_users = db.session.query(ChatMessage.username)\
                    .filter(ChatMessage.timestamp >= since)\
                    .filter(ChatMessage.channel_id != channel_id)\
                    .filter(ChatMessage.username.in_(current_users))\
                    .distinct().all()

                cross_channel_users_count = len(recent_active_users)
                hive_mind_stats["cross_channel_users"] = cross_channel_users_count

                if cross_channel_users_count > 5: # If more than 5 users are hopping channels simultaneously
                    score += 10
            except Exception as e:
                print(f"Hive mind query error: {e}")

        # ML Anomaly Detection
        ml_score, anomaly_indices = ml_detector.detect_anomalies(messages)
        if ml_score > 20: # High percentage of weird messages
            score += 10
        if ml_score > 50:
            score += 10

        # Topic Modeling
        topics = topic_modeler.extract_topics(messages)

        score = min(score, 100.0)

        # Alerting
        if score > 80 and Config.DISCORD_WEBHOOK_URL:
            send_discord_alert(channel, score, stats.viewer_count, age_stats)

        # Save result
        result = AnalysisResult(
            channel_id=channel_id,
            bot_score=score,
            details={
                "viewer_count": stats.viewer_count,
                "chatter_count": stats.chatter_count,
                "message_count": len(messages),
                "languages": langs,
                "account_age": age_stats,
                "sentiment": sentiment,
                "hive_mind": hive_mind_stats,
                "ml_anomaly_score": ml_score,
                "topics": topics
            }
        )
        db.session.add(result)
        db.session.commit()

    except Exception as e:
        print(f"Error analyzing channel {channel_id}: {e}")
