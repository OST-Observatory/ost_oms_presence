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

from flask import Flask, jsonify, request, render_template_string, render_template, abort
from datetime import datetime, timedelta
import threading
import os
import json
from functools import wraps

SECRET_TOKEN = os.getenv('SECRET_TOKEN', '')
DATA_FILE = os.getenv('DATA_FILE', 'presence.json')
BASE_PATH = os.getenv('BASE_PATH', '')
# Configure static path to respect reverse-proxy mount (e.g., /ost_status)
_static_url_path = (BASE_PATH + '/static') if BASE_PATH else '/static'
app = Flask(__name__, static_url_path=_static_url_path, static_folder='static', template_folder='templates')
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

def parse_int(value, default=0, min_value=None, max_value=None):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if min_value is not None and v < min_value:
        v = min_value
    if max_value is not None and v > max_value:
        v = max_value
    return v

def parse_float(value, default=0.0, min_value=None, max_value=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if min_value is not None and v < min_value:
        v = min_value
    if max_value is not None and v > max_value:
        v = max_value
    return v
def require_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Dev mode: when SECRET_TOKEN is empty, do not require auth
        if not SECRET_TOKEN:
            return fn(*args, **kwargs)
        # Prefer Authorization header Bearer token; allow 'token' in form/json as fallback
        auth_header = request.headers.get('Authorization', '')
        token_value = ''
        if auth_header.startswith('Bearer '):
            token_value = auth_header.split(' ', 1)[1]
        else:
            json_data = request.get_json(silent=True) or {}
            token_value = json_data.get('token') or request.values.get('token', '')
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

@app.before_request
def _ensure_init():
    # Ensure base structure and start background cleaner once (Flask 3.x safe)
    with state_lock:
        if 'occupied' not in state:
            state['occupied'] = False
            save_state()
        if not _cleaner_started:
            _start_cleaner_once()

@app.route('/')
def index():
    with state_lock:
        s = state.copy()
    occupied = s.get('occupied', False)
    return render_template('index.html',
                           occupied=occupied,
                           user=s.get('user',''),
                           start=s.get('start',''),
                           target=s.get('target',''),
                           hb=s.get('last_heartbeat',''),
                           hosts=s.get('hosts') or {},
                           base_path=BASE_PATH or '')

@app.route('/status')
def status():
    with state_lock:
        return jsonify(state)

@app.route('/host_status', methods=['POST'])
@require_token
def host_status():
    if not request.is_json:
        return jsonify({'ok': False, 'msg': 'Expected JSON body'}), 400
    payload = request.get_json(silent=True) or {}
    host_id = (payload.get('hostId') or '').strip() or request.remote_addr
    now_ts = now_iso()
    record = {
        'hostId': host_id,
        'ts': payload.get('ts') or now_ts,
        'uptimeSec': parse_int(payload.get('uptimeSec'), default=0, min_value=0),
        'cpuPercent': parse_float(payload.get('cpuPercent'), default=0.0, min_value=0.0, max_value=100.0),
        'memPercent': parse_float(payload.get('memPercent'), default=0.0, min_value=0.0, max_value=100.0),
        'diskCPercent': parse_float(payload.get('diskCPercent'), default=0.0, min_value=0.0, max_value=100.0),
        'osVersion': payload.get('osVersion') or ''
    }
    with state_lock:
        if not isinstance(state.get('hosts'), dict):
            state['hosts'] = {}
        state['hosts'][host_id] = record
        save_state()
    return jsonify({'ok': True})
@app.route('/start', methods=['POST'])
@require_token
def start():
    # can be called by autostart client or manual form
    json_data = request.get_json(silent=True) or {}
    user = request.values.get('user') or json_data.get('user') or request.remote_addr
    target = request.values.get('target') or json_data.get('target') or ''
    # planned duration/end (multiple inputs supported)
    planned_minutes_raw = request.values.get('planned_minutes') or json_data.get('planned_minutes')
    planned_hours_raw = request.values.get('planned_hours') or json_data.get('planned_hours')
    planned_end_iso = request.values.get('planned_end') or json_data.get('planned_end')
    planned_minutes = parse_int(planned_minutes_raw, default=0, min_value=0)
    planned_hours = parse_float(planned_hours_raw, default=0.0, min_value=0.0)
    with state_lock:
        if state.get('occupied'):
            return jsonify({'ok': False, 'msg': 'Bereits belegt', 'state': state}), 409
        state['occupied'] = True
        state['user'] = user
        state['target'] = target
        start_iso = now_iso()
        state['start'] = start_iso
        state['last_heartbeat'] = start_iso
        # compute/store planned_end if provided
        planned_end = None
        if planned_end_iso:
            # accept as-is (should be ISO UTC)
            planned_end = planned_end_iso
        else:
            # derive from hours/minutes
            total_minutes = 0
            if planned_hours and planned_hours > 0:
                total_minutes = int(round(planned_hours * 60))
                state['planned_hours'] = planned_hours
            elif planned_minutes and planned_minutes > 0:
                total_minutes = planned_minutes
                state['planned_minutes'] = planned_minutes
            if total_minutes > 0:
                try:
                    base = datetime.fromisoformat(start_iso.replace('Z',''))
                except Exception:
                    base = datetime.utcnow()
                end_dt = base + timedelta(minutes=total_minutes)
                planned_end = end_dt.isoformat() + 'Z'
        if planned_end:
            state['planned_end'] = planned_end
        save_state()
    return jsonify({'ok': True, 'state': state})

@app.route('/heartbeat', methods=['POST'])
@require_token
def heartbeat():
    json_data = request.get_json(silent=True) or {}
    user = json_data.get('user') or request.values.get('user')
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
        if not _cleaner_started:
            _start_cleaner_once()
    # production: use gunicorn or systemd + WSGI; for testing this is fine
    app.run(host='0.0.0.0', port=5000)

