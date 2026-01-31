from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash, send_file, Response
from app.models import db, Channel, ChatMessage, StreamStats, AnalysisResult, Stream, SystemConfig, Viewer
from app.auth import login_required
from app.tasks import analyze_channel
from app.analysis import generate_wordcloud, analyze_sentiment
from app.network_analysis import generate_user_network
from app.reports import generate_pdf_report
from datetime import datetime, timedelta
import subprocess
import sys
import csv
import io
import requests
from config import Config

main = Blueprint('main', __name__)

@main.route('/')
@login_required
def index():
    channels = Channel.query.all()
    # Get latest analysis for each channel
    channel_data = []
    for c in channels:
        latest_analysis = AnalysisResult.query.filter_by(channel_id=c.id).order_by(AnalysisResult.timestamp.desc()).first()
        score = latest_analysis.bot_score if latest_analysis else 0

        # Get active stream info if live
        active_stream = Stream.query.filter_by(channel_id=c.id, is_live=True).first()

        channel_data.append({
            'id': c.id,
            'name': c.name,
            'is_active': c.is_active,
            'score': score,
            'is_live': active_stream is not None,
            'game_name': active_stream.game_name if active_stream else "",
            'title': active_stream.title if active_stream else ""
        })
    return render_template('dashboard.html', channels=channel_data)

@main.route('/channel/<int:channel_id>')
@login_required
def channel_detail(channel_id):
    channel = Channel.query.get_or_404(channel_id)
    return render_template('channel.html', channel=channel)

@main.route('/api/streams/<int:channel_id>')
@login_required
def api_streams_list(channel_id):
    # List all streams for this channel, newest first
    streams = Stream.query.filter_by(channel_id=channel_id).order_by(Stream.started_at.desc()).limit(100).all()
    data = []
    for s in streams:
        duration = ""
        if s.ended_at:
             delta = s.ended_at - s.started_at
             duration = str(delta).split('.')[0]
        elif s.is_live:
             delta = datetime.utcnow() - s.started_at
             duration = str(delta).split('.')[0] + " (Live)"

        data.append({
            'id': s.id,
            'title': s.title,
            'game_name': s.game_name,
            'started_at': s.started_at.strftime('%Y-%m-%d %H:%M'),
            'duration': duration,
            'is_live': s.is_live
        })
    return jsonify(data)

@main.route('/stream/<int:stream_id>')
@login_required
def stream_detail(stream_id):
    stream = Stream.query.get_or_404(stream_id)
    return render_template('stream_detail.html', stream=stream)

@main.route('/api/stream/<int:stream_id>/stats')
@login_required
def api_stream_stats(stream_id):
    stats = StreamStats.query.filter_by(stream_id=stream_id).order_by(StreamStats.timestamp.asc()).all()
    data = {
        'labels': [s.timestamp.strftime('%H:%M') for s in stats],
        'viewers': [s.viewer_count for s in stats],
        'chatters': [s.chatter_count for s in stats]
    }
    return jsonify(data)

@main.route('/api/stream/<int:stream_id>/logs')
@login_required
def api_stream_logs(stream_id):
    limit = request.args.get('limit', 1000, type=int)
    messages = ChatMessage.query.filter_by(stream_id=stream_id).order_by(ChatMessage.timestamp.asc()).limit(limit).all()

    logs = []
    for m in messages:
        logs.append({
            'username': m.username,
            'message': m.message,
            'timestamp': m.timestamp.strftime('%H:%M:%S'),
            'badges': m.badges
        })
    return jsonify(logs)

@main.route('/api/stats/<int:channel_id>')
@login_required
def api_channel_stats(channel_id):
    # Get stats for last 24 hours OR current active stream
    # If there is an active stream, prefer its stats?
    # Current behavior: last 24h. Let's keep it but also support `?stream_id=` param

    stream_id = request.args.get('stream_id', type=int)

    if stream_id:
        stats = StreamStats.query.filter_by(stream_id=stream_id).order_by(StreamStats.timestamp.asc()).all()
    else:
        since = datetime.utcnow() - timedelta(hours=24)
        stats = StreamStats.query.filter_by(channel_id=channel_id).filter(StreamStats.timestamp >= since).order_by(StreamStats.timestamp.asc()).all()

    data = {
        'labels': [s.timestamp.strftime('%H:%M') for s in stats],
        'viewers': [s.viewer_count for s in stats],
        'chatters': [s.chatter_count for s in stats]
    }
    return jsonify(data)

@main.route('/api/analysis/<int:channel_id>')
@login_required
def api_channel_analysis(channel_id):
    # Support fetching analysis for a specific stream if needed, but usually we just want latest
    latest = AnalysisResult.query.filter_by(channel_id=channel_id).order_by(AnalysisResult.timestamp.desc()).first()
    if not latest:
        return jsonify({})
    return jsonify(latest.details)

@main.route('/api/logs/<int:channel_id>')
@login_required
def api_channel_logs(channel_id):
    # Get recent messages
    limit = request.args.get('limit', 100, type=int)
    messages = ChatMessage.query.filter_by(channel_id=channel_id).order_by(ChatMessage.timestamp.desc()).limit(limit).all()

    logs = []
    seen_messages = {}

    for m in messages:
        logs.append({
            'username': m.username,
            'message': m.message,
            'timestamp': m.timestamp.strftime('%H:%M:%S'),
            'badges': m.badges,
            'suspicious': False
        })

    # Mark duplicates
    processed_logs = []
    # Process in reverse (chronological) to find first instance?
    # Actually for "Live" view, we just want to flag if it repeats often.
    # Simple check:
    content_counts = {}
    for log in logs:
        msg = log['message']
        content_counts[msg] = content_counts.get(msg, 0) + 1

    for log in logs:
        if content_counts[log['message']] > 3: # Arbitrary threshold
            log['suspicious'] = True

    return jsonify(logs)

@main.route('/api/wordcloud/<int:channel_id>')
@login_required
def api_wordcloud(channel_id):
    # Get last 1000 messages
    messages = ChatMessage.query.filter_by(channel_id=channel_id).order_by(ChatMessage.timestamp.desc()).limit(1000).all()
    img_io = generate_wordcloud(messages)
    if not img_io:
        # Return empty 1x1 png or 404
        return "No data", 404

    return send_file(img_io, mimetype='image/png')

@main.route('/export/chat/<int:channel_id>')
@login_required
def export_chat(channel_id):
    channel = Channel.query.get_or_404(channel_id)
    # Get last 10k messages
    messages = ChatMessage.query.filter_by(channel_id=channel_id).order_by(ChatMessage.timestamp.desc()).limit(10000).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'Username', 'Message', 'Badges'])

    for m in messages:
        writer.writerow([m.timestamp.isoformat(), m.username, m.message, m.badges])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=chat_{channel.name}.csv"}
    )

@main.route('/export/pdf/<int:channel_id>')
@login_required
def export_pdf(channel_id):
    channel = Channel.query.get_or_404(channel_id)
    latest_analysis = AnalysisResult.query.filter_by(channel_id=channel_id).order_by(AnalysisResult.timestamp.desc()).first()
    latest_stats = StreamStats.query.filter_by(channel_id=channel_id).order_by(StreamStats.timestamp.desc()).first()
    messages = ChatMessage.query.filter_by(channel_id=channel_id).order_by(ChatMessage.timestamp.desc()).limit(100).all()

    if not latest_analysis or not latest_stats:
        flash("Brak wystarczających danych do raportu.")
        return redirect(url_for('main.channel_detail', channel_id=channel_id))

    pdf_bytes = generate_pdf_report(channel, latest_stats, latest_analysis, messages)

    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={"Content-disposition": f"attachment; filename=report_{channel.name}.pdf"}
    )

@main.route('/api/history/<int:channel_id>')
@login_required
def api_history(channel_id):
    # Last 7 days scores
    since = datetime.utcnow() - timedelta(days=7)
    analyses = AnalysisResult.query.filter_by(channel_id=channel_id).filter(AnalysisResult.timestamp >= since).order_by(AnalysisResult.timestamp.asc()).all()

    data = {
        'labels': [a.timestamp.strftime('%Y-%m-%d %H:%M') for a in analyses],
        'scores': [a.bot_score for a in analyses]
    }
    return jsonify(data)

@main.route('/api/network/<int:channel_id>')
@login_required
def api_network(channel_id):
    # Analyze last 500 messages for network graph
    messages = ChatMessage.query.filter_by(channel_id=channel_id).order_by(ChatMessage.timestamp.desc()).limit(500).all()
    graph_data = generate_user_network(messages)
    return jsonify(graph_data)

@main.route('/api/viewers/<int:channel_id>')
@login_required
def api_viewers(channel_id):
    # Get all viewers for this channel, sorted by message count
    viewers = Viewer.query.filter_by(channel_id=channel_id).order_by(Viewer.message_count.desc()).limit(1000).all()

    data = []
    for v in viewers:
        data.append({
            'username': v.username,
            'message_count': v.message_count,
            'is_subscriber': v.is_subscriber,
            'is_mod': v.is_mod,
            'last_seen': v.last_seen.strftime('%Y-%m-%d %H:%M:%S'),
            'color': v.color
        })
    return jsonify(data)

@main.route('/add_channel', methods=['POST'])
@login_required
def add_channel():
    name = request.form.get('channel_name')
    if name:
        name = name.strip()
        if not Channel.query.filter_by(name=name).first():
            channel = Channel(name=name)
            db.session.add(channel)
            db.session.commit()
            flash(f'Kanał {name} dodany.')
        else:
            flash(f'Kanał {name} już istnieje.')
    return redirect(url_for('main.index'))

@main.route('/remove_channel/<int:channel_id>')
@login_required
def remove_channel(channel_id):
    channel = Channel.query.get(channel_id)
    if channel:
        db.session.delete(channel)
        db.session.commit()
        flash(f'Kanał {channel.name} usunięty.')
    return redirect(url_for('main.index'))

@main.route('/analyze/<int:channel_id>')
@login_required
def trigger_analysis(channel_id):
    analyze_channel.delay(channel_id)
    flash('Analiza rozpoczęta.')
    return redirect(url_for('main.channel_detail', channel_id=channel_id))

@main.route('/user/<username>')
@login_required
def user_profile(username):
    # Fetch all messages from this user across all channels
    messages = ChatMessage.query.filter_by(username=username).order_by(ChatMessage.timestamp.desc()).all()

    if not messages:
        flash(f'Użytkownik {username} nie znaleziony w bazie.')
        return redirect(url_for('main.index'))

    total_messages = len(messages)
    channels_seen = set(m.channel_id for m in messages)

    # Calculate sentiment
    sentiment = analyze_sentiment(messages)

    # Activity Heatmap (Messages per Hour of Day)
    activity_data = [0] * 24
    for m in messages:
        activity_data[m.timestamp.hour] += 1

    return render_template('user.html',
                           username=username,
                           total_messages=total_messages,
                           channels_count=len(channels_seen),
                           avg_sentiment=sentiment['polarity'],
                           activity_data=activity_data,
                           recent_messages=messages[:50])

@main.route('/update', methods=['POST'])
@login_required
def update_app():
    try:
        # Now calls update_code.sh which handles migration
        # But we can't run the shell script from here directly if it requires root or passwordless sudo for restart
        # The script `update_code.sh` checks for EUID 0.
        # This route is likely just triggering git pull.
        # Let's assume the user has configured this to work or it's a "soft" update.
        # However, for a proper update including restarts, it's usually done via CLI.
        # If this button just does git pull, that's what I'll leave it as, but I'll try to add the migration step here too.

        subprocess.run(["git", "pull"], check=True)
        # Install dependencies
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
        # Attempt migration via python directly
        subprocess.run(["python3", "migrate_db.py"], check=True)

        flash('Rozpoczęto aktualizację, instalację zależności i migrację. W razie potrzeby zrestartuj usługi ręcznie.')
    except Exception as e:
        flash(f'Aktualizacja nieudana: {e}')
    return redirect(url_for('main.index'))

@main.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        client_id = request.form.get('twitch_client_id')
        client_secret = request.form.get('twitch_client_secret')
        irc_token = request.form.get('twitch_irc_token')

        keys = {
            'twitch_client_id': client_id,
            'twitch_client_secret': client_secret,
            'twitch_irc_token': irc_token
        }

        for key, value in keys.items():
            conf = SystemConfig.query.get(key)
            if not conf:
                conf = SystemConfig(key=key)
                db.session.add(conf)
            conf.value = value

        db.session.commit()
        flash('Konfiguracja zapisana. Uruchom ponownie bota, aby zastosować zmiany.')
        return redirect(url_for('main.settings'))

    # Load config
    configs = SystemConfig.query.all()
    config_data = {c.key: c.value for c in configs}
    return render_template('settings.html', config_data=config_data)

@main.route('/api/check_twitch')
@login_required
def check_api():
    # Try loading from DB first
    token_conf = SystemConfig.query.get('twitch_irc_token')
    client_id_conf = SystemConfig.query.get('twitch_client_id')

    token = token_conf.value if token_conf else Config.TWITCH_IRC_TOKEN
    client_id = client_id_conf.value if client_id_conf else Config.TWITCH_CLIENT_ID

    if not token or not client_id:
        return jsonify({'status': 'error', 'message': 'Brak kluczy API'})

    # Validate token
    headers = {
        'Authorization': f'Bearer {token.replace("oauth:", "")}',
        'Client-Id': client_id
    }

    try:
        resp = requests.get('https://id.twitch.tv/oauth2/validate', headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            return jsonify({'status': 'ok', 'user': data.get('login')})
        else:
            return jsonify({'status': 'error', 'message': f'Błąd API: {resp.status_code} - {resp.text}'})
    except Exception as e:
         return jsonify({'status': 'error', 'message': str(e)})
