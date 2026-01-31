from flask import Flask
from config import Config
from app.models import db
from celery import Celery, Task
from flask_socketio import SocketIO
from sqlalchemy import text, inspect

def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app

celery = Celery(__name__)
socketio = SocketIO()

def check_and_migrate_db(app):
    """Checks for missing tables/columns and updates the DB."""
    with app.app_context():
        try:
            # 1. Create tables if not exist (via SQLAlchemy)
            # This handles 'streams' if it's completely missing
            db.create_all()

            # 2. Check for missing columns (Migrations)
            inspector = inspect(db.engine)
            with db.engine.connect() as conn:
                # Add stream_id to chat_messages
                if "chat_messages" in inspector.get_table_names():
                    cols = [c['name'] for c in inspector.get_columns("chat_messages")]
                    if "stream_id" not in cols:
                        print("Migrating: Adding stream_id to chat_messages")
                        conn.execute(text("ALTER TABLE chat_messages ADD COLUMN stream_id INTEGER REFERENCES streams(id)"))
                        conn.commit()

                # Add stream_id to stream_stats
                if "stream_stats" in inspector.get_table_names():
                    cols = [c['name'] for c in inspector.get_columns("stream_stats")]
                    if "stream_id" not in cols:
                        print("Migrating: Adding stream_id to stream_stats")
                        conn.execute(text("ALTER TABLE stream_stats ADD COLUMN stream_id INTEGER REFERENCES streams(id)"))
                    if "active_chatter_count" not in cols:
                        print("Migrating: Adding active_chatter_count to stream_stats")
                        conn.execute(text("ALTER TABLE stream_stats ADD COLUMN active_chatter_count INTEGER DEFAULT 0"))
                    conn.commit()

                # Add fields to viewers
                if "viewers" in inspector.get_table_names():
                    cols = [c['name'] for c in inspector.get_columns("viewers")]
                    if "sub_tier" not in cols:
                        print("Migrating: Adding sub_tier to viewers")
                        conn.execute(text("ALTER TABLE viewers ADD COLUMN sub_tier VARCHAR(32)"))
                    if "follow_duration" not in cols:
                        print("Migrating: Adding follow_duration to viewers")
                        conn.execute(text("ALTER TABLE viewers ADD COLUMN follow_duration INTEGER"))
                    conn.commit()

                # Add stream_id to analysis_results
                if "analysis_results" in inspector.get_table_names():
                    cols = [c['name'] for c in inspector.get_columns("analysis_results")]
                    if "stream_id" not in cols:
                        print("Migrating: Adding stream_id to analysis_results")
                        conn.execute(text("ALTER TABLE analysis_results ADD COLUMN stream_id INTEGER REFERENCES streams(id)"))
                        conn.commit()

            print("Database check/migration complete.")
        except Exception as e:
            print(f"Database migration error: {e}")

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)

    # Auto-migrate on startup
    check_and_migrate_db(app)

    app.config.from_mapping(
        CELERY=dict(
            broker_url=app.config['CELERY_BROKER_URL'],
            result_backend=app.config['CELERY_RESULT_BACKEND'],
            task_ignore_result=True,
        ),
    )

    global celery
    celery = celery_init_app(app)

    # Initialize SocketIO
    socketio.init_app(app, message_queue=app.config['CELERY_BROKER_URL'], async_mode='eventlet')

    from app.routes import main
    app.register_blueprint(main)

    from app.auth import auth
    app.register_blueprint(auth, url_prefix='/auth')

    return app
