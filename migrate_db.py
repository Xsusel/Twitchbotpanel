import os
from sqlalchemy import create_engine, text, inspect
from config import Config

def migrate():
    print("Starting migration...")

    # Get Database URL
    db_url = Config.SQLALCHEMY_DATABASE_URI
    if not db_url:
        print("Error: DATABASE_URL not set.")
        return

    engine = create_engine(db_url)
    inspector = inspect(engine)

    with engine.connect() as conn:
        # 1. Create streams table if it doesn't exist
        if not inspector.has_table("streams"):
            print("Creating 'streams' table...")
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
            print("'streams' table created.")
        else:
            print("'streams' table already exists.")

        # 2. Add stream_id to chat_messages
        columns = [col['name'] for col in inspector.get_columns("chat_messages")]
        if "stream_id" not in columns:
            print("Adding 'stream_id' to 'chat_messages'...")
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN stream_id INTEGER REFERENCES streams(id);"))
            print("Added.")
        else:
            print("'stream_id' exists in 'chat_messages'.")

        # 3. Add stream_id to stream_stats
        columns = [col['name'] for col in inspector.get_columns("stream_stats")]
        if "stream_id" not in columns:
            print("Adding 'stream_id' to 'stream_stats'...")
            conn.execute(text("ALTER TABLE stream_stats ADD COLUMN stream_id INTEGER REFERENCES streams(id);"))
            print("Added.")
        else:
            print("'stream_id' exists in 'stream_stats'.")

        # 4. Add stream_id to analysis_results
        columns = [col['name'] for col in inspector.get_columns("analysis_results")]
        if "stream_id" not in columns:
            print("Adding 'stream_id' to 'analysis_results'...")
            conn.execute(text("ALTER TABLE analysis_results ADD COLUMN stream_id INTEGER REFERENCES streams(id);"))
            print("Added.")
        else:
            print("'stream_id' exists in 'analysis_results'.")

        conn.commit()

    print("Migration complete.")

if __name__ == "__main__":
    migrate()
