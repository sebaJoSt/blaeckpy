import pytest

from blaeckpy import _status


@pytest.fixture(autouse=True)
def _no_status_page(monkeypatch):
    """No status page unless a test asks for one: none listening on 8080, no firewall prompt."""
    monkeypatch.setattr(_status, "DEFAULT_PORT", None)
