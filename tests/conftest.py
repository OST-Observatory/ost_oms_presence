import os
import tempfile

import pytest

# Configure environment before importing the application module.
_test_data = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
_test_data.close()
os.environ['ALLOW_OPEN_API'] = '1'
os.environ['SECRET_TOKEN'] = 'test-secret'
os.environ['DATA_FILE'] = _test_data.name

import observatory_presence as op  # noqa: E402


@pytest.fixture
def client():
    op.app.config['TESTING'] = True
    with op.app.test_client() as c:
        with op.state_lock:
            op.state.clear()
            op.state['occupied'] = False
            op.save_state()
        yield c
