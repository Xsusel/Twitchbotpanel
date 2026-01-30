from app import create_app, celery
import app.tasks

app = create_app()
app.app_context().push()
