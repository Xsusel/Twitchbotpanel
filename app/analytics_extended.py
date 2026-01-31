from app.models import db, StreamStats, ChatMessage, StreamViewerStats, Viewer, Stream, AnalysisResult, Channel
from datetime import datetime, timedelta
import Levenshtein
from sqlalchemy import func, distinct
import math
import re
import networkx as nx
from textblob import TextBlob
from collections import Counter

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
    # 1. Get usernames of current stream's silent viewers
    current_lurkers_usernames = [r[0] for r in db.session.query(Viewer.username).join(StreamViewerStats).filter(
        StreamViewerStats.stream_id == stream_id,
        StreamViewerStats.message_count == 0
    ).all()]

    if not current_lurkers_usernames:
        return []

    # 2. Get other active streams
    active_streams = Stream.query.filter(Stream.is_live == True, Stream.id != stream_id).all()
    active_stream_ids = [s.id for s in active_streams]

    if not active_stream_ids:
        return []

    # 3. Find these usernames in other active streams (also silent)
    # We join Viewer to match by username across channels
    potential_zombies = db.session.query(
        Viewer.username,
        StreamViewerStats.stream_id,
        Viewer.suspicion_score
    ).join(StreamViewerStats).filter(
        StreamViewerStats.stream_id.in_(active_stream_ids),
        Viewer.username.in_(current_lurkers_usernames),
        StreamViewerStats.message_count == 0
    ).all()

    zombie_map = {}
    for username, sid, score in potential_zombies:
        if username not in zombie_map:
            zombie_map[username] = {'streams': set(), 'score': score}
        zombie_map[username]['streams'].add(sid)

    zombies = []
    for username, data in zombie_map.items():
        zombies.append({
            'username': username,
            'other_streams_count': len(data['streams']),
            'other_stream_ids': list(data['streams']),
            'suspicion_score': data['score']
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

def get_suspicion_distribution(stream_id):
    """
    Returns a histogram of suspicion scores for viewers in the stream.
    Buckets: 0-20% (Safe), 21-50% (Low Risk), 51-80% (Medium Risk), 81-100% (High Risk)
    """
    stats = StreamViewerStats.query.filter_by(stream_id=stream_id).all()

    buckets = {
        '0-20%': 0,
        '21-50%': 0,
        '51-80%': 0,
        '81-100%': 0
    }

    for s in stats:
        score = s.suspicion_score
        if score <= 20: buckets['0-20%'] += 1
        elif score <= 50: buckets['21-50%'] += 1
        elif score <= 80: buckets['51-80%'] += 1
        else: buckets['81-100%'] += 1

    return buckets

def analyze_temporal_synchronization(stream_id):
    """
    Hive Mind: Detects second-level synchronization of messages.
    Returns timestamps where > Threshold unique users sent a message in the same second.
    """
    # SQLite has limited date functions, Postgres uses date_trunc.
    # We will fetch all timestamps and process in python for compatibility/simplicity unless volume is huge.
    # Assuming stream logs < 100k messages, python processing is fine.

    messages = db.session.query(ChatMessage.timestamp, ChatMessage.username).filter(
        ChatMessage.stream_id == stream_id
    ).all()

    if not messages:
        return {'spikes': []}

    # Group by second
    counts = {} # timestamp_str -> set(usernames)

    for ts, user in messages:
        ts_sec = ts.replace(microsecond=0)
        if ts_sec not in counts:
            counts[ts_sec] = set()
        counts[ts_sec].add(user)

    # Find spikes (e.g. > 3 users in same second)
    spikes = []
    THRESHOLD = 3

    for ts, users in counts.items():
        if len(users) > THRESHOLD:
            spikes.append({
                'timestamp': ts,
                'count': len(users),
                'users': list(users)[:5] # Sample
            })

    # Sort by timestamp
    spikes.sort(key=lambda x: x['timestamp'])

    # Format timestamps for JSON
    result = []
    for s in spikes:
        result.append({
            'timestamp': s['timestamp'].isoformat(),
            'count': s['count'],
            'users': s['users']
        })

    return {'spikes': result}

def analyze_new_chatters_over_time(stream_id, bucket_minutes=5):
    """
    Returns time series of % messages that come from users seen for the first time during this stream.
    """
    stream = Stream.query.get(stream_id)
    if not stream:
        return {'times': [], 'ratios': []}

    messages = db.session.query(ChatMessage.timestamp, ChatMessage.username).filter(
        ChatMessage.stream_id == stream_id
    ).order_by(ChatMessage.timestamp.asc()).all()

    if not messages:
        return {'times': [], 'ratios': []}

    # Pre-fetch user first_seen dates?
    # Or just use the fact that if Viewer.first_seen >= stream.started_at, they are new.
    # Optimization: Get set of "new" usernames
    new_users = set(r[0] for r in db.session.query(Viewer.username).filter(
        Viewer.first_seen >= stream.started_at
    ).all())

    start_time = stream.started_at
    buckets = {} # bucket_idx -> {'total': 0, 'new': 0}

    for ts, username in messages:
        delta = ts - start_time
        minutes = int(delta.total_seconds() / 60)
        bucket_idx = (minutes // bucket_minutes) * bucket_minutes

        if bucket_idx not in buckets:
            buckets[bucket_idx] = {'total': 0, 'new': 0}

        buckets[bucket_idx]['total'] += 1
        if username in new_users:
            buckets[bucket_idx]['new'] += 1

    sorted_times = sorted(buckets.keys())
    times_out = []
    ratios_out = []

    for t in sorted_times:
        b = buckets[t]
        ratio = (b['new'] / b['total']) * 100 if b['total'] > 0 else 0
        times_out.append(t) # Minutes from start
        ratios_out.append(round(ratio, 1))

    return {'times': times_out, 'ratios': ratios_out}

def analyze_session_durations(stream_id):
    """
    Returns histogram of user session durations (last_seen - first_seen) in this stream.
    """
    stats = StreamViewerStats.query.filter_by(stream_id=stream_id).all()

    buckets = {
        '<1m': 0,
        '1-5m': 0,
        '5-15m': 0,
        '15-60m': 0,
        '>1h': 0
    }

    for s in stats:
        duration = (s.last_seen - s.first_seen).total_seconds()

        if duration < 60: buckets['<1m'] += 1
        elif duration < 300: buckets['1-5m'] += 1
        elif duration < 900: buckets['5-15m'] += 1
        elif duration < 3600: buckets['15-60m'] += 1
        else: buckets['>1h'] += 1

    return buckets

def get_inter_arrival_histogram(stream_id):
    """
    Calculates the distribution of time deltas between consecutive messages.
    """
    messages = db.session.query(ChatMessage.timestamp).filter(
        ChatMessage.stream_id == stream_id
    ).order_by(ChatMessage.timestamp.asc()).all()

    if len(messages) < 2:
        return {'bins': [], 'counts': []}

    deltas = []
    for i in range(1, len(messages)):
        diff = (messages[i][0] - messages[i-1][0]).total_seconds()
        deltas.append(diff)

    # Buckets: 0-1, 1-2, ... 9-10, >10
    buckets = {i: 0 for i in range(11)} # 0..10

    for d in deltas:
        if d >= 10:
            buckets[10] += 1
        else:
            buckets[int(d)] += 1

    labels = [f"{i}-{i+1}s" for i in range(10)] + [">10s"]
    counts = [buckets[i] for i in range(11)]

    return {'labels': labels, 'counts': counts}

def get_repeated_sequences(stream_id, n=3):
    """
    Finds top N-grams of messages (sequences of 3 messages).
    Useful to detect bot scripts repeating conversations.
    """
    messages = db.session.query(ChatMessage.message).filter(
        ChatMessage.stream_id == stream_id
    ).order_by(ChatMessage.timestamp.asc()).all()

    msgs = [m[0].strip().lower() for m in messages]
    if len(msgs) < n:
        return []

    sequences = []
    for i in range(len(msgs) - n + 1):
        seq = tuple(msgs[i:i+n])
        sequences.append(seq)

    # Filter out sequences that are just same word repeated? "lol", "lol", "lol"
    # Maybe not, that is also suspicious or just spam.

    counter = Counter(sequences)

    # Get top 20, but filter for count > 1
    most_common = counter.most_common(20)
    result = []
    for seq, count in most_common:
        if count > 1:
            result.append({'sequence': list(seq), 'count': count})

    return result

def get_chatter_viewer_correlation(stream_id):
    """
    Returns X (Viewers) and Y (Active Chatters) for scatter plot.
    """
    stats = StreamStats.query.filter_by(stream_id=stream_id).order_by(StreamStats.timestamp.asc()).all()

    data = []
    for s in stats:
        data.append({
            'x': s.viewer_count,
            'y': s.active_chatter_count, # or s.chatter_count? active is better for activity correlation
            'time': s.timestamp.isoformat()
        })
    return data

def get_sentiment_timeseries(stream_id, bucket_minutes=5):
    """
    Returns average sentiment over time.
    """
    stream = Stream.query.get(stream_id)
    if not stream:
        return {'times': [], 'sentiment': []}

    messages = db.session.query(ChatMessage.timestamp, ChatMessage.message).filter(
        ChatMessage.stream_id == stream_id
    ).order_by(ChatMessage.timestamp.asc()).all()

    start_time = stream.started_at

    buckets = {} # bucket_idx -> [scores]

    for ts, text in messages:
        delta = ts - start_time
        minutes = int(delta.total_seconds() / 60)
        bucket_idx = (minutes // bucket_minutes) * bucket_minutes

        blob = TextBlob(text)
        score = blob.sentiment.polarity

        if bucket_idx not in buckets:
            buckets[bucket_idx] = []
        buckets[bucket_idx].append(score)

    sorted_times = sorted(buckets.keys())
    times_out = []
    scores_out = []

    for t in sorted_times:
        scores = buckets[t]
        avg = sum(scores) / len(scores) if scores else 0
        times_out.append(t)
        scores_out.append(round(avg, 2))

    return {'times': times_out, 'sentiment': scores_out}

def get_cross_channel_graph(stream_id):
    """
    Builds a graph of users in this stream who are also present in other channels.
    Nodes: Users (Central), Channels (Linked).
    """
    # 1. Get usernames of current stream viewers
    current_usernames = [r[0] for r in db.session.query(Viewer.username).join(StreamViewerStats).filter(
        StreamViewerStats.stream_id == stream_id
    ).all()]

    if not current_usernames:
        return {'nodes': [], 'edges': []}

    # 2. Get active streams (excluding current)
    active_streams = Stream.query.filter(Stream.is_live == True, Stream.id != stream_id).all()
    active_stream_ids = [s.id for s in active_streams]

    if not active_stream_ids:
        return {'nodes': [], 'edges': []}

    # 3. Find these usernames in other active streams
    cross_presence = db.session.query(
        Viewer.username,
        StreamViewerStats.stream_id,
        Viewer.suspicion_score,
        Viewer.id
    ).join(StreamViewerStats).filter(
        StreamViewerStats.stream_id.in_(active_stream_ids),
        Viewer.username.in_(current_usernames)
    ).all()

    if not cross_presence:
        return {'nodes': [], 'edges': []}

    G = nx.Graph()

    # Map Stream IDs to Channel Names
    streams = Stream.query.filter(Stream.id.in_(active_stream_ids)).all()
    channel_map = {s.id: s.channel.name for s in streams}

    # Add Channel Nodes (Other Channels)
    for sid, name in channel_map.items():
        G.add_node(f"C_{sid}", label=name, type='channel')

    # Add User Nodes and Edges
    for username, sid, score, vid in cross_presence:
        u_node = f"U_{username}"

        if not G.has_node(u_node):
            G.add_node(u_node, label=username, type='user', score=score)

        if sid in channel_map:
            G.add_edge(u_node, f"C_{sid}")

    # Add Current Stream Node
    current_stream = Stream.query.get(stream_id)
    if current_stream:
        c_node = f"C_{stream_id}"
        G.add_node(c_node, label=current_stream.channel.name, type='channel', current=True)
        # Link all found users to this node too
        for username, _, _, _ in cross_presence:
             u_node = f"U_{username}"
             G.add_edge(u_node, c_node)

    # Format
    nodes = []
    for n, attr in G.nodes(data=True):
        if attr['type'] == 'channel':
            color = '#0053f1'
            if attr.get('current'): color = '#6610f2'
            nodes.append({
                'id': n,
                'label': attr['label'],
                'shape': 'box',
                'color': color,
                'font': {'color': 'white'}
            })
        else:
            color = '#28a745'
            if attr['score'] > 70: color = '#dc3545'
            elif attr['score'] > 30: color = '#ffc107'
            nodes.append({
                'id': n,
                'label': attr['label'],
                'shape': 'dot',
                'color': color,
                'size': 10
            })

    edges = [{'from': u, 'to': v} for u, v in G.edges()]

    return {'nodes': nodes, 'edges': edges}
