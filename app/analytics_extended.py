from app.models import db, StreamStats, ChatMessage, StreamViewerStats, Viewer, Stream
from datetime import datetime, timedelta
import Levenshtein
from sqlalchemy import func, distinct

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
