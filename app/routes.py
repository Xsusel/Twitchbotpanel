from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash, send_file, Response
from app.models import db, Channel, ChatMessage, StreamStats, AnalysisResult
from app.auth import login_required
from app.tasks import analyze_channel
from app.analysis import generate_wordcloud, analyze_sentiment
from app.network_analysis import generate_user_network
from app.reports import generate_pdf_report
from datetime import datetime, timedelta
import subprocess
import csv
import io

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
        channel_data.append({
            'id': c.id,
            'name': c.name,
            'is_active': c.is_active,
            'score': score
        })
    return render_template('dashboard.html', channels=channel_data)

@main.route('/channel/<int:channel_id>')
@login_required
def channel_detail(channel_id):
    channel = Channel.query.get_or_404(channel_id)
    return render_template('channel.html', channel=channel)

@main.route('/api/stats/<int:channel_id>')
@login_required
def api_channel_stats(channel_id):
    # Get stats for last 24 hours
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
    for m in messages:
        is_suspicious = False
        # Simple heuristic: if meta has flag or based on content
        # For now, let's mark repetitive messages as suspicious here too or leave it to frontend?
        # The plan says "Update app/routes.py: In /api/logs, add a suspicious flag".
        # Let's verify duplicates in the fetched batch

        logs.append({
            'username': m.username,
            'message': m.message,
            'timestamp': m.timestamp.strftime('%H:%M:%S'),
            'badges': m.badges,
            'suspicious': is_suspicious # Placeholder, will improve in next step
        })

    # Simple post-processing for duplicates in the current batch
    seen_messages = {}
    for log in logs:
        msg = log['message']
        if msg in seen_messages:
            log['suspicious'] = True
            seen_messages[msg]['suspicious'] = True # Mark the first one too? Maybe not.
        else:
            seen_messages[msg] = log

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
        flash("Not enough data for report.")
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

@main.route('/add_channel', methods=['POST'])
@login_required
def add_channel():
    name = request.form.get('channel_name')
    if name:
        if not Channel.query.filter_by(name=name).first():
            channel = Channel(name=name)
            db.session.add(channel)
            db.session.commit()
            flash(f'Channel {name} added.')
        else:
            flash(f'Channel {name} already exists.')
    return redirect(url_for('main.index'))

@main.route('/remove_channel/<int:channel_id>')
@login_required
def remove_channel(channel_id):
    channel = Channel.query.get(channel_id)
    if channel:
        db.session.delete(channel)
        db.session.commit()
        flash(f'Channel {channel.name} removed.')
    return redirect(url_for('main.index'))

@main.route('/analyze/<int:channel_id>')
@login_required
def trigger_analysis(channel_id):
    analyze_channel.delay(channel_id)
    flash('Analysis started.')
    return redirect(url_for('main.channel_detail', channel_id=channel_id))

@main.route('/user/<username>')
@login_required
def user_profile(username):
    # Fetch all messages from this user across all channels
    messages = ChatMessage.query.filter_by(username=username).order_by(ChatMessage.timestamp.desc()).all()

    if not messages:
        flash(f'User {username} not found in database.')
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
        subprocess.run(["git", "pull"], check=True)
        flash('Update initiated (git pull). Please restart services.')
    except Exception as e:
        flash(f'Update failed: {e}')
    return redirect(url_for('main.index'))
