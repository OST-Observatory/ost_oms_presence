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

Files in this repo:
- observatory_presence.py            # Flask server + minimal UI
- autostart_client_prompt.ps1       # Windows PowerShell autostart client
- README.md
- LICENSE
- todo.txt
"""

from flask import Flask, jsonify, request, render_template_string
from datetime import datetime, timedelta
import threading
import os
import json

app = Flask(__name__)
DATA_FILE = 'presence.json'
HEARTBEAT_TIMEOUT = 90  # seconds: if no heartbeat within this, release

# minimal in-memory cache backed by file
state_lock = threading.Lock()
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
      <form action="/release" method="post">
        <input type="hidden" name="token" value="RELEASE_TOKEN">
        <button type="submit">Freigeben (manuell)</button>
      </form>
    {% else %}
      <p>Frei — niemand beobachtet gerade.</p>
    {% endif %}
    <hr>
    <h3>Manuell verbinden</h3>
    <form action="/start" method="post">
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
                                  hb=s.get('last_heartbeat',''))

@app.route('/status')
def status():
    with state_lock:
        return jsonify(state)

@app.route('/start', methods=['POST'])
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
    t = threading.Thread(target=cleaner_loop, daemon=True)
    t.start()
    # production: use gunicorn or systemd + WSGI; for testing this is fine
    app.run(host='0.0.0.0', port=5000)

