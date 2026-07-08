"""
test_survey_link_delivery.py
Unit tests for the pluggable survey-link delivery layer (survey_web.py):
  - WebhookProvider: POST body + auth header + success/http-error/transport-error.
  - SurveyLinkDelivery router: per-kind routing, mock fallback, config cache/invalidate.

No real SMS/e-mail channel is exercised — the httpx client is faked and the
config is injected, so the whole layer is validated offline.
"""
import pytest

from ..survey_web import MockProvider, WebhookProvider, SurveyLinkDelivery


# ── Fake httpx client (async context manager with .post) ──────────────────────

class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    def __init__(self, resp=None, exc=None, captured=None) -> None:
        self._resp = resp
        self._exc = exc
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        if self._captured is not None:
            self._captured.update({"url": url, "json": json, "headers": headers})
        if self._exc is not None:
            raise self._exc
        return self._resp


def _factory(resp=None, exc=None, captured=None):
    return lambda: _FakeClient(resp, exc, captured)


# ── WebhookProvider ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_success_posts_payload_and_auth():
    captured: dict = {}
    prov = WebhookProvider("https://gw/notify", token="s3cret", client_factory=_factory(_FakeResp(200), captured=captured))

    result = await prov.send("sms", "+5511999", "https://app/survey/tok", "tenant_demo")

    assert result == {"delivered": True, "provider": "webhook", "status": 200}
    assert captured["url"] == "https://gw/notify"
    assert captured["json"] == {
        "kind": "sms", "address": "+5511999", "url": "https://app/survey/tok", "tenant_id": "tenant_demo",
    }
    assert captured["headers"]["Authorization"] == "Bearer s3cret"


@pytest.mark.asyncio
async def test_webhook_no_token_omits_auth_header():
    captured: dict = {}
    prov = WebhookProvider("https://gw/notify", token="", client_factory=_factory(_FakeResp(204), captured=captured))
    await prov.send("email", "a@b.com", "u", "t")
    assert "Authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_webhook_http_error_reports_not_delivered():
    prov = WebhookProvider("https://gw/notify", client_factory=_factory(_FakeResp(502)))
    result = await prov.send("sms", "x", "u", "t")
    assert result["delivered"] is False
    assert result["reason"] == "http_error"
    assert result["status"] == 502


@pytest.mark.asyncio
async def test_webhook_transport_error_is_swallowed():
    prov = WebhookProvider("https://gw/notify", client_factory=_factory(exc=RuntimeError("conn refused")))
    result = await prov.send("sms", "x", "u", "t")
    assert result == {"delivered": False, "provider": "webhook", "reason": "transport_error"}


# ── SurveyLinkDelivery router ────────────────────────────────────────────────

def _fetch(cfg, counter=None):
    async def _f(tenant_id: str):
        if counter is not None:
            counter.append(tenant_id)
        return cfg
    return _f


@pytest.mark.asyncio
async def test_router_defaults_to_mock_when_unconfigured():
    router = SurveyLinkDelivery(config_fetch=_fetch({}), mock_dev_log=True)
    result = await router.send("sms", "+55", "u", "tenant_demo")
    assert result["provider"] == "mock"
    assert result["delivered"] is True


@pytest.mark.asyncio
async def test_router_routes_kind_to_webhook():
    captured: dict = {}
    cfg = {"routes": {"sms": "webhook"}, "webhook": {"url": "https://gw/notify"}}
    router = SurveyLinkDelivery(
        config_fetch=_fetch(cfg), webhook_token="tok", client_factory=_factory(_FakeResp(200), captured=captured),
    )
    result = await router.send("sms", "+55", "https://app/survey/x", "tenant_demo")
    assert result["provider"] == "webhook" and result["delivered"] is True
    assert captured["json"]["kind"] == "sms"
    assert captured["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_router_unrouted_kind_uses_default_provider():
    # default_provider=webhook, but email has no explicit route → still webhook
    cfg = {"default_provider": "webhook", "webhook": {"url": "https://gw/notify"}}
    router = SurveyLinkDelivery(config_fetch=_fetch(cfg), client_factory=_factory(_FakeResp(200)))
    result = await router.send("email", "a@b.com", "u", "t")
    assert result["provider"] == "webhook"


@pytest.mark.asyncio
async def test_router_webhook_without_url_falls_back_to_mock():
    cfg = {"default_provider": "webhook", "webhook": {"url": ""}}
    router = SurveyLinkDelivery(config_fetch=_fetch(cfg), mock_dev_log=True)
    result = await router.send("sms", "+55", "u", "t")
    assert result["provider"] == "mock"


@pytest.mark.asyncio
async def test_router_caches_config_and_invalidates():
    calls: list[str] = []
    router = SurveyLinkDelivery(config_fetch=_fetch({}, counter=calls), mock_dev_log=True)
    await router.send("sms", "+55", "u", "tenant_demo")
    await router.send("sms", "+55", "u", "tenant_demo")
    assert len(calls) == 1                    # second send hit the cache
    router.invalidate("tenant_demo")
    await router.send("sms", "+55", "u", "tenant_demo")
    assert len(calls) == 2                    # re-fetched after invalidation


@pytest.mark.asyncio
async def test_mock_provider_dev_log_off_reports_not_delivered():
    result = await MockProvider(dev_log=False).send("sms", "x", "u", "t")
    assert result == {"delivered": False, "provider": "mock", "reason": "dev_log_off"}
