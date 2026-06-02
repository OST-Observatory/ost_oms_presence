import os
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

import observatory_presence as op  # noqa: E402


@pytest.fixture
def client():
    op.app.config['TESTING'] = True
    with op.app.test_client() as c:
        with op.state_lock:
            op.state.clear()
            op.state['occupied'] = False
            op.save_state()
        with op.log_lock:
            op._save_session_log({'entries': []})
        yield c
