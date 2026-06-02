"""
Observatory Presence / Reservation lightweight system
Single-file Flask app + helper scripts
"""

from flask import Flask, jsonify, request, render_template, abort, send_from_directory
from datetime import datetime, timedelta, timezone
import threading
import os
import sys
import json
import hmac
import logging
from functools import wraps

log = logging.getLogger(__name__)

SECRET_TOKEN = os.getenv('SECRET_TOKEN', '')
ALLOW_OPEN_API = os.getenv('ALLOW_OPEN_API', '').strip().lower() in ('1', 'true', 'yes', 'on')
DATA_FILE = os.getenv('DATA_FILE', 'presence.json')
BASE_PATH = os.getenv('BASE_PATH', '')
CAMERA_MEDIA_DIR = os.getenv('CAMERA_MEDIA_DIR', '/var/lib/observatory_cameras')
CAMERA_MEDIA_FILES = frozenset({
    'outdoor_current.jpg',
    'indoor_current.jpg',
    'outdoor_video.webm',
    'yesterday_outdoor_video.webm',
})

try:
    HEARTBEAT_TIMEOUT = int(os.getenv('HEARTBEAT_TIMEOUT', '90'))
except ValueError:
    HEARTBEAT_TIMEOUT = 90

try:
    HOST_STATUS_TTL_SEC = int(os.getenv('HOST_STATUS_TTL_SEC', '3600'))
except ValueError:
    HOST_STATUS_TTL_SEC = 3600

try:
    TELESCOPE_STATUS_TTL_SEC = int(os.getenv('TELESCOPE_STATUS_TTL_SEC', '2400'))
except ValueError:
    TELESCOPE_STATUS_TTL_SEC = 2400

if not SECRET_TOKEN and not ALLOW_OPEN_API:
    sys.exit(
        'SECRET_TOKEN is required in production. '
        'Set ALLOW_OPEN_API=1 only for local development.'
    )

_static_url_path = (BASE_PATH + '/static') if BASE_PATH else '/static'
app = Flask(__name__, static_url_path=_static_url_path, static_folder='static', template_folder='templates')

state_lock = threading.Lock()
data_dir = os.path.dirname(os.path.abspath(DATA_FILE))
if data_dir and not os.path.exists(data_dir):
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        pass
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        state = {}
else:
    state = {}


def utc_now():
    return datetime.now(timezone.utc)


def now_iso():
    return utc_now().replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def parse_iso_utc(value):
    if not value:
        return None
    try:
        s = str(value).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def save_state():
    tmp_path = DATA_FILE + '.tmp'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, default=str)
        os.replace(tmp_path, DATA_FILE)
    except Exception:
        log.exception('Failed to save state to %s', DATA_FILE)
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def as_str(value, default=''):
    try:
        return str(value) if value is not None else default
    except Exception:
        return default


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


def parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    try:
        s = str(value).strip().lower()
    except Exception:
        return default
    return s in ('1', 'true', 'yes', 'y', 'on')


def clear_session_fields():
    for key in (
        'occupied',
        'user',
        'target',
        'start',
        'last_heartbeat',
        'planned_hours',
        'planned_minutes',
        'planned_end',
    ):
        state.pop(key, None)


def end_session():
    clear_session_fields()
    state['occupied'] = False


def prune_stale_monitoring(now=None):
    now = now or utc_now()
    removed = False
    for bucket, ttl in (('hosts', HOST_STATUS_TTL_SEC), ('telescope', TELESCOPE_STATUS_TTL_SEC)):
        if ttl <= 0:
            continue
        entries = state.get(bucket)
        if not isinstance(entries, dict):
            continue
        stale = []
        for key, record in entries.items():
            ts = parse_iso_utc(record.get('ts') if isinstance(record, dict) else None)
            if ts is None or (now - ts).total_seconds() > ttl:
                stale.append(key)
        for key in stale:
            entries.pop(key, None)
            removed = True
    return removed


def require_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if ALLOW_OPEN_API and not SECRET_TOKEN:
            return fn(*args, **kwargs)
        auth_header = request.headers.get('Authorization', '')
        token_value = ''
        if auth_header.startswith('Bearer '):
            token_value = auth_header.split(' ', 1)[1]
        else:
            json_data = request.get_json(silent=True) or {}
            token_value = json_data.get('token') or request.values.get('token', '')
        if not token_value or not SECRET_TOKEN:
            abort(401)
        if not hmac.compare_digest(token_value, SECRET_TOKEN):
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
    with state_lock:
        if 'occupied' not in state:
            state['occupied'] = False
            save_state()
        if not _cleaner_started:
            _start_cleaner_once()


def camera_media_url_prefix():
    prefix = (BASE_PATH or '').rstrip('/')
    return f'{prefix}/media/cameras'


@app.route('/health')
def health():
    return jsonify({'ok': True})


@app.route('/media/cameras/<path:filename>')
def camera_media(filename):
    if filename not in CAMERA_MEDIA_FILES:
        abort(404)
    if not CAMERA_MEDIA_DIR or not os.path.isdir(CAMERA_MEDIA_DIR):
        abort(404)
    return send_from_directory(CAMERA_MEDIA_DIR, filename)


@app.route('/')
def index():
    return render_template(
        'index.html',
        base_path=BASE_PATH or '',
        media_base=camera_media_url_prefix(),
    )


@app.route('/status')
def status():
    with state_lock:
        return jsonify(state)


@app.route('/telescope_status', methods=['POST'])
@require_token
def telescope_status():
    json_data = request.get_json(silent=True) or {}
    host_id = as_str(json_data.get('hostId'), request.remote_addr or '').strip()
    if not host_id:
        return jsonify({'ok': False, 'msg': 'hostId required'}), 400
    now_ts = now_iso()
    try:
        ra_hours = float(json_data.get('raHours'))
        dec_deg = float(json_data.get('decDeg'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'raHours/decDeg must be numeric'}), 400
    frame = as_str(json_data.get('frame'), 'JNow').strip()
    tracking_v = json_data.get('tracking')
    slewing_v = json_data.get('slewing')
    at_park_v = json_data.get('atPark')
    pulse_v = json_data.get('isPulseGuiding')
    tracking = parse_bool(tracking_v, default=False) if tracking_v is not None else None
    slewing = parse_bool(slewing_v, default=False) if slewing_v is not None else None
    at_park = parse_bool(at_park_v, default=False) if at_park_v is not None else None
    is_pulse_guiding = parse_bool(pulse_v, default=False) if pulse_v is not None else None
    ts = json_data.get('ts') or now_ts
    try:
        alt_deg = float(json_data.get('altDeg'))
    except (TypeError, ValueError):
        alt_deg = None
    try:
        az_deg = float(json_data.get('azDeg'))
    except (TypeError, ValueError):
        az_deg = None
    side_of_pier = as_str(json_data.get('sideOfPier'), '').strip() or None
    utc_str = as_str(json_data.get('utc'), '').strip() or None
    try:
        lst_hours = float(json_data.get('lst'))
    except (TypeError, ValueError):
        lst_hours = None

    ra_j2000 = ra_hours
    dec_j2000 = dec_deg
    used_frame = 'J2000'
    if frame.upper() != 'J2000':
        try:
            from astropy.coordinates import SkyCoord, FK5
            from astropy.time import Time
            import astropy.units as u
            obstime = Time.now()
            c = SkyCoord(ra=ra_hours * u.hourangle, dec=dec_deg * u.deg, frame='fk5', equinox=obstime)
            c2000 = c.transform_to(FK5(equinox=Time('J2000')))
            ra_j2000 = c2000.ra.to(u.hourangle).value
            dec_j2000 = c2000.dec.deg
        except Exception:
            used_frame = frame

    record = {
        'hostId': host_id,
        'ts': ts,
        'raHours': ra_j2000,
        'decDeg': dec_j2000,
        'frame': used_frame,
        'tracking': tracking,
        'slewing': slewing,
        'atPark': at_park,
        'isPulseGuiding': is_pulse_guiding,
        'altDeg': alt_deg,
        'azDeg': az_deg,
        'sideOfPier': side_of_pier,
        'utc': utc_str,
        'lst': lst_hours,
    }
    with state_lock:
        if not isinstance(state.get('telescope'), dict):
            state['telescope'] = {}
        state['telescope'][host_id] = record
        save_state()
    return jsonify({'ok': True})


@app.route('/host_status', methods=['POST'])
@require_token
def host_status():
    if not request.is_json:
        return jsonify({'ok': False, 'msg': 'Expected JSON body'}), 400
    payload = request.get_json(silent=True) or {}
    host_id = as_str(payload.get('hostId'), '').strip() or (request.remote_addr or '')
    now_ts = now_iso()
    record = {
        'hostId': host_id,
        'ts': payload.get('ts') or now_ts,
        'uptimeSec': parse_int(payload.get('uptimeSec'), default=0, min_value=0),
        'cpuPercent': parse_float(payload.get('cpuPercent'), default=0.0, min_value=0.0, max_value=100.0),
        'memPercent': parse_float(payload.get('memPercent'), default=0.0, min_value=0.0, max_value=100.0),
        'diskCPercent': parse_float(payload.get('diskCPercent'), default=0.0, min_value=0.0, max_value=100.0),
        'osVersion': payload.get('osVersion') or '',
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
    json_data = request.get_json(silent=True) or {}
    user = request.values.get('user') or json_data.get('user') or request.remote_addr
    target = request.values.get('target') or json_data.get('target') or ''
    force_raw = request.values.get('force') or json_data.get('force')
    force = parse_bool(force_raw, default=False)
    planned_minutes_raw = request.values.get('planned_minutes') or json_data.get('planned_minutes')
    planned_hours_raw = request.values.get('planned_hours') or json_data.get('planned_hours')
    planned_end_iso = request.values.get('planned_end') or json_data.get('planned_end')
    planned_minutes = parse_int(planned_minutes_raw, default=0, min_value=0)
    planned_hours = parse_float(planned_hours_raw, default=0.0, min_value=0.0)
    with state_lock:
        if state.get('occupied') and not force:
            return jsonify({'ok': False, 'msg': 'Already occupied', 'state': state}), 409
        if force and state.get('occupied'):
            clear_session_fields()
        state['occupied'] = True
        state['user'] = user
        state['target'] = target
        start_iso = now_iso()
        state['start'] = start_iso
        state['last_heartbeat'] = start_iso
        planned_end = None
        if planned_end_iso:
            planned_end = planned_end_iso
        else:
            total_minutes = 0
            if planned_hours and planned_hours > 0:
                total_minutes = int(round(planned_hours * 60))
                state['planned_hours'] = planned_hours
            elif planned_minutes and planned_minutes > 0:
                total_minutes = planned_minutes
                state['planned_minutes'] = planned_minutes
            if total_minutes > 0:
                base = parse_iso_utc(start_iso) or utc_now()
                end_dt = base + timedelta(minutes=total_minutes)
                planned_end = end_dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')
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
            return jsonify({'ok': False, 'msg': 'No active session'}), 404
        if user and user != state.get('user'):
            return jsonify({'ok': False, 'msg': 'Unauthorized user'}), 403
        state['last_heartbeat'] = now_iso()
        save_state()
    return jsonify({'ok': True})


@app.route('/release', methods=['POST'])
@require_token
def release():
    with state_lock:
        end_session()
        save_state()
    return jsonify({'ok': True})


def cleaner_loop():
    import time
    prune_counter = 0
    while True:
        with state_lock:
            now = utc_now()
            if state.get('occupied') and state.get('last_heartbeat'):
                last = parse_iso_utc(state['last_heartbeat'])
                if last is None:
                    last = now
                if (now - last).total_seconds() > HEARTBEAT_TIMEOUT:
                    log.info(
                        'Releasing stale session for %s (last heartbeat %s, timeout %ss)',
                        state.get('user'),
                        state.get('last_heartbeat'),
                        HEARTBEAT_TIMEOUT,
                    )
                    end_session()
                    save_state()
            prune_counter += 1
            if prune_counter >= 6:
                prune_counter = 0
                if prune_stale_monitoring(now):
                    save_state()
        time.sleep(10)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    with state_lock:
        if 'occupied' not in state:
            state['occupied'] = False
            save_state()
        if not _cleaner_started:
            _start_cleaner_once()
    app.run(host='0.0.0.0', port=5000)
