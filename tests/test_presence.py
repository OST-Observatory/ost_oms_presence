import json

import observatory_presence as op


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
