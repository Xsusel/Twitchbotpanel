import sys
import os
import asyncio
import requests
from datetime import datetime
from collections import defaultdict

# Add root path to sys.path to allow imports from app
sys.path.append(os.getcwd())

from twitchio.ext import commands
from app import create_app
from app.models import db, Channel, ChatMessage, StreamStats, Stream, SystemConfig, Viewer, StreamViewerStats
from app.tasks import analyze_channel, cleanup_old_data
from config import Config
from flask_socketio import SocketIO

# External SocketIO to emit events to Flask
socketio = SocketIO(message_queue=Config.CELERY_BROKER_URL)

class Bot(commands.Bot):
    def __init__(self):
        # Initialize app first to access DB
        self.app = create_app()
        token = Config.TWITCH_IRC_TOKEN

        # Try to load token from DB
        try:
            with self.app.app_context():
                db_token = SystemConfig.query.get('twitch_irc_token')
                if db_token and db_token.value:
                    token = db_token.value
        except Exception as e:
            print(f"Warning: Could not load token from DB: {e}")

        super().__init__(token=token, prefix='?', initial_channels=[])
        self.channels_to_monitor = []
        self.last_cleanup = datetime.utcnow()
        self.active_speakers = defaultdict(set) # channel_name -> set(usernames)
        self.channel_ids = {} # name -> id (cache for API calls)
        self.bot_user_id = None # Cache for bot's own ID

    async def event_ready(self):
        print(f'Logged in as | {self.nick}')
        print(f'User id is | {self.user_id}')
        self.bot_user_id = self.user_id

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
        await asyncio.to_thread(self.save_message, message)

    def save_message(self, message):
        try:
            channel_name = message.channel.name
            username = message.author.name
            content = message.content
            timestamp = message.timestamp
            badges = message.author.badges

            # Extract additional viewer info
            author = message.author
            color = str(author.color) if author.color else None
            is_mod = author.is_mod
            is_subscriber = author.is_subscriber
            # Handle user ID (TwitchIO 2.x 'id' field for user)
            twitch_id = str(author.id) if hasattr(author, 'id') else None

            # Extract sub tier
            sub_tier = None
            if badges and 'subscriber' in badges:
                sub_tier = str(badges['subscriber'])

            # Update active speakers (thread-safe enough for this purpose)
            self.active_speakers[channel_name].add(username)

            with self.app.app_context():
                channel = Channel.query.filter_by(name=channel_name).first()
                if channel:
                    # Find active stream
                    stream = Stream.query.filter_by(channel_id=channel.id, is_live=True).order_by(Stream.started_at.desc()).first()
                    stream_id = stream.id if stream else None

                    # 1. Save Message
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

                    # 2. Update/Create Viewer
                    # Try to find existing viewer by username + channel
                    viewer = Viewer.query.filter_by(channel_id=channel.id, username=username).first()
                    if viewer:
                        viewer.last_seen = timestamp
                        viewer.message_count += 1
                        viewer.is_subscriber = bool(is_subscriber)
                        viewer.is_mod = bool(is_mod)
                        if sub_tier:
                            viewer.sub_tier = sub_tier
                        if color:
                            viewer.color = color

                        # Handle Nickname History
                        # If twitch_id matches but username is different, add old name to history
                        if twitch_id and viewer.twitch_id and viewer.twitch_id == twitch_id:
                            if viewer.username != username:
                                old_names = viewer.nick_history or []
                                if viewer.username not in old_names:
                                    old_names.append(viewer.username)
                                viewer.nick_history = old_names
                                viewer.username = username # Update to new name

                        if twitch_id:
                            viewer.twitch_id = twitch_id
                    else:
                        viewer = Viewer(
                            channel_id=channel.id,
                            username=username,
                            twitch_id=twitch_id,
                            first_seen=timestamp,
                            last_seen=timestamp,
                            message_count=1,
                            is_subscriber=bool(is_subscriber),
                            is_mod=bool(is_mod),
                            sub_tier=sub_tier,
                            color=color
                        )
                        db.session.add(viewer)
                        db.session.commit() # Commit to get ID

                    # 3. Update StreamViewerStats (if stream is live)
                    if stream_id:
                         sv_stats = StreamViewerStats.query.filter_by(stream_id=stream_id, viewer_id=viewer.id).first()
                         if sv_stats:
                             sv_stats.message_count += 1
                             sv_stats.last_seen = timestamp
                         else:
                             sv_stats = StreamViewerStats(
                                 stream_id=stream_id,
                                 viewer_id=viewer.id,
                                 message_count=1,
                                 first_seen=timestamp,
                                 last_seen=timestamp
                             )
                             db.session.add(sv_stats)

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

    async def fetch_real_chatter_count(self, channel_name):
        """
        Fetches the accurate chatter count using Twitch Helix API.
        Requires the bot to be a moderator to get the full list,
        but we can try to get the list/count if possible.
        Actually, GET /chat/chatters requires 'moderator:read:chatters'.
        """
        try:
            broadcaster_id = self.channel_ids.get(channel_name)
            if not broadcaster_id:
                # Try to fetch user to get ID
                users = await self.fetch_users(names=[channel_name])
                if users:
                    broadcaster_id = str(users[0].id)
                    self.channel_ids[channel_name] = broadcaster_id

            if not broadcaster_id or not self.bot_user_id:
                return 0

            token = Config.TWITCH_IRC_TOKEN.replace("oauth:", "")
            headers = {
                'Client-ID': Config.TWITCH_CLIENT_ID,
                'Authorization': f'Bearer {token}'
            }

            # Helper to handle pagination if needed, but for count we might just get first page total?
            # Twitch API response for chatters includes 'total'.
            url = f"https://api.twitch.tv/helix/chat/chatters"
            params = {
                'broadcaster_id': broadcaster_id,
                'moderator_id': self.bot_user_id,
                'first': 1 # We just want the total
            }

            # We need to run this sync request in a thread or use aiohttp if available
            # Since requests is sync, wrap in to_thread
            resp = await asyncio.to_thread(requests.get, url, headers=headers, params=params)

            if resp.status_code == 200:
                data = resp.json()
                return data.get('total', 0)
            elif resp.status_code == 401:
                 print(f"API Unauthorized for {channel_name}. Check token scopes.")
            elif resp.status_code == 403:
                 # Likely not a moderator
                 pass

            return 0
        except Exception as e:
            print(f"Error fetching real chatter count: {e}")
            return 0

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
                             # Cache ID
                             self.channel_ids[s.user.name.lower()] = str(s.user.id)

                        fetch_success = True
                    except Exception as e:
                        print(f"Error fetching streams: {e}")

                    # Run save_stats in thread ONLY if fetch was successful
                    if fetch_success:
                        # Fetch chatter counts concurrently
                        chatter_counts = {}
                        for name in self.channels_to_monitor:
                            if name.lower() in stream_data: # Only if live
                                count = await self.fetch_real_chatter_count(name.lower())
                                if count > 0:
                                    chatter_counts[name.lower()] = count

                        await asyncio.to_thread(self.save_stats, self.channels_to_monitor, stream_data, chatter_counts)

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

    def save_stats(self, channel_names, stream_data, chatter_counts):
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

                    # Get chatter count
                    # Priority: API count > IRC cache > 0
                    chatter_count = chatter_counts.get(name.lower(), 0)

                    if chatter_count == 0 and is_live:
                        # Fallback to IRC cache if API failed or returned 0 (and we know it shouldn't be 0 if live?)
                        # Actually 0 is valid. But if API failed (403/401), we might want fallback.
                        # For now, let's just use what we have.
                        try:
                            channel = self.get_channel(name)
                            if channel and channel.chatters:
                                irc_count = len(channel.chatters)
                                if irc_count > chatter_count:
                                    chatter_count = irc_count
                        except Exception as e:
                            print(f"Error getting chatter count from IRC: {e}")

                    # Calculate active chatters
                    active_count = len(self.active_speakers.get(name, set()))
                    # Clear the set for next interval
                    if name in self.active_speakers:
                        self.active_speakers[name].clear()

                    stats = StreamStats(
                        channel_id=c.id,
                        stream_id=active_stream.id if active_stream else None,
                        viewer_count=viewer_count,
                        chatter_count=chatter_count,
                        active_chatter_count=active_count
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
