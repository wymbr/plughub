"""
test_attachment_expiry_resolver.py
Unit tests for resolve_attachment_expiry_days.

ATT-04 (2026-10-01): o prazo do anexo é a classe `retention.attachment_days`, lida pelo
`retention_config` (o mesmo cache HTTP do config-api, namespace `retention`). A chave antiga
`webchat.attachment_expiry_days` deixou de ser lida — o teste que a fixava como fonte agora
prova o contrário (um valor nela não muda nada).
"""
import pytest

from ..attachment_store import resolve_attachment_expiry_days
from .. import webchat_config as wc


@pytest.fixture(autouse=True)
def _reset_cache():
    wc.retention_config._data = {}
    wc.webchat_config._data = {}
    yield
    wc.retention_config._data = {}
    wc.webchat_config._data = {}


@pytest.mark.asyncio
async def test_reads_retention_class():
    wc.retention_config._data = {"attachment_days": 14}
    assert await resolve_attachment_expiry_days(None, "tenant_demo", 30) == 14


@pytest.mark.asyncio
async def test_old_webchat_key_is_no_longer_read():
    wc.webchat_config._data = {"attachment_expiry_days": 3}
    assert await resolve_attachment_expiry_days(None, "tenant_demo", 30) == 30


@pytest.mark.asyncio
async def test_falls_back_to_builtin_default_when_absent():
    # Cache vazio → o default do namespace retention (30, igual à semente do config-api).
    assert await resolve_attachment_expiry_days(None, "tenant_demo", 99) == 30


@pytest.mark.asyncio
async def test_coerces_string_value():
    wc.retention_config._data = {"attachment_days": "7"}
    assert await resolve_attachment_expiry_days(None, "tenant_demo", 30) == 7


def test_degradation_names_what_stops_being_valid():
    c = wc.WebchatConfigCache("retention", {"attachment_days": 30})
    assert "attachment_days=30" in c._what_degrades()
    c._data = {"attachment_days": 10}
    assert "LAST loaded" in c._what_degrades()
