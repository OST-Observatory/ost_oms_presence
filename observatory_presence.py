"""
Observatory Presence / Reservation lightweight system
Single-file Flask app + helper scripts

Features:
- Runs on the observatory PC as a local web service (http://localhost:5000 or on-network)
- Supports auto-start via OS login (Task Scheduler/systemd) with a small client script
- Heartbeat mechanism: client pings the server periodically so the slot auto-releases on disconnect/inactivity
- Simple web UI to see who is observing, target, start time, optional notes

How it works (high level):
- The observatory PC runs this Flask app as a service.
- At remote login, an autostart script calls the /start API with username and optional target.
- The client keeps sending /heartbeat every N seconds. If heartbeats stop, the server marks the slot free.
- Anyone on the local network can open the web UI and see current status (or bind to localhost only).
"""

from flask import Flask, jsonify, request, render_template_string, abort
from datetime import datetime, timedelta
import threading
import os
import json
from functools import wraps

app = Flask(__name__)
SECRET_TOKEN = os.getenv('SECRET_TOKEN', '')
DATA_FILE = os.getenv('DATA_FILE', 'presence.json')
BASE_PATH = os.getenv('BASE_PATH', '')
try:
    HEARTBEAT_TIMEOUT = int(os.getenv('HEARTBEAT_TIMEOUT', '90'))  # seconds
except ValueError:
    HEARTBEAT_TIMEOUT = 90  # fallback

# minimal in-memory cache backed by file
state_lock = threading.Lock()
# ensure parent directory exists if DATA_FILE is an absolute path
data_dir = os.path.dirname(DATA_FILE)
if data_dir and not os.path.exists(data_dir):
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        pass
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, 'r') as f:
            state = json.load(f)
    except Exception:
        state = {}
else:
    state = {}

# helper functions
def save_state():
    with open(DATA_FILE, 'w') as f:
        json.dump(state, f, default=str)

def now_iso():
    return datetime.utcnow().isoformat() + 'Z'

def require_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Prefer Authorization header Bearer token; allow 'token' in form/json as fallback
        auth_header = request.headers.get('Authorization', '')
        token_value = ''
        if auth_header.startswith('Bearer '):
            token_value = auth_header.split(' ', 1)[1]
        elif request.is_json and request.json:
            token_value = request.json.get('token', '')
        else:
            token_value = request.values.get('token', '')
        if not SECRET_TOKEN or token_value != SECRET_TOKEN:
            abort(401)
        return fn(*args, **kwargs)
    return wrapper

_cleaner_started = False
def _start_cleaner_once():
    global _cleaner_started
    if not _cleaner_started:
        t = threading.Thread(target=cleaner_loop, daemon=True)
        t.start()
        _cleaner_started = True

@app.before_first_request
def _init_app():
    # Ensure base structure and start background cleaner when running under WSGI/Gunicorn
    with state_lock:
        if 'occupied' not in state:
            state['occupied'] = False
            save_state()
    _start_cleaner_once()

@app.route('/')
def index():
    with state_lock:
        s = state.copy()
    # simple one-file template
    template = '''
    <!doctype html>
    <html>
    <head><meta charset="utf-8"><title>Observatory Presence</title></head>
    <body>
    <h1>Observatory Status</h1>
    {% if occupied %}
      <p><strong>Aktuell beobachtet von:</strong> {{user}}</p>
      <p><strong>Start:</strong> {{start}}</p>
      <p><strong>Ziel / Notiz:</strong> {{target}}</p>
      <p><em>Letzter Herzschlag:</em> {{hb}}</p>
      <form action="{{ base_path }}/release" method="post">
        <input type="hidden" name="token" value="RELEASE_TOKEN">
        <button type="submit">Freigeben (manuell)</button>
      </form>
    {% else %}
      <p>Frei — niemand beobachtet gerade.</p>
    {% endif %}
    <hr>
    <h3>Manuell verbinden</h3>
    <form action="{{ base_path }}/start" method="post">
      Benutzer: <input name="user"><br>
      Ziel / Notiz: <input name="target"><br>
      <button type="submit">Start</button>
    </form>
    <p>Kleiner Hinweis: die Oberfläche ist minimal — für Produktion empfiehlt sich HTTPS & Auth.</p>
    </body>
    </html>
    '''
    occupied = s.get('occupied', False)
    return render_template_string(template,
                                  occupied=occupied,
                                  user=s.get('user',''),
                                  start=s.get('start',''),
                                  target=s.get('target',''),
                                  hb=s.get('last_heartbeat',''),
                                  base_path=BASE_PATH or '')

@app.route('/status')
def status():
    with state_lock:
        return jsonify(state)

@app.route('/start', methods=['POST'])
@require_token
def start():
    # can be called by autostart client or manual form
    user = request.values.get('user') or request.json and request.json.get('user') or request.remote_addr
    target = request.values.get('target') or (request.json and request.json.get('target')) or ''
    with state_lock:
        if state.get('occupied'):
            return jsonify({'ok': False, 'msg': 'Bereits belegt', 'state': state}), 409
        state['occupied'] = True
        state['user'] = user
        state['target'] = target
        state['start'] = now_iso()
        state['last_heartbeat'] = now_iso()
        save_state()
    return jsonify({'ok': True, 'state': state})

@app.route('/heartbeat', methods=['POST'])
@require_token
def heartbeat():
    user = request.json.get('user') if request.json else request.values.get('user')
    with state_lock:
        if not state.get('occupied'):
            return jsonify({'ok': False, 'msg': 'Kein aktiver Nutzer'}), 404
        if user and user != state.get('user'):
            return jsonify({'ok': False, 'msg': 'Nicht autorisierter Benutzer'}), 403
        state['last_heartbeat'] = now_iso()
        save_state()
    return jsonify({'ok': True})

@app.route('/release', methods=['POST'])
@require_token
def release():
    # allow form or JSON; in production protect this endpoint
    with state_lock:
        state.clear()
        state['occupied'] = False
        save_state()
    return jsonify({'ok': True})

# background cleaner to auto-release stale sessions
def cleaner_loop():
    import time
    while True:
        with state_lock:
            if state.get('occupied') and state.get('last_heartbeat'):
                try:
                    last = datetime.fromisoformat(state['last_heartbeat'].replace('Z',''))
                except Exception:
                    last = datetime.utcnow()
                if datetime.utcnow() - last > timedelta(seconds=HEARTBEAT_TIMEOUT):
                    print('Releasing stale session for', state.get('user'))
                    state.clear()
                    state['occupied'] = False
                    save_state()
        time.sleep(10)

if __name__ == '__main__':
    # ensure base structure
    with state_lock:
        if 'occupied' not in state:
            state['occupied'] = False
            save_state()
    _start_cleaner_once()
    # production: use gunicorn or systemd + WSGI; for testing this is fine
    app.run(host='0.0.0.0', port=5000)

