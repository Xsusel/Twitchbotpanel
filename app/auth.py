from flask import Blueprint, render_template, redirect, url_for, request, session, flash
from functools import wraps
from config import Config

auth = Blueprint('auth', __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == Config.ADMIN_PASSWORD:
            session['logged_in'] = True
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.index'))
        else:
            flash('Invalid password')
    return render_template('login.html')

@auth.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('auth.login'))
