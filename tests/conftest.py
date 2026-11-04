import pytest

from blaeckpy import _status


@pytest.fixture(autouse=True)
def _no_status_page(monkeypatch):
    """No status page in this process unless a test asks for one: none listening on 8080, no
    firewall prompt. The examples run in processes of their own, each with its page."""
    monkeypatch.setattr(_status, "DEFAULT_PORT", None)
