import os
import time
from sqlalchemy import create_engine, text, inspect
from dotenv import load_dotenv

# Load env from .env file explicitly if needed, mostly for standalone script usage
load_dotenv()

def migrate():
    print("Starting migration script...")

    # Get Database URL
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL not set in environment.")
        return

    # Retry logic for connection
    max_retries = 5
    for i in range(max_retries):
        try:
            engine = create_engine(db_url)
            with engine.connect() as conn:
                print("Database connection successful.")

                # Check tables
                inspector = inspect(engine)

                # 1. Create streams table
                if not inspector.has_table("streams"):
                    print("Creating 'streams' table...")
                    if engine.dialect.name == 'postgresql':
                        conn.execute(text("""
                            CREATE TABLE streams (
                                id SERIAL PRIMARY KEY,
                                channel_id INTEGER NOT NULL REFERENCES channels(id),
                                title VARCHAR(255),
                                game_name VARCHAR(128),
                                started_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
                                ended_at TIMESTAMP WITHOUT TIME ZONE,
                                is_live BOOLEAN DEFAULT TRUE
                            );
                        """))
                    else:
                        conn.execute(text("""
                            CREATE TABLE streams (
                                id INTEGER PRIMARY KEY,
                                channel_id INTEGER NOT NULL REFERENCES channels(id),
                                title VARCHAR(255),
                                game_name VARCHAR(128),
                                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                ended_at TIMESTAMP,
                                is_live BOOLEAN DEFAULT TRUE
                            );
                        """))
                    print("'streams' table created.")

                # 2. Add columns
                tables_to_check = ["chat_messages", "stream_stats", "analysis_results"]
                for table in tables_to_check:
                    if inspector.has_table(table):
                        cols = [c['name'] for c in inspector.get_columns(table)]
                        if "stream_id" not in cols:
                            print(f"Adding 'stream_id' to '{table}'...")
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN stream_id INTEGER REFERENCES streams(id);"))

                # Add new columns for version 2
                if inspector.has_table("stream_stats"):
                    cols = [c['name'] for c in inspector.get_columns("stream_stats")]
                    if "active_chatter_count" not in cols:
                        print("Adding 'active_chatter_count' to 'stream_stats'...")
                        conn.execute(text("ALTER TABLE stream_stats ADD COLUMN active_chatter_count INTEGER DEFAULT 0;"))

                if inspector.has_table("viewers"):
                    cols = [c['name'] for c in inspector.get_columns("viewers")]
                    if "sub_tier" not in cols:
                        print("Adding 'sub_tier' to 'viewers'...")
                        conn.execute(text("ALTER TABLE viewers ADD COLUMN sub_tier VARCHAR(32);"))
                    if "follow_duration" not in cols:
                        print("Adding 'follow_duration' to 'viewers'...")
                        conn.execute(text("ALTER TABLE viewers ADD COLUMN follow_duration INTEGER;"))

                    # New columns for viewers
                    if "twitch_id" not in cols:
                        print("Adding 'twitch_id' to 'viewers'...")
                        conn.execute(text("ALTER TABLE viewers ADD COLUMN twitch_id VARCHAR(64);"))
                    if "account_created_at" not in cols:
                        print("Adding 'account_created_at' to 'viewers'...")
                        conn.execute(text("ALTER TABLE viewers ADD COLUMN account_created_at TIMESTAMP WITHOUT TIME ZONE;"))
                    if "suspicion_score" not in cols:
                        print("Adding 'suspicion_score' to 'viewers'...")
                        conn.execute(text("ALTER TABLE viewers ADD COLUMN suspicion_score INTEGER DEFAULT 0;"))
                    if "suspicion_reason" not in cols:
                        print("Adding 'suspicion_reason' to 'viewers'...")
                        conn.execute(text("ALTER TABLE viewers ADD COLUMN suspicion_reason VARCHAR(255);"))
                    if "nick_history" not in cols:
                        print("Adding 'nick_history' to 'viewers'...")
                        # Handle dialect for JSON
                        if engine.dialect.name == 'postgresql':
                            conn.execute(text("ALTER TABLE viewers ADD COLUMN nick_history JSONB DEFAULT '[]'::jsonb;"))
                        else:
                            conn.execute(text("ALTER TABLE viewers ADD COLUMN nick_history JSON DEFAULT '[]';"))

                if inspector.has_table("chat_messages"):
                    cols = [c['name'] for c in inspector.get_columns("chat_messages")]
                    if "meta" not in cols:
                        print("Adding 'meta' to 'chat_messages'...")
                        if engine.dialect.name == 'postgresql':
                            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN meta JSONB DEFAULT '{}'::jsonb;"))
                        else:
                            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN meta JSON DEFAULT '{}';"))

                # Create stream_viewer_stats table
                if not inspector.has_table("stream_viewer_stats"):
                    print("Creating 'stream_viewer_stats' table...")
                    # Common SQL for both mostly, but SERIAL vs AUTOINCREMENT
                    if engine.dialect.name == 'postgresql':
                        conn.execute(text("""
                            CREATE TABLE stream_viewer_stats (
                                id SERIAL PRIMARY KEY,
                                stream_id INTEGER NOT NULL REFERENCES streams(id),
                                viewer_id INTEGER NOT NULL REFERENCES viewers(id),
                                message_count INTEGER DEFAULT 0,
                                first_seen TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
                                last_seen TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() AT TIME ZONE 'utc'),
                                suspicion_score INTEGER DEFAULT 0,
                                is_suspicious BOOLEAN DEFAULT FALSE,
                                CONSTRAINT _stream_viewer_uc UNIQUE (stream_id, viewer_id)
                            );
                        """))
                    else:
                        # SQLite
                        conn.execute(text("""
                            CREATE TABLE stream_viewer_stats (
                                id INTEGER PRIMARY KEY,
                                stream_id INTEGER NOT NULL REFERENCES streams(id),
                                viewer_id INTEGER NOT NULL REFERENCES viewers(id),
                                message_count INTEGER DEFAULT 0,
                                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                suspicion_score INTEGER DEFAULT 0,
                                is_suspicious BOOLEAN DEFAULT FALSE,
                                UNIQUE(stream_id, viewer_id)
                            );
                        """))
                    print("'stream_viewer_stats' table created.")

                conn.commit()
                print("Migration complete.")
                break

        except Exception as e:
            print(f"Migration attempt {i+1} failed: {e}")
            if i < max_retries - 1:
                time.sleep(2)
            else:
                print("Migration failed after retries.")

if __name__ == "__main__":
    migrate()
