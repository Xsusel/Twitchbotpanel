from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash
from app.models import db, Channel, ChatMessage, StreamStats, AnalysisResult
from app.auth import login_required
from app.tasks import analyze_channel
from datetime import datetime, timedelta
import subprocess

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

@main.route('/update', methods=['POST'])
@login_required
def update_app():
    try:
        subprocess.run(["git", "pull"], check=True)
        flash('Update initiated (git pull). Please restart services.')
    except Exception as e:
        flash(f'Update failed: {e}')
    return redirect(url_for('main.index'))
