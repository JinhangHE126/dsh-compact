import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_gateway import start_mock_gateway  # noqa: E402


@pytest.fixture
def mock_gateway():
    """Threaded OpenAI-compatible mock; yields MockGateway, then stops."""
    gw = start_mock_gateway()
    try:
        yield gw
    finally:
        gw.stop()
