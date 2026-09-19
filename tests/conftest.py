import httpx
import pytest

from solarbench.mock_server import STATE, app


@pytest.fixture
def mock_client():
    """httpx client wired straight into the mock ASGI app (no sockets)."""
    STATE["ttft_s"], STATE["tpot_s"] = 0.005, 0.0005
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mock")
