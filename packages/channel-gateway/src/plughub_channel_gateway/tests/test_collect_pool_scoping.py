"""
test_collect_pool_scoping.py
Segurança Fase B (J4c) — o pool da SESSÃO PESQUISADA flui na escrita do survey pelo
caminho collect-based:

  handle_collect        → resolve o pool do ALVO (signal_target_id) do ctx
                          (session.pool.id) e o congela no pending (signal_pool_id).
  handle_collect_engage → semeia session.survey_pool_id no ctx da sessão de survey.
                          O runner (skill_survey_runner_v1) lê essa tag e a passa ao
                          survey_record → a resposta nasce com o pool do atendimento
                          pesquisado, NÃO com o pool de infra do survey.

Cobre grain=session (alvo = origem), grain=journey (alvo = raiz) e o caso degradado
(ctx do alvo ausente → pool vazio = admin-only, decisão C, sem crash). Offline: fake
redis mínimo (sem dependência externa) — os métodos exercidos são poucos.
"""
import json
import types

import pytest

from ..adapters.webhook import WebhookAdapter


# ── Fake redis assíncrono mínimo (hset/hget/set/get/expire) ───────────────────
class _FakeRedis:
    def __init__(self) -> None:
        self._kv: dict = {}
        self._hash: dict = {}

    async def hget(self, key, field):
        return self._hash.get(key, {}).get(field)

    async def hset(self, key, field=None, value=None, *, mapping=None):
        h = self._hash.setdefault(key, {})
        if mapping is not None:
            h.update(mapping)
        else:
            h[field] = value
        return 1

    async def set(self, key, value, ex=None, keepttl=None):
        self._kv[key] = value
        return True

    async def get(self, key):
        return self._kv.get(key)

    async def expire(self, key, ttl):
        return True


def _ctx_entry(value: str) -> str:
    return json.dumps({
        "value": value, "confidence": 1.0, "source": "test",
        "visibility": "agents_only", "updated_at": "2026-07-23T00:00:00+00:00",
    })


def _adapter(redis) -> WebhookAdapter:
    # producer/settings não são exercidos por handle_collect/engage.
    return WebhookAdapter(producer=None, redis=redis, settings=types.SimpleNamespace(), db_pool=None)


async def _collect(a, *, tenant, caller, token, grain):
    return await a.handle_collect(
        tenant_id=tenant, session_id=caller, customer_id="cus_x", step_id="pesquisar",
        collect_token=token, target={"type": "customer", "id": "cus_x"},
        interaction="form", prompt="P", channel="webchat",
        channel_policy={"channels": {"webchat": "survey_web_ia"}},
        dialog_form_id="dialog_nps_buttons", signal_grain=grain,
    )


@pytest.mark.asyncio
async def test_grain_session_stamps_origin_pool():
    r = _FakeRedis()
    tenant, caller, origin = "tenant_demo", "wf_sess_1", "contact_sess_1"
    # o ALVO (origem) tem o pool escrito pela Routing Engine
    await r.hset(f"{tenant}:ctx:{origin}", "session.pool.id", _ctx_entry("retencao_humano"))
    # o workflow chamador aponta a origem (grain=session resolve o alvo daqui)
    await r.hset(f"{tenant}:ctx:{caller}", "session.origin_session_id", _ctx_entry(origin))

    a = _adapter(r)
    res = await _collect(a, tenant=tenant, caller=caller, token="tok_1", grain="session")
    assert res["link"] == "/survey/tok_1"

    pending = json.loads(await r.get(f"{tenant}:collect:tok_1"))
    assert pending["signal_target_id"] == origin
    # pool do ALVO, não o pool de survey (survey_web_ia)
    assert pending["signal_pool_id"] == "retencao_humano"

    eng = await a.handle_collect_engage(tenant_id=tenant, collect_token="tok_1", jwt_secret_default="s")
    sid = eng["session_id"]
    seeded = await r.hget(f"{tenant}:ctx:{sid}", "session.survey_pool_id")
    assert seeded is not None and json.loads(seeded)["value"] == "retencao_humano"
    assert json.loads(await r.hget(f"{tenant}:ctx:{sid}", "session.survey_target_id"))["value"] == origin


@pytest.mark.asyncio
async def test_grain_journey_stamps_root_pool():
    r = _FakeRedis()
    tenant, caller, root = "tenant_demo", "wf_sess_2", "root_sess_2"
    await r.hset(f"{tenant}:ctx:{root}", "session.pool.id", _ctx_entry("sac_ia"))
    # journey grain: alvo = raiz canônica lida do chamador
    await r.hset(f"{tenant}:ctx:{caller}", "session.root_session_id", _ctx_entry(root))

    a = _adapter(r)
    await _collect(a, tenant=tenant, caller=caller, token="tok_2", grain="journey")

    pending = json.loads(await r.get(f"{tenant}:collect:tok_2"))
    assert pending["signal_target_id"] == root
    assert pending["signal_pool_id"] == "sac_ia"

    eng = await a.handle_collect_engage(tenant_id=tenant, collect_token="tok_2", jwt_secret_default="s")
    seeded = await r.hget(f"{tenant}:ctx:{eng['session_id']}", "session.survey_pool_id")
    assert json.loads(seeded)["value"] == "sac_ia"


@pytest.mark.asyncio
async def test_empty_pool_when_target_ctx_absent_degrades_not_crashes():
    r = _FakeRedis()
    tenant, caller = "tenant_demo", "wf_sess_3"
    # raiz aponta p/ sessão sem ctx (ex.: expirado) → pool não resolve
    await r.hset(f"{tenant}:ctx:{caller}", "session.root_session_id", _ctx_entry("root_missing"))

    a = _adapter(r)
    await _collect(a, tenant=tenant, caller=caller, token="tok_3", grain="journey")

    pending = json.loads(await r.get(f"{tenant}:collect:tok_3"))
    assert pending["signal_pool_id"] == ""  # vazio (admin-only), sem crash

    # engage NÃO semeia a tag quando o pool é vazio → ref do runner resolve null
    eng = await a.handle_collect_engage(tenant_id=tenant, collect_token="tok_3", jwt_secret_default="s")
    assert await r.hget(f"{tenant}:ctx:{eng['session_id']}", "session.survey_pool_id") is None
