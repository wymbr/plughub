"""
test_aas09_customer_agent.py — AAS-09 (2026-10-01): o token do PRÓPRIO cliente, do lado do gateway.

PROPOSIÇÕES (adr-a2a-server-binding D6, D9):
  · a introspecção de `customer_agent` vira um principal COM titular, prova, mandato, validade e
    cota; sem titular ou validade é recusado, nunca vira `partner`; e o cache de 30 s não estica
    a validade de um token que venceu no meio da janela;
  · a sessão que nasce dele tem o TITULAR como cliente e `core.a2a.holder`/`core.a2a.mandate` —
    e a do `partner` continua com cliente de sistema (controle);
  · a cota conta tasks NÃO terminadas (deduzidas dos fatos, não de contador) e tasks por dia; o
    pedido recusado por contrato não gasta cota; acima dela a porta responde 429 com Retry-After;
  · a página de retirada: o GET não retira (preview de link não queima o código); o POST retira
    e mostra a credencial uma vez, sem cache; código indisponível é 410.
"""
from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from plughub_channel_gateway import a2a_customer_token as act
from plughub_channel_gateway import a2a_principal as apr
from plughub_channel_gateway import a2a_tasks as at
from plughub_channel_gateway import main as gw_main
from plughub_channel_gateway.config import Settings
from plughub_channel_gateway.tests.test_aas06_a2a_tasks import POOL, T, Env, FakeRedis, msg

HOLDER = at.Holder(customer_id="cli-1", proof_mechanism="otp", proof_verified_at="2026-10-01T10:00:00+00:00",
                   mandate=("consultar",))
POOL_CA = {"pool_id": POOL, "channel_types": ["a2a"],
           "a2a": {"input_schema": {"type": "object", "required": ["linha"],
                                    "properties": {"linha": {"type": "string"}}},
                   "output_schema": {"type": "object"}, "principal_kinds": ["partner", "customer_agent"]}}


class QuotaRedis(FakeRedis):
    async def incr(self, k):
        self.kv[k] = str(int(self.kv.get(k) or 0) + 1)
        return int(self.kv[k])


def env_ca() -> Env:
    e = Env(pool=("exists", POOL_CA, ""))
    e.r = QuotaRedis()
    e.svc._r = e.r
    return e


def cliente(quota=(2, 3)) -> at.Caller:
    return at.Caller(sub="ca_1", kind="customer_agent", tenant_id=T, pool_id=POOL, slug="sv",
                     holder=HOLDER, quota=quota)


async def nova(env: Env, c: at.Caller, data=None) -> dict:
    return (await env.svc.send_message(c, {
        "message": msg("oi", data if data is not None else {"linha": "1"}),
        "configuration": {"returnImmediately": True}}))["task"]


# ── introspecção → principal ─────────────────────────────────────────────────

def _intro(body: dict):
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(h))


CA_BODY = {"active": True, "sub": "ca_1", "tenant_id": T, "kind": "customer_agent", "display_name": "x",
           "allowed_pools": [POOL], "customer_id": "cli-1", "proof_mechanism": "otp",
           "proof_verified_at": "2026-10-01T10:00:00+00:00", "mandate": ["consultar"],
           "exp": int(time.time()) + 3600, "max_active_tasks": 2, "max_tasks_per_day": 20}


async def test_customer_agent_vira_principal_com_titular_e_cota():
    apr.clear_cache()
    async with _intro(CA_BODY) as c:
        r = await apr.authenticate("Bearer pha_a", auth_api_url="http://a", service_token="s", client=c)
    p = r.principal
    assert r.outcome == "ok" and p.customer_id == "cli-1" and p.mandate == ("consultar",)
    assert (p.max_active_tasks, p.max_tasks_per_day) == (2, 20) and p.exp == CA_BODY["exp"]


@pytest.mark.parametrize("falta", ["customer_id", "exp"])
async def test_customer_agent_sem_titular_ou_validade_e_recusado(falta):
    apr.clear_cache()
    async with _intro({k: v for k, v in CA_BODY.items() if k != falta}) as c:
        r = await apr.authenticate("Bearer pha_b", auth_api_url="http://a", service_token="s", client=c)
    assert r.outcome == "invalid"


async def test_cache_nao_estica_validade_vencida():
    apr.clear_cache()
    async with _intro({**CA_BODY, "exp": int(time.time()) + 3600}) as c:
        assert (await apr.authenticate("Bearer pha_c", auth_api_url="http://a", service_token="s", client=c)).outcome == "ok"
    # o mesmo principal no cache, agora vencido
    key = next(iter(apr._cache))
    res, until = apr._cache[key]
    vencido = apr.AuthResult("ok", principal=apr.Principal(**{**res.principal.__dict__, "exp": int(time.time()) - 1}))
    apr._cache[key] = (vencido, until)
    r = await apr.authenticate("Bearer pha_c", auth_api_url="http://a", service_token="s")
    assert r.outcome == "invalid"


# ── a sessão nasce do titular ────────────────────────────────────────────────

async def test_sessao_do_customer_agent_tem_o_titular_como_cliente():
    env = env_ca()
    sid = (await nova(env, cliente()))["id"]
    (_, _, tags), = env.ctx
    assert tags["core.a2a.holder"]["customer_id"] == "cli-1" and tags["core.a2a.mandate"] == ["consultar"]
    inbound = [p for t, p, _ in env.published if t == "conversations.inbound"][0]
    assert inbound["customer_id"] == "cli-1"
    import json as _j
    assert _j.loads(env.r.kv[f"session:{sid}:meta"])["customer_id"] == "cli-1"


async def test_controle_partner_continua_com_cliente_de_sistema():
    env = env_ca()
    p = at.Caller(sub="ap_1", kind="partner", tenant_id=T, pool_id=POOL, slug="sv")
    await nova(env, p)
    (_, _, tags), = env.ctx
    assert "core.a2a.holder" not in tags
    inbound = [x for t, x, _ in env.published if t == "conversations.inbound"][0]
    assert inbound["customer_id"].startswith("sys:a2a:")


# ── cota ─────────────────────────────────────────────────────────────────────

async def test_teto_de_tasks_ativas_e_deduzido_dos_fatos():
    env = env_ca()
    c = cliente(quota=(2, 50))
    a = (await nova(env, c))["id"]
    await nova(env, c)
    with pytest.raises(at.A2AQuota) as q:
        await nova(env, c)
    assert q.value.which == "active" and q.value.limit == 2
    # uma termina (fato), a vaga volta — sem decremento de ninguém
    env.r.kv[f"session:{a}:closed_recorded"] = "caller_cancel"
    await nova(env, c)


async def test_teto_diario_e_retry_ate_a_virada():
    env = env_ca()
    c = cliente(quota=(50, 2))
    for _ in range(2):
        await nova(env, c)
    with pytest.raises(at.A2AQuota) as q:
        await nova(env, c)
    assert q.value.which == "daily" and 0 < q.value.retry_after_s <= 86_400


async def test_pedido_fora_do_contrato_nao_gasta_cota():
    env = env_ca()
    c = cliente(quota=(50, 1))
    with pytest.raises(at.A2AError):
        await nova(env, c, data={})            # falta `linha`
    await nova(env, c)                         # a única do dia ainda está lá


async def test_controle_partner_sem_cota_nao_conta():
    env = env_ca()
    p = at.Caller(sub="ap_1", kind="partner", tenant_id=T, pool_id=POOL, slug="sv")
    for _ in range(5):
        await nova(env, p)
    assert not [k for k in env.r.kv if ":a2a:quota:" in k]


# ── a porta: 429 e a página de retirada ──────────────────────────────────────

@pytest.fixture
def porta(monkeypatch):
    settings = Settings(tenant_id=T, auth_api_url="http://auth", auth_api_service_token="svc",
                        agent_registry_url="http://reg", a2a_public_base_url="https://pub.example")
    monkeypatch.setattr(gw_main, "get_settings", lambda: settings)
    return TestClient(gw_main.app)


def test_cota_esgotada_e_429_com_retry_after(porta, monkeypatch):
    p = apr.Principal(sub="ca_1", tenant_id=T, kind="customer_agent", display_name="x",
                      allowed_pools=frozenset({POOL}), customer_id="cli-1", exp=int(time.time()) + 60,
                      max_active_tasks=1, max_tasks_per_day=1)

    async def _auth(*a, **kw):
        return apr.AuthResult("ok", principal=p)

    async def _res(**kw):
        return gw_main.ResolvedEndpoint(POOL, "external", False, None, "found")

    class _Svc:
        async def handle(self, caller, req):
            assert caller.holder.customer_id == "cli-1" and caller.quota == (1, 1)
            raise at.A2AQuota("daily", 1, 123)

    monkeypatch.setattr(apr, "authenticate", _auth)
    monkeypatch.setattr(gw_main, "resolve_endpoint", _res)
    monkeypatch.setattr(gw_main, "_a2a_service", lambda: _Svc())
    r = porta.post("/a2a/sv", json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}},
                   headers={"Authorization": "Bearer pha_x", "A2A-Version": "1.0"})
    assert r.status_code == 429 and r.headers["retry-after"] == "123"
    assert r.json() == {"error": "quota_exceeded", "quota": "daily", "limit": 1}


def test_get_da_retirada_nao_retira(porta, monkeypatch):
    chamou = []

    async def _redeem(*a, **kw):
        chamou.append(1)
        return act.Redeemed("ok", principal={})
    monkeypatch.setattr(act, "redeem", _redeem)
    r = porta.get("/a2a/customer-token/pkc_x")
    assert r.status_code == 200 and "<form method=\"post\">" in r.text
    assert chamou == [] and r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_post_retira_e_mostra_uma_vez_sem_cache(porta, monkeypatch):
    async def _redeem(code, **kw):
        assert code == "pkc_x" and kw["tenant_id"] == T
        return act.Redeemed("ok", principal={"credential": "pha_SEGREDO", "expires_at": "2026-10-08",
                                             "allowed_pools": [POOL], "mandate": ["consultar"],
                                             "customer_id": "cli-1", "agent_principal_id": "ca_1"})

    async def _cards(settings, pools):
        return [f"https://pub.example/a2a/sv/.well-known/agent-card.json"]
    monkeypatch.setattr(act, "redeem", _redeem)
    monkeypatch.setattr(gw_main, "_a2a_card_urls", _cards)
    r = porta.post("/a2a/customer-token/pkc_x")
    assert r.status_code == 200 and "pha_SEGREDO" in r.text and "agent-card.json" in r.text
    assert r.headers["cache-control"] == "no-store" and "default-src 'none'" in r.headers["content-security-policy"]


def test_post_de_codigo_indisponivel_e_410(porta, monkeypatch):
    async def _redeem(code, **kw):
        return act.Redeemed("gone")
    monkeypatch.setattr(act, "redeem", _redeem)
    r = porta.post("/a2a/customer-token/pkc_x")
    assert r.status_code == 410 and "pha_" not in r.text
