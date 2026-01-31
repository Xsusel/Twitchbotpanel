from flask import Flask
from app.models import db, Channel, Stream, ChatMessage, StreamViewerStats, Viewer, AnalysisResult, StreamStats
from sqlalchemy import event
from sqlalchemy.engine import Engine

# Enable Foreign Keys for SQLite
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

def reproduce():
    with app.app_context():
        try:
            db.create_all()

            # Create Channel
            channel = Channel(name='test_channel')
            db.session.add(channel)
            db.session.commit()

            # Create Stream
            stream = Stream(channel_id=channel.id, title='test stream')
            db.session.add(stream)
            db.session.commit()

            # Create Viewer
            viewer = Viewer(channel_id=channel.id, username='test_viewer')
            db.session.add(viewer)
            db.session.commit()

            # Create StreamViewerStats
            svs = StreamViewerStats(stream_id=stream.id, viewer_id=viewer.id)
            db.session.add(svs)

            # Create ChatMessage
            msg = ChatMessage(channel_id=channel.id, stream_id=stream.id, username='test_viewer', message='hello')
            db.session.add(msg)

            # Create AnalysisResult
            analysis = AnalysisResult(channel_id=channel.id, stream_id=stream.id, bot_score=0.5)
            db.session.add(analysis)

            db.session.commit()

            print("Data created. Attempting to delete CHANNEL...")

            db.session.delete(channel)
            db.session.commit()
            print("SUCCESS: Channel deleted.")

        except Exception as e:
            print(f"FAILURE: Channel deletion failed: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    reproduce()
