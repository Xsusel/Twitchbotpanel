import sys
import os
import asyncio
import requests
from datetime import datetime

# Add root path to sys.path to allow imports from app
sys.path.append(os.getcwd())

from twitchio.ext import commands
from app import create_app
from app.models import db, Channel, ChatMessage, StreamStats, Stream
from app.tasks import analyze_channel, cleanup_old_data
from config import Config
from flask_socketio import SocketIO

# External SocketIO to emit events to Flask
socketio = SocketIO(message_queue=Config.CELERY_BROKER_URL)

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(token=Config.TWITCH_IRC_TOKEN, prefix='?', initial_channels=[])
        self.app = create_app()
        self.channels_to_monitor = []
        self.last_cleanup = datetime.utcnow()

    async def event_ready(self):
        print(f'Logged in as | {self.nick}')
        print(f'User id is | {self.user_id}')

        await self.load_channels()
        # Start background task
        self.loop.create_task(self.background_monitor())

    async def load_channels(self):
        print("Loading channels from DB...")
        try:
            with self.app.app_context():
                channels = Channel.query.filter_by(is_active=True).all()
                self.channels_to_monitor = [c.name.strip() for c in channels]

            if self.channels_to_monitor:
                print(f"Monitoring channels: {self.channels_to_monitor}")
                await self.join_channels(self.channels_to_monitor)
            else:
                print("No active channels found in DB.")
        except Exception as e:
            print(f"Error loading channels: {e}")

    async def event_message(self, message):
        if message.echo:
            return

        # Run DB operation in thread
        await asyncio.to_thread(self.save_message, message.channel.name, message.author.name, message.content, message.timestamp, message.author.badges)

    def save_message(self, channel_name, username, content, timestamp, badges):
        try:
            with self.app.app_context():
                channel = Channel.query.filter_by(name=channel_name).first()
                if channel:
                    # Find active stream
                    stream = Stream.query.filter_by(channel_id=channel.id, is_live=True).order_by(Stream.started_at.desc()).first()
                    stream_id = stream.id if stream else None

                    new_msg = ChatMessage(
                        channel_id=channel.id,
                        stream_id=stream_id,
                        username=username,
                        message=content,
                        timestamp=timestamp,
                        badges=badges,
                        meta={}
                    )
                    db.session.add(new_msg)
                    db.session.commit()

                    # Emit real-time event
                    try:
                        socketio.emit('new_message', {
                            'channel_id': channel.id,
                            'username': username,
                            'message': content,
                            'timestamp': timestamp.strftime('%H:%M:%S'),
                            'badges': badges
                        })
                    except Exception as e:
                        print(f"SocketIO emit error: {e}")

        except Exception as e:
            print(f"Error saving message: {e}")

    async def background_monitor(self):
        while True:
            try:
                # Reload channels
                with self.app.app_context():
                     channels = Channel.query.filter_by(is_active=True).all()
                     current_channels = [c.name.strip() for c in channels]

                # Identify changes
                new_channels = list(set(current_channels) - set(self.channels_to_monitor))
                removed_channels = list(set(self.channels_to_monitor) - set(current_channels))

                if new_channels:
                    print(f"Joining new channels: {new_channels}")
                    await self.join_channels(new_channels)

                if removed_channels:
                    print(f"Leaving channels: {removed_channels}")
                    try:
                        await self.part_channels(removed_channels)
                    except Exception as e:
                        print(f"Error leaving channels: {e}")

                self.channels_to_monitor = current_channels

                if self.channels_to_monitor:
                    stream_data = {} # name -> {viewer_count, title, game_name}
                    fetch_success = False
                    try:
                        # Use lowercase for API call
                        user_logins = [name.lower() for name in self.channels_to_monitor]
                        streams = await self.fetch_streams(user_logins=user_logins)

                        print(f"Fetched {len(streams)} live streams.")

                        for s in streams:
                             stream_data[s.user.name.lower()] = {
                                 "viewer_count": s.viewer_count,
                                 "title": s.title,
                                 "game_name": s.game_name
                             }
                        fetch_success = True
                    except Exception as e:
                        print(f"Error fetching streams: {e}")

                    # Run save_stats in thread ONLY if fetch was successful
                    if fetch_success:
                        await asyncio.to_thread(self.save_stats, self.channels_to_monitor, stream_data)

                # Periodic Cleanup (Once a day)
                now = datetime.utcnow()
                if (now - self.last_cleanup).total_seconds() > 86400:
                    await asyncio.to_thread(self.trigger_cleanup)
                    self.last_cleanup = now

            except Exception as e:
                print(f"Error in background monitor: {e}")

            await asyncio.sleep(30) # 30 seconds

    def trigger_cleanup(self):
        try:
             cleanup_old_data.delay()
             print("Triggered daily cleanup task.")
        except Exception as e:
             print(f"Error triggering cleanup: {e}")

    def save_stats(self, channel_names, stream_data):
        with self.app.app_context():
            for name in channel_names:
                try:
                    c = Channel.query.filter_by(name=name).first()
                    if not c:
                        continue

                    data = stream_data.get(name.lower())
                    is_live = data is not None

                    # Manage Stream Session
                    active_stream = Stream.query.filter_by(channel_id=c.id, is_live=True).order_by(Stream.started_at.desc()).first()

                    if is_live:
                        if not active_stream:
                            # Start new stream
                            active_stream = Stream(
                                channel_id=c.id,
                                title=data['title'],
                                game_name=data['game_name'],
                                is_live=True
                            )
                            db.session.add(active_stream)
                            db.session.commit() # Commit to get ID
                        else:
                            # Update metadata if changed
                            if active_stream.title != data['title'] or active_stream.game_name != data['game_name']:
                                active_stream.title = data['title']
                                active_stream.game_name = data['game_name']
                    else:
                        if active_stream:
                            # End stream
                            active_stream.is_live = False
                            active_stream.ended_at = datetime.utcnow()
                            db.session.add(active_stream)
                            # active_stream variable remains valid for this iteration, but we won't link stats to it if offline
                            # Actually, if we just went offline, maybe we shouldn't link stats?
                            # Or link to the just-ended stream? Let's treat offline stats as no stream for now.
                            active_stream = None

                    viewer_count = data['viewer_count'] if is_live else 0

                    # Get chatter count from TMI (only if live or check anyway?)
                    chatter_count = 0
                    if is_live:
                        try:
                            tmi_url = f"https://tmi.twitch.tv/group/user/{name.lower()}/chatters"
                            resp = requests.get(tmi_url, timeout=5)
                            if resp.status_code == 200:
                                data_tmi = resp.json()
                                chatter_count = data_tmi.get('chatter_count', 0)
                        except Exception:
                            pass # Ignore TMI errors

                    stats = StreamStats(
                        channel_id=c.id,
                        stream_id=active_stream.id if active_stream else None,
                        viewer_count=viewer_count,
                        chatter_count=chatter_count
                    )
                    db.session.add(stats)

                    # Trigger analysis (only if live)
                    if is_live:
                        analyze_channel.delay(c.id)

                except Exception as e:
                    print(f"Error processing stats for {name}: {e}")

            try:
                db.session.commit()
            except Exception as e:
                print(f"Error committing stats: {e}")
                db.session.rollback()

if __name__ == "__main__":
    if not Config.TWITCH_IRC_TOKEN:
        print("TWITCH_IRC_TOKEN not set in .env")
    else:
        bot = Bot()
        bot.run()
