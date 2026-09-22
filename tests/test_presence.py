import json

import observatory_presence as op
from conftest import csrf_from, login


def auth_headers():
    return {'Authorization': 'Bearer test-secret'}


def test_start_heartbeat_release(client):
    r = client.post('/start', data={'user': 'alice', 'target': 'm42'}, headers=auth_headers())
    assert r.status_code == 200
    r = client.post('/heartbeat', json={'user': 'alice'}, headers=auth_headers())
    assert r.status_code == 200
    r = client.get('/status')
    data = r.get_json()
    assert data['occupied'] is True
    assert data['user'] == 'alice'
    r = client.post('/release', headers=auth_headers())
    assert r.status_code == 200
    data = client.get('/status').get_json()
    assert data['occupied'] is False
    assert 'user' not in data


def test_release_preserves_hosts(client):
    client.post(
        '/host_status',
        json={'hostId': 'OMS-PC', 'cpuPercent': 10},
        headers=auth_headers(),
    )
    client.post('/start', data={'user': 'bob'}, headers=auth_headers())
    client.post('/release', headers=auth_headers())
    data = client.get('/status').get_json()
    assert 'hosts' in data
    assert 'OMS-PC' in data['hosts']


def test_cleaner_end_session_preserves_telescope():
    with op.state_lock:
        op.state.clear()
        op.state['occupied'] = True
        op.state['user'] = 'carol'
        op.state['last_heartbeat'] = '2000-01-01T00:00:00Z'
        op.state['telescope'] = {'tel1': {'hostId': 'tel1', 'ts': op.now_iso(), 'raHours': 1, 'decDeg': 2}}
        op.end_session('timeout')
        op.save_state()
        assert op.state['occupied'] is False
        assert 'user' not in op.state
        assert 'tel1' in op.state['telescope']


def test_auth_required_when_token_configured(client, monkeypatch):
    monkeypatch.setattr(op, 'SECRET_TOKEN', 'prod-token')
    monkeypatch.setattr(op, 'ALLOW_OPEN_API', False)
    r = client.post('/start', data={'user': 'x'})
    assert r.status_code == 401
    r = client.post('/start', data={'user': 'x'}, headers={'Authorization': 'Bearer prod-token'})
    assert r.status_code == 200


def test_open_api_without_token(client, monkeypatch):
    monkeypatch.setattr(op, 'SECRET_TOKEN', '')
    monkeypatch.setattr(op, 'ALLOW_OPEN_API', True)
    r = client.post('/start', data={'user': 'dev'})
    assert r.status_code == 200


def test_start_conflict_409(client):
    client.post('/start', data={'user': 'first'}, headers=auth_headers())
    r = client.post('/start', data={'user': 'second'}, headers=auth_headers())
    assert r.status_code == 409
    body = r.get_json()
    assert body['state']['user'] == 'first'


def test_health(client):
    r = client.get('/health')
    assert r.status_code == 200
    assert r.get_json() == {'ok': True}


def test_datenschutz_page(client):
    r = client.get('/datenschutz')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Informationen zum Datenschutz' in html
    assert 'Universität Potsdam' in html
    assert 'DS-GVO' in html
    assert 'Von der Innenkamera wird keine fortlaufende Videoaufnahme erzeugt' in html
    assert 'Universitätsnetzes' in html
    dashboard = client.get('/')
    assert dashboard.status_code == 200
    assert '/datenschutz' in dashboard.get_data(as_text=True)


def test_session_log_on_release(client):
    client.post(
        '/start',
        data={'user': 'dana', 'target': 'saturn', 'planned_hours': '1'},
        headers=auth_headers(),
    )
    client.post('/release', headers=auth_headers())
    r = client.get('/logbook')
    assert r.status_code == 200
    body = r.get_json()
    assert body['total'] == 1
    entry = body['entries'][0]
    assert entry['user'] == 'dana'
    assert entry['target'] == 'saturn'
    assert entry['endReason'] == 'release'
    assert entry['id']
    assert entry['durationSec'] >= 0


def test_force_logs_previous_session(client):
    client.post('/start', data={'user': 'first'}, headers=auth_headers())
    r = client.post(
        '/start',
        data={'user': 'second', 'target': 'moon', 'force': 'true'},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    body = client.get('/logbook').get_json()
    assert body['total'] == 1
    assert body['entries'][0]['user'] == 'first'
    assert body['entries'][0]['endReason'] == 'force'
    status = client.get('/status').get_json()
    assert status['user'] == 'second'


def test_logbook_limit(client):
    for i in range(3):
        client.post('/start', data={'user': f'u{i}'}, headers=auth_headers())
        client.post('/release', headers=auth_headers())
    r = client.get('/logbook?limit=2')
    body = r.get_json()
    assert body['total'] == 3
    assert len(body['entries']) == 2
    assert body['entries'][0]['user'] == 'u2'


def test_atomic_save_writes_valid_json(client, tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    monkeypatch.setattr(op, 'DATA_FILE', str(path))
    with op.state_lock:
        op.state['occupied'] = True
        op.state['user'] = 'save-test'
        op.save_state()
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    assert data['user'] == 'save-test'


# --- Dashboard login -------------------------------------------------------

def test_dashboard_redirects_when_anonymous(anon_client):
    r = anon_client.get('/')
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/login')


def test_camera_media_requires_login(anon_client):
    r = anon_client.get('/media/cameras/outdoor_current.jpg')
    assert r.status_code == 401


def test_api_endpoints_401_when_anonymous(anon_client):
    for path in ('/status', '/logbook'):
        r = anon_client.get(path)
        assert r.status_code == 401, path
        assert r.get_json()['ok'] is False


def test_login_success_sets_session(anon_client):
    r = login(anon_client)
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/')
    assert anon_client.get('/status').status_code == 200
    html = anon_client.get('/').get_data(as_text=True)
    assert 'Alice Example' in html
    assert 'Sign out' in html


def test_login_wrong_password_rejected(anon_client):
    r = login(anon_client, password='wrong')
    assert r.status_code == 401
    assert 'Sign-in failed' in r.get_data(as_text=True)
    assert anon_client.get('/status').status_code == 401


def test_login_empty_password_rejected(anon_client):
    # An LDAP bind with an empty password is an unauthenticated bind and
    # would otherwise succeed. It must never be accepted.
    r = login(anon_client, password='')
    assert r.status_code in (400, 401)
    assert anon_client.get('/status').status_code == 401


def test_login_unknown_user_rejected(anon_client):
    r = login(anon_client, username='mallory', password='whatever')
    assert r.status_code == 401
    assert anon_client.get('/status').status_code == 401


def test_login_requires_csrf_token(anon_client):
    anon_client.get('/login')
    r = anon_client.post(
        '/login', data={'username': 'alice', 'password': 'correct-horse'}
    )
    assert r.status_code == 400
    assert anon_client.get('/status').status_code == 401


def test_login_lockout_after_repeated_failures(anon_client):
    for _ in range(op.LOGIN_MAX_ATTEMPTS):
        assert login(anon_client, password='wrong').status_code == 401
    r = login(anon_client, password='wrong')
    assert r.status_code == 429
    # Correct credentials are refused too while the lockout is active.
    assert login(anon_client).status_code == 429


def test_login_ignores_offsite_next_target(anon_client):
    r = login(anon_client, next='https://evil.example/phish')
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/')
    assert 'evil.example' not in r.headers['Location']


def test_login_honours_relative_next_target(anon_client):
    r = login(anon_client, next='/logbook')
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/logbook')


def test_logout_clears_session(client):
    html = client.get('/').get_data(as_text=True)
    r = client.post('/logout', data={'csrf_token': csrf_from(html)})
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/login')
    assert client.get('/status').status_code == 401


def test_logout_requires_csrf_token(client):
    assert client.post('/logout').status_code == 400
    assert client.get('/status').status_code == 200


def test_public_pages_need_no_login(anon_client):
    assert anon_client.get('/health').status_code == 200
    r = anon_client.get('/datenschutz')
    assert r.status_code == 200
    assert 'Informationen zum Datenschutz' in r.get_data(as_text=True)


def test_agent_posts_work_without_session(anon_client):
    r = anon_client.post(
        '/start', data={'user': 'agent'}, headers=auth_headers()
    )
    assert r.status_code == 200
    r = anon_client.post(
        '/host_status', json={'hostId': 'OMS-PC', 'cpuPercent': 5}, headers=auth_headers()
    )
    assert r.status_code == 200


def test_expired_session_is_rejected(anon_client, monkeypatch):
    login(anon_client)
    assert anon_client.get('/status').status_code == 200
    monkeypatch.setattr(op, 'SESSION_LIFETIME_HOURS', 0.0)
    assert anon_client.get('/status').status_code == 401


def test_anonymous_dashboard_flag_skips_login(anon_client, monkeypatch):
    monkeypatch.setattr(op, 'ALLOW_ANONYMOUS_DASHBOARD', True)
    assert anon_client.get('/status').status_code == 200
    assert anon_client.get('/').status_code == 200


def test_privacy_page_public_and_english(anon_client):
    r = anon_client.get('/privacy')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Privacy notice' in html
    assert 'lang="en"' in html
    # The things the sign-in actually processes must be named.
    for expected in ('ost_status_session', 'GDPR', 'BbgDSG', 'seven days'):
        assert expected in html, expected
    # Reading it must not set a cookie -- you read it before signing in.
    assert 'Set-Cookie' not in r.headers


def test_privacy_page_links_camera_notice(anon_client):
    html = anon_client.get('/privacy').get_data(as_text=True)
    assert '/datenschutz' in html


def test_both_notices_linked_from_login_page(anon_client):
    html = anon_client.get('/login').get_data(as_text=True)
    assert '/privacy' in html and '/datenschutz' in html


def test_both_notices_linked_from_dashboard(client):
    html = client.get('/').get_data(as_text=True)
    assert '/privacy' in html and '/datenschutz' in html
