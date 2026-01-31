import Levenshtein
from langdetect import detect, LangDetectException
import statistics
from datetime import datetime
from textblob import TextBlob
from wordcloud import WordCloud
import io
import base64
from app.models import StreamStats

def calculate_ratio(viewer_count, chatter_count):
    if viewer_count == 0:
        return 0.0
    return chatter_count / viewer_count

def calculate_badge_density(messages):
    """
    Calculates the percentage of messages from users with NO badges.
    messages: list of ChatMessage objects or dicts with 'badges' key.
    """
    if not messages:
        return 0.0

    no_badge_count = 0
    for msg in messages:
        badges = msg.badges if hasattr(msg, 'badges') else msg.get('badges', {})
        if not badges:
            no_badge_count += 1

    return (no_badge_count / len(messages)) * 100

def calculate_entropy(messages):
    """
    Calculates average Levenshtein distance between consecutive messages.
    Lower value means higher similarity (more repetitive).
    """
    if not messages or len(messages) < 2:
        return 100.0 # High entropy (safe)

    distances = []
    texts = [msg.message for msg in messages if hasattr(msg, 'message')]
    if not texts:
        texts = [msg.get('message', '') for msg in messages]

    for i in range(len(texts) - 1):
        dist = Levenshtein.distance(texts[i], texts[i+1])
        distances.append(dist)

    avg_distance = sum(distances) / len(distances)
    # Normalize?
    # If average distance is low (e.g. 1-2 chars), it's very repetitive.
    # If average distance is high (e.g. 20+), it's varied.
    return avg_distance

def calculate_timing_variance(messages):
    """
    Calculates variance of time differences between messages.
    Low variance means mechanical precision.
    """
    if not messages or len(messages) < 2:
        return 1.0 # arbitrary non-zero

    timestamps = [msg.timestamp for msg in messages if hasattr(msg, 'timestamp')]
    if not timestamps:
         # Fallback for dicts if needed, assuming ISO strings or datetime objects
         pass

    # Ensure timestamps are sorted
    timestamps.sort()

    diffs = []
    for i in range(len(timestamps) - 1):
        diff = (timestamps[i+1] - timestamps[i]).total_seconds()
        diffs.append(diff)

    if not diffs:
        return 0.0

    if len(diffs) < 2:
        return 0.0

    try:
        variance = statistics.variance(diffs)
    except statistics.StatisticsError:
        return 0.0

    return variance

ALLOWED_LANGS = {'pl', 'en', 'de', 'ru', 'uk'}

def normalize_lang(lang):
    if lang in ALLOWED_LANGS:
        return lang
    return 'other'

def detect_dominant_language(messages):
    if not messages:
        return "unknown"

    text_content = " ".join([msg.message for msg in messages if hasattr(msg, 'message')])
    if not text_content:
        return "unknown"

    try:
        lang = detect(text_content)
        return normalize_lang(lang)
    except LangDetectException:
        return "unknown"

def get_language_distribution(messages):
    """
    Returns a dictionary {lang_code: count}.
    """
    if not messages:
        return {}

    counts = {}
    for msg in messages:
        text = msg.message if hasattr(msg, 'message') else msg.get('message', '')
        if len(text) < 3: # Skip very short messages
            continue
        try:
            lang = detect(text)
            lang = normalize_lang(lang)
        except LangDetectException:
            lang = 'unknown'

        counts[lang] = counts.get(lang, 0) + 1

    return counts

STOPWORDS = set([
    'i', 'w', 'na', 'z', 'do', 'o', 'a', 'że', 'to', 'jest', 'nie', 'ale', 'się', 'co', 'jak', 'tak',
    'mnie', 'mi', 'ci', 'mu', 'jej', 'nam', 'wam', 'im', 'ten', 'ta', 'to', 'tam', 'tu', 'gdzie', 'kiedy',
    'the', 'and', 'is', 'a', 'in', 'to', 'of', 'it', 'for', 'on', 'with', 'as', 'this', 'that', 'but',
    'be', 'at', 'by', 'not', 'or', 'from', 'you', 'are', 'me', 'my', 'your', 'so', 'was', 'if', 'lol', 'xd', 'lmao'
])

def extract_trending_topics(messages, top_n=10):
    if not messages:
        return []

    text = " ".join([msg.message for msg in messages if hasattr(msg, 'message')]).lower()
    # Remove punctuation basic
    import re
    text = re.sub(r'[^\w\s]', '', text)
    words = text.split()

    # Filter
    filtered = [w for w in words if len(w) > 3 and w not in STOPWORDS]

    from collections import Counter
    counts = Counter(filtered)
    return [t[0] for t in counts.most_common(top_n)]

def analyze_usernames(messages):
    """
    Analyzes usernames for bot-like patterns (high digit density).
    Returns a score 0-100 (100 = suspicious).
    """
    if not messages:
        return 0.0

    suspicious_count = 0
    unique_users = set()

    for msg in messages:
        username = msg.username if hasattr(msg, 'username') else msg.get('username', '')
        if username in unique_users:
            continue
        unique_users.add(username)

        # Check digit density
        digits = sum(c.isdigit() for c in username)
        if len(username) > 0 and (digits / len(username)) > 0.4:
            suspicious_count += 1

    if not unique_users:
        return 0.0

    return (suspicious_count / len(unique_users)) * 100

def analyze_global_repetition(messages):
    """
    Detects if multiple different users are sending the exact same message.
    Returns a score 0-100.
    """
    if not messages:
        return 0.0

    msg_map = {} # content -> set(usernames)

    for msg in messages:
        content = msg.message if hasattr(msg, 'message') else msg.get('message', '')
        username = msg.username if hasattr(msg, 'username') else msg.get('username', '')

        if content not in msg_map:
            msg_map[content] = set()
        msg_map[content].add(username)

    # Count messages sent by > 1 distinct users
    multi_user_msgs = 0
    total_msgs = len(messages)

    for content, users in msg_map.items():
        if len(users) > 2: # Same message from 3+ different users
            multi_user_msgs += len(users)

    return (multi_user_msgs / total_msgs) * 100 if total_msgs > 0 else 0.0

def analyze_sentiment(messages):
    """
    Analyzes sentiment of messages.
    Returns a dict with average polarity and subjectivity.
    Polarity: -1.0 (Negative) to 1.0 (Positive)
    Subjectivity: 0.0 (Objective) to 1.0 (Subjective)
    """
    if not messages:
        return {'polarity': 0.0, 'subjectivity': 0.0}

    polarities = []
    subjectivities = []

    for msg in messages:
        text = msg.message if hasattr(msg, 'message') else msg.get('message', '')
        blob = TextBlob(text)
        polarities.append(blob.sentiment.polarity)
        subjectivities.append(blob.sentiment.subjectivity)

    avg_polarity = sum(polarities) / len(polarities)
    avg_subjectivity = sum(subjectivities) / len(subjectivities)

    return {
        'polarity': round(avg_polarity, 2),
        'subjectivity': round(avg_subjectivity, 2)
    }

def generate_wordcloud(messages):
    """
    Generates a word cloud image (PNG bytes).
    """
    if not messages:
        return None

    text = " ".join([msg.message if hasattr(msg, 'message') else msg.get('message', '') for msg in messages])
    if not text.strip():
        return None

    wc = WordCloud(width=800, height=400, background_color='black', colormap='Blues').generate(text)

    img_io = io.BytesIO()
    wc.to_image().save(img_io, 'PNG')
    img_io.seek(0)
    return img_io

def calculate_bot_score(viewer_count, chatter_count, messages):
    """
    Returns a score from 0 to 100.
    100 = High probability of botting.
    """
    score = 0.0

    # 1. Ratio Check
    # If ratio < 15%, alarm.
    ratio = calculate_ratio(viewer_count, chatter_count)
    if ratio < 0.15 and viewer_count > 10: # Only if significant viewers
        score += 40
    elif ratio < 0.25 and viewer_count > 10:
        score += 20

    if not messages:
        return score

    # 2. Badge Density
    # If > 80% are grey accounts
    grey_percent = calculate_badge_density(messages)
    if grey_percent > 80:
        score += 20
    elif grey_percent > 50:
        score += 10

    # 3. Entropy (Repetitiveness)
    # If avg distance is very low (< 5), messages are very similar
    entropy = calculate_entropy(messages)
    if entropy < 5:
        score += 20
    elif entropy < 10:
        score += 10

    # 4. Timing Variance
    # If variance is very low (< 0.1), it's mechanical
    variance = calculate_timing_variance(messages)
    if variance < 0.1 and len(messages) > 10:
        score += 20

    # 5. Username Patterns
    user_score = analyze_usernames(messages)
    if user_score > 30:
        score += 10
    if user_score > 50:
        score += 10

    # 6. Global Repetition (Bot Net)
    rep_score = analyze_global_repetition(messages)
    if rep_score > 10:
        score += 10
    if rep_score > 30:
        score += 20

    return min(score, 100.0)

def analyze_viewer_growth(stream_id):
    """
    Analyzes viewer count growth rate to detect unnatural spikes.
    Returns dict with growth metrics.
    """
    if not stream_id:
        return {}

    # Get stats for stream sorted by time
    stats = StreamStats.query.filter_by(stream_id=stream_id).order_by(StreamStats.timestamp.asc()).all()
    if len(stats) < 2:
        return {'has_spike': False}

    # Calculate derivative (viewers per minute)
    spikes = []
    max_growth_rate = 0

    for i in range(1, len(stats)):
        prev = stats[i-1]
        curr = stats[i]

        time_diff = (curr.timestamp - prev.timestamp).total_seconds() / 60
        if time_diff == 0: continue

        viewer_diff = curr.viewer_count - prev.viewer_count
        rate = viewer_diff / time_diff # viewers per minute

        if rate > max_growth_rate:
            max_growth_rate = rate

        # Threshold: > 100 viewers/min or > 50% increase in 1 min is suspicious
        if rate > 100 or (prev.viewer_count > 0 and (viewer_diff / prev.viewer_count) > 0.5):
            spikes.append({
                'time': curr.timestamp.isoformat(),
                'rate': rate,
                'from': prev.viewer_count,
                'to': curr.viewer_count
            })

    return {
        'has_spike': len(spikes) > 0,
        'spike_count': len(spikes),
        'max_growth_rate': max_growth_rate,
        'spikes': spikes
    }
