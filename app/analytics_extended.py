from app.models import db, StreamStats, ChatMessage, StreamViewerStats, Viewer, Stream, AnalysisResult
from datetime import datetime, timedelta
import Levenshtein
from sqlalchemy import func, distinct
import math
import re
import networkx as nx

def calculate_join_part_velocity(stream_id):
    """
    Calculates the rate of viewer joins/parts per minute.
    """
    stats = StreamStats.query.filter_by(stream_id=stream_id).order_by(StreamStats.timestamp.asc()).all()
    if len(stats) < 2:
        return {'timestamps': [], 'velocity': []}

    timestamps = []
    velocities = []

    for i in range(1, len(stats)):
        prev = stats[i-1]
        curr = stats[i]

        time_diff = (curr.timestamp - prev.timestamp).total_seconds()
        if time_diff < 1: continue

        # Viewer change per minute
        viewer_diff = curr.viewer_count - prev.viewer_count
        velocity = (viewer_diff / time_diff) * 60

        timestamps.append(curr.timestamp.strftime('%H:%M:%S'))
        velocities.append(round(velocity, 2))

    return {
        'timestamps': timestamps,
        'velocity': velocities
    }

def find_message_clusters(stream_id, distance_threshold=5, limit=2000):
    """
    Groups similar messages to find spam patterns.
    """
    messages = ChatMessage.query.filter_by(stream_id=stream_id).order_by(ChatMessage.timestamp.desc()).limit(limit).all()

    clusters = [] # List of {'pattern': str, 'count': int, 'samples': set}

    for msg in messages:
        text = msg.message.strip().lower()
        if len(text) < 3: continue

        found = False
        for cluster in clusters:
            # Check distance to pattern
            dist = Levenshtein.distance(text, cluster['pattern'])
            if dist <= distance_threshold:
                cluster['count'] += 1
                cluster['samples'].add(msg.message)
                found = True
                break

        if not found:
            clusters.append({
                'pattern': text,
                'count': 1,
                'samples': {msg.message}
            })

    # Filter for significant clusters (e.g. count > 2)
    significant_clusters = [c for c in clusters if c['count'] > 2]

    # Sort by count desc
    significant_clusters.sort(key=lambda x: x['count'], reverse=True)

    # Format for JSON
    result = []
    for c in significant_clusters[:20]: # Top 20
        result.append({
            'pattern': c['pattern'],
            'count': c['count'],
            'samples': list(c['samples'])[:5] # Return top 5 variations
        })

    return result

def analyze_lurkers(stream_id):
    """
    Analyzes silent viewers (message_count = 0).
    """
    # Get lurker stats
    lurkers_query = db.session.query(StreamViewerStats, Viewer).join(Viewer).filter(
        StreamViewerStats.stream_id == stream_id,
        StreamViewerStats.message_count == 0
    )

    lurkers = lurkers_query.all()
    total_lurkers = len(lurkers)
    if total_lurkers == 0:
        return {'count': 0}

    total_age_days = 0
    age_count = 0
    suspicious_sum = 0

    # Age distribution
    age_buckets = {'<1d': 0, '1d-1w': 0, '1w-1m': 0, '>1m': 0}

    now = datetime.utcnow()

    for svs, v in lurkers:
        suspicious_sum += svs.suspicion_score

        if v.account_created_at:
            age = now - v.account_created_at
            days = age.days
            total_age_days += days
            age_count += 1

            if days < 1: age_buckets['<1d'] += 1
            elif days < 7: age_buckets['1d-1w'] += 1
            elif days < 30: age_buckets['1w-1m'] += 1
            else: age_buckets['>1m'] += 1

    avg_age = round(total_age_days / age_count, 1) if age_count > 0 else 0
    avg_suspicion = round(suspicious_sum / total_lurkers, 1)

    return {
        'count': total_lurkers,
        'avg_account_age_days': avg_age,
        'avg_suspicion': avg_suspicion,
        'age_distribution': age_buckets
    }

def generate_chat_heatmap(stream_id, bucket_minutes=5):
    """
    Aggregates messages into time buckets.
    """
    stream = Stream.query.get(stream_id)
    if not stream:
        return {}

    start_time = stream.started_at
    end_time = stream.ended_at or datetime.utcnow()

    # Round start time to nearest bucket
    # Iterate buckets

    messages = ChatMessage.query.filter_by(stream_id=stream_id).all()

    # Create buckets
    buckets = {}

    for msg in messages:
        # Calculate minutes from start
        delta = msg.timestamp - start_time
        minutes = int(delta.total_seconds() / 60)
        bucket_idx = (minutes // bucket_minutes) * bucket_minutes

        buckets[bucket_idx] = buckets.get(bucket_idx, 0) + 1

    # Sort buckets
    sorted_times = sorted(buckets.keys())
    if not sorted_times:
        return {'times': [], 'counts': []}

    # Fill gaps? Maybe not necessary for heatmap, but good for charts
    max_time = sorted_times[-1]
    filled_times = []
    filled_counts = []

    for t in range(0, max_time + bucket_minutes, bucket_minutes):
        filled_times.append(t)
        filled_counts.append(buckets.get(t, 0))

    return {
        'times': filled_times, # Minutes from start
        'counts': filled_counts,
        'bucket_size': bucket_minutes
    }

def check_cross_stream_zombies(stream_id):
    """
    Finds viewers in this stream who are also in other ACTIVE streams
    and are silent in ALL of them (or just this one + others).
    """
    # 1. Get current stream's silent viewers
    current_lurkers_ids = [r[0] for r in db.session.query(StreamViewerStats.viewer_id).filter_by(
        stream_id=stream_id, message_count=0
    ).all()]

    if not current_lurkers_ids:
        return []

    # 2. Get other active streams
    active_streams = Stream.query.filter(Stream.is_live == True, Stream.id != stream_id).all()
    active_stream_ids = [s.id for s in active_streams]

    if not active_stream_ids:
        return []

    # 3. Check if these lurkers are present in other streams
    # We want viewers who are in current_lurkers_ids AND in StreamViewerStats of other streams

    zombies = []

    # This query might be heavy if many viewers. Optimize?
    # Query: Select viewer_id, stream_id from StreamViewerStats
    # where stream_id IN active_stream_ids AND viewer_id IN current_lurkers_ids

    potential_zombies = db.session.query(
        StreamViewerStats.viewer_id,
        StreamViewerStats.stream_id
    ).filter(
        StreamViewerStats.stream_id.in_(active_stream_ids),
        StreamViewerStats.viewer_id.in_(current_lurkers_ids),
        StreamViewerStats.message_count == 0
    ).all()

    zombie_map = {}
    for vid, sid in potential_zombies:
        if vid not in zombie_map:
            zombie_map[vid] = set()
        zombie_map[vid].add(sid)

    for vid, stream_ids in zombie_map.items():
        viewer = Viewer.query.get(vid)
        zombies.append({
            'username': viewer.username,
            'other_streams_count': len(stream_ids),
            'other_stream_ids': list(stream_ids),
            'suspicion_score': viewer.suspicion_score
        })

    # Sort by count desc
    zombies.sort(key=lambda x: x['other_streams_count'], reverse=True)

    return zombies

def get_account_age_distribution(stream_id):
    """
    Calculates age buckets for all viewers in the stream.
    """
    # Join StreamViewerStats with Viewer to get account_created_at
    query = db.session.query(Viewer.account_created_at).join(StreamViewerStats).filter(
        StreamViewerStats.stream_id == stream_id,
        Viewer.account_created_at.isnot(None)
    )

    results = query.all()

    age_buckets = {
        '<1d': 0,
        '1d-1w': 0,
        '1w-1m': 0,
        '1m-1y': 0,
        '>1y': 0
    }

    now = datetime.utcnow()

    for (created_at,) in results:
        if not created_at: continue
        age = now - created_at
        days = age.days

        if days < 1: age_buckets['<1d'] += 1
        elif days < 7: age_buckets['1d-1w'] += 1
        elif days < 30: age_buckets['1w-1m'] += 1
        elif days < 365: age_buckets['1m-1y'] += 1
        else: age_buckets['>1y'] += 1

    return age_buckets

def get_follower_velocity(stream_id):
    """
    Reconstructs follower joins during the stream using follow_duration.
    """
    stream = Stream.query.get(stream_id)
    if not stream:
        return {'timestamps': [], 'followers': [], 'messages': []}

    start = stream.started_at
    end = stream.ended_at or datetime.utcnow()

    # Get all viewers with follow_duration
    viewers = db.session.query(Viewer.follow_duration).join(StreamViewerStats).filter(
        StreamViewerStats.stream_id == stream_id,
        Viewer.follow_duration.isnot(None)
    ).all()

    now = datetime.utcnow()
    followers_ts = []

    for (duration,) in viewers:
        if duration is None: continue
        # Calculate when they followed
        # Note: follow_duration is usually updated when bot sees user.
        # Ideally it's duration until NOW. Assuming it's up-to-date.
        followed_at = now - timedelta(seconds=duration)
        if start <= followed_at <= end:
            followers_ts.append(followed_at)

    # Also get message timestamps for comparison
    messages = db.session.query(ChatMessage.timestamp).filter(
        ChatMessage.stream_id == stream_id
    ).all()
    messages_ts = [m[0] for m in messages]

    # Bucket by minute
    buckets = {}

    # Helper to add to bucket
    def add_bucket(ts, key):
        bucket = ts.replace(second=0, microsecond=0)
        ts_str = bucket.strftime('%H:%M')
        if ts_str not in buckets: buckets[ts_str] = {'followers': 0, 'messages': 0, 'ts': bucket}
        buckets[ts_str][key] += 1

    for ts in followers_ts: add_bucket(ts, 'followers')
    for ts in messages_ts: add_bucket(ts, 'messages')

    # Sort by time
    sorted_items = sorted(buckets.items(), key=lambda x: x[1]['ts'])

    return {
        'timestamps': [k for k, v in sorted_items],
        'followers': [v['followers'] for k, v in sorted_items],
        'messages': [v['messages'] for k, v in sorted_items]
    }

def analyze_username_patterns(stream_id):
    """
    Analyzes usernames for entropy and suspicious patterns.
    """
    viewers = db.session.query(Viewer.username).join(StreamViewerStats).filter(
        StreamViewerStats.stream_id == stream_id
    ).all()

    if not viewers:
        return {'entropy_dist': {}, 'patterns': [], 'suspicious_patterns_count': 0, 'top_patterns': []}

    high_entropy = 0
    medium_entropy = 0
    low_entropy = 0

    patterns = []
    regex_suspicious = re.compile(r'^[a-zA-Z]+[0-9]{3,}$') # e.g. Bob1234

    for (username,) in viewers:
        if not username: continue
        # Calculate Shannon Entropy
        prob = [float(username.count(c)) / len(username) for c in dict.fromkeys(list(username))]
        entropy = - sum([p * math.log(p) / math.log(2.0) for p in prob])

        if entropy > 4.0: high_entropy += 1 # Very random
        elif entropy > 3.0: medium_entropy += 1
        else: low_entropy += 1

        if regex_suspicious.match(username):
             patterns.append(username)

    return {
        'entropy_dist': {'Wysoka': high_entropy, 'Średnia': medium_entropy, 'Niska': low_entropy},
        'suspicious_patterns_count': len(patterns),
        'top_patterns': patterns[:20]
    }

def get_global_threat_level(stream_id):
    """
    Aggregates metrics into a single 0-100 score.
    """
    # 1. Base Score from AnalysisResult (if exists)
    analysis = db.session.query(AnalysisResult).filter_by(stream_id=stream_id).order_by(AnalysisResult.timestamp.desc()).first()
    base_score = analysis.bot_score if analysis else 0.0

    # 2. Lurker Ratio
    stats = StreamStats.query.filter_by(stream_id=stream_id).order_by(StreamStats.timestamp.desc()).first()
    lurker_score = 0
    if stats and stats.viewer_count > 0:
        ratio = (stats.viewer_count - stats.chatter_count) / stats.viewer_count
        if ratio > 0.8: lurker_score = 80 # High lurker count
        elif ratio > 0.5: lurker_score = 40

    # 3. Account Age Factor
    age_dist = get_account_age_distribution(stream_id)
    total_viewers = sum(age_dist.values())
    age_score = 0
    if total_viewers > 0:
        new_account_ratio = (age_dist['<1d'] + age_dist['1d-1w']) / total_viewers
        if new_account_ratio > 0.3: age_score = 90
        elif new_account_ratio > 0.1: age_score = 50

    # Weighted Average
    # Base (ML/Heuristics) 50%, Age 30%, Lurkers 20%
    final_score = (base_score * 0.5) + (age_score * 0.3) + (lurker_score * 0.2)
    return min(100, round(final_score))

def generate_stream_network(stream_id):
    """
    Generates a network graph for a specific stream.
    Returns data compatible with Vis.js.
    """
    messages = ChatMessage.query.filter_by(stream_id=stream_id).all()

    G = nx.DiGraph()

    # Track node stats
    node_stats = {} # username -> {msg_count: 0, suspicion: 0}

    # Pre-fetch suspicion scores
    if not messages:
        return {'nodes': [], 'edges': []}

    usernames = set(m.username for m in messages)
    viewers = Viewer.query.filter(Viewer.username.in_(usernames)).all()
    suspicion_map = {v.username: v.suspicion_score for v in viewers}

    for msg in messages:
        u = msg.username
        if u not in node_stats:
            node_stats[u] = {'msg_count': 0, 'suspicion': suspicion_map.get(u, 0)}
        node_stats[u]['msg_count'] += 1

        # Simple Mentions
        mentions = re.findall(r'@(\w+)', msg.message)
        for target in mentions:
            if target in usernames: # Only map internal interactions? Or add external nodes?
                 if target not in node_stats:
                      # If target didn't speak, we might not have them in 'viewers' map if they are just mentioned
                      # But let's add them as nodes
                      node_stats[target] = {'msg_count': 0, 'suspicion': 0}

                 if G.has_edge(u, target):
                     G[u][target]['weight'] += 1
                 else:
                     G.add_edge(u, target, weight=1)

    # Format for Vis.js
    nodes = []
    for user, stats in node_stats.items():
        # Color: Green (0) to Red (100)
        score = stats['suspicion']
        color = '#28a745' # Green
        if score > 70: color = '#dc3545' # Red
        elif score > 30: color = '#ffc107' # Yellow

        # Size based on msg_count (log scale?)
        size = 10 + min(50, stats['msg_count'])

        nodes.append({
            'id': user,
            'label': user,
            'value': size, # Vis.js uses 'value' for size
            'color': color,
            'title': f"Msgs: {stats['msg_count']}, Score: {score}" # Tooltip
        })

    edges = []
    for u, v, data in G.edges(data=True):
        edges.append({
            'from': u,
            'to': v,
            'value': data['weight'],
            'arrows': 'to'
        })

    return {'nodes': nodes, 'edges': edges}
