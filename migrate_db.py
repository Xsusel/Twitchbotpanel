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

                # 2. Add columns
                tables_to_check = ["chat_messages", "stream_stats", "analysis_results"]
                for table in tables_to_check:
                    if inspector.has_table(table):
                        cols = [c['name'] for c in inspector.get_columns(table)]
                        if "stream_id" not in cols:
                            print(f"Adding 'stream_id' to '{table}'...")
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN stream_id INTEGER REFERENCES streams(id);"))

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
