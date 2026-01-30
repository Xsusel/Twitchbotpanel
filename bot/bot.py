import sys
import os
import asyncio
import requests
from datetime import datetime

# Add root path to sys.path to allow imports from app
sys.path.append(os.getcwd())

from twitchio.ext import commands
from app import create_app
from app.models import db, Channel, ChatMessage, StreamStats
from config import Config

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(token=Config.TWITCH_IRC_TOKEN, prefix='?', initial_channels=[])
        self.app = create_app()
        self.channels_to_monitor = []

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
                self.channels_to_monitor = [c.name for c in channels]

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
                    new_msg = ChatMessage(
                        channel_id=channel.id,
                        username=username,
                        message=content,
                        timestamp=timestamp,
                        badges=badges,
                        meta={}
                    )
                    db.session.add(new_msg)
                    db.session.commit()
        except Exception as e:
            print(f"Error saving message: {e}")

    async def background_monitor(self):
        while True:
            # print("Starting stats collection cycle...")
            try:
                # Reload channels
                with self.app.app_context():
                     channels = Channel.query.filter_by(is_active=True).all()
                     current_channels = [c.name for c in channels]

                # Join new channels
                new_channels = list(set(current_channels) - set(self.channels_to_monitor))
                if new_channels:
                    print(f"Joining new channels: {new_channels}")
                    await self.join_channels(new_channels)
                    self.channels_to_monitor = current_channels

                if self.channels_to_monitor:
                    viewer_counts = {}
                    try:
                        streams = await self.fetch_streams(user_logins=self.channels_to_monitor)
                        for s in streams:
                             viewer_counts[s.user.name.lower()] = s.viewer_count
                    except Exception as e:
                        print(f"Error fetching streams: {e}")

                    # Run save_stats in thread
                    await asyncio.to_thread(self.save_stats, self.channels_to_monitor, viewer_counts)

            except Exception as e:
                print(f"Error in background monitor: {e}")

            await asyncio.sleep(300) # 5 minutes

    def save_stats(self, channel_names, viewer_counts):
        with self.app.app_context():
            for name in channel_names:
                try:
                    c = Channel.query.filter_by(name=name).first()
                    if not c:
                        continue

                    viewer_count = viewer_counts.get(name.lower(), 0)

                    # Get chatter count from TMI
                    chatter_count = 0
                    try:
                        tmi_url = f"https://tmi.twitch.tv/group/user/{name.lower()}/chatters"
                        resp = requests.get(tmi_url, timeout=5)
                        if resp.status_code == 200:
                            data = resp.json()
                            chatter_count = data.get('chatter_count', 0)
                    except Exception:
                        pass # Ignore TMI errors

                    stats = StreamStats(
                        channel_id=c.id,
                        viewer_count=viewer_count,
                        chatter_count=chatter_count
                    )
                    db.session.add(stats)
                except Exception as e:
                    print(f"Error processing stats for {name}: {e}")

            try:
                db.session.commit()
                # print("Stats saved.")
            except Exception as e:
                print(f"Error committing stats: {e}")
                db.session.rollback()

if __name__ == "__main__":
    if not Config.TWITCH_IRC_TOKEN:
        print("TWITCH_IRC_TOKEN not set in .env")
    else:
        bot = Bot()
        bot.run()
