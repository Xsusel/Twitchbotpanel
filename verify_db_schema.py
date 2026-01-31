import os
from sqlalchemy import create_engine, inspect, text
from dotenv import load_dotenv

load_dotenv()

def verify():
    print("Verifying database schema...")
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL not set.")
        return

    try:
        engine = create_engine(db_url)
        inspector = inspect(engine)

        # Check analysis_results.stream_id type
        columns = inspector.get_columns('analysis_results')
        stream_id_col = next((c for c in columns if c['name'] == 'stream_id'), None)

        if stream_id_col:
            print(f"analysis_results.stream_id type: {stream_id_col['type']}")
            # distinct types might appear as INTEGER or INTEGER()
            if "INT" in str(stream_id_col['type']).upper():
                print("SUCCESS: analysis_results.stream_id is Integer.")
            else:
                print("FAILURE: analysis_results.stream_id is NOT Integer.")
        else:
            print("FAILURE: stream_id column not found in analysis_results.")

        # Check Indexes
        indexes = inspector.get_indexes('chat_messages')
        index_names = [i['name'] for i in indexes]
        if 'idx_chat_messages_stream_id' in index_names:
             print("SUCCESS: idx_chat_messages_stream_id exists.")
        else:
             print("WARNING: idx_chat_messages_stream_id missing.")

    except Exception as e:
        print(f"Verification failed: {e}")

if __name__ == "__main__":
    verify()
