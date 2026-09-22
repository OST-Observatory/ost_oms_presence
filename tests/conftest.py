import os
import re
import tempfile

import pytest

# Configure environment before importing the application module.
_test_data = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
_test_data.close()
_test_log = tempfile.NamedTemporaryFile(delete=False, suffix='_log.json')
_test_log.close()
os.environ['ALLOW_OPEN_API'] = '1'
os.environ['SECRET_TOKEN'] = 'test-secret'
os.environ['DATA_FILE'] = _test_data.name
os.environ['SESSION_LOG_FILE'] = _test_log.name
# Dashboard login is exercised against a fake directory (see fake_ldap), so no
# LDAP server is needed; the app still runs its full login code path.
os.environ['SESSION_SECRET'] = 'test-session-secret'
os.environ['SESSION_COOKIE_SECURE'] = '0'
os.environ['LDAP_SERVER_URI'] = 'ldaps://ldap.test.invalid'
os.environ['LDAP_USER_SEARCH_BASE'] = 'ou=people,dc=test,dc=invalid'
os.environ.pop('ALLOW_ANONYMOUS_DASHBOARD', None)

import observatory_presence as op  # noqa: E402

TEST_USER = 'alice'
TEST_PASSWORD = 'correct-horse'
_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def reset_state():
    with op.state_lock:
        op.state.clear()
        op.state['occupied'] = False
        op.save_state()
    with op.log_lock:
        op._save_session_log({'entries': []})
    with op.login_lock:
        op.login_failures.clear()


def fake_authenticate(username, password, config=None):
    """Stand-in for ldap_auth.authenticate.

    Accepts one user with one password; every other combination is rejected
    the way a real directory would reject it.
    """
    if not username or not password:
        return None
    if username == TEST_USER and password == TEST_PASSWORD:
        return {'uid': TEST_USER, 'display_name': 'Alice Example', 'groups': ['cn=obs,dc=test']}
    return None


def csrf_from(html):
    match = _CSRF_RE.search(html)
    assert match, 'no CSRF token in response'
    return match.group(1)


def login(client, username=TEST_USER, password=TEST_PASSWORD, **extra):
    """Fetch the form (for its CSRF token) and post credentials."""
    page = client.get('/login')
    data = {
        'csrf_token': csrf_from(page.get_data(as_text=True)),
        'username': username,
        'password': password,
    }
    data.update(extra)
    return client.post('/login', data=data)


@pytest.fixture(autouse=True)
def fake_ldap(monkeypatch):
    monkeypatch.setattr(op, 'authenticate', fake_authenticate)


@pytest.fixture
def anon_client():
    """Test client without a dashboard session."""
    op.app.config['TESTING'] = True
    with op.app.test_client() as c:
        reset_state()
        yield c


@pytest.fixture
def client(anon_client):
    """Test client already logged in as TEST_USER."""
    response = login(anon_client)
    assert response.status_code == 302, 'fixture login failed'
    return anon_client
