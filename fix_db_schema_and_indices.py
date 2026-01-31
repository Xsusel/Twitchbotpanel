import os
import time
from sqlalchemy import create_engine, text, inspect
from dotenv import load_dotenv

load_dotenv()

def fix_schema():
    print("Starting schema fix script...")

    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL not set in environment.")
        return

    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            print("Database connection successful.")

            # Fix Type Mismatch for analysis_results.stream_id
            print("Checking analysis_results.stream_id type...")
            try:
                # We try to alter it. If it's already integer, Postgres might complain or just do nothing depending on syntax,
                # but 'TYPE INTEGER USING stream_id::integer' works if it is currently varchar.
                conn.execute(text("ALTER TABLE analysis_results ALTER COLUMN stream_id TYPE INTEGER USING stream_id::integer;"))
                print("Converted analysis_results.stream_id to INTEGER.")
            except Exception as e:
                print(f"Could not alter analysis_results.stream_id (might already be int or data issue): {e}")

            # Fix Type Mismatch for chat_messages.stream_id (just in case)
            print("Checking chat_messages.stream_id type...")
            try:
                conn.execute(text("ALTER TABLE chat_messages ALTER COLUMN stream_id TYPE INTEGER USING stream_id::integer;"))
                print("Converted chat_messages.stream_id to INTEGER.")
            except Exception as e:
                print(f"Skipping chat_messages.stream_id: {e}")

            # Fix Type Mismatch for stream_stats.stream_id (just in case)
            print("Checking stream_stats.stream_id type...")
            try:
                conn.execute(text("ALTER TABLE stream_stats ALTER COLUMN stream_id TYPE INTEGER USING stream_id::integer;"))
                print("Converted stream_stats.stream_id to INTEGER.")
            except Exception as e:
                print(f"Skipping stream_stats.stream_id: {e}")

            # Add Indexes
            indexes_to_create = [
                ("idx_chat_messages_stream_id", "chat_messages", "stream_id"),
                ("idx_stream_stats_stream_id", "stream_stats", "stream_id"),
                ("idx_analysis_results_stream_id", "analysis_results", "stream_id"),
                ("idx_stream_viewer_stats_stream_id", "stream_viewer_stats", "stream_id"),
                ("idx_chat_messages_channel_id", "chat_messages", "channel_id"),
                ("idx_stream_stats_channel_id", "stream_stats", "channel_id"),
                ("idx_viewers_channel_id", "viewers", "channel_id")
            ]

            for idx_name, table, col in indexes_to_create:
                try:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({col});"))
                    print(f"Index {idx_name} created (or existed).")
                except Exception as e:
                    print(f"Error creating index {idx_name}: {e}")

            conn.commit()
            print("Schema fix complete.")

    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    fix_schema()
