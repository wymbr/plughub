"""
test_aas06_a2a_tasks.py — AAS-06 (2026-10-01): o adapter JSON-RPC do canal `a2a`.

PROPOSIÇÕES:
  · a task É a sessão, e o estado é DEDUZIDO dos fatos dela — e sem fato, a resposta diz que não
    sabe (UNSPECIFIED), nunca um estado plausível;
  · o pedido inicial é ENTRADA DE CONTRATO: conferido contra o `input_schema` e semeado no
    ContextStore antes do roteamento;
  · task e contexto são do PRINCIPAL que os criou: alheio e inexistente têm a MESMA resposta;
  · interrompida, a task só ASSENTA com fala NOVA do agente (nunca devolve o menu já respondido);
  · cancelar é `caller_cancel`, nunca abandono.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from plughub_channel_gateway import a2a_tasks as at

T, POOL, SUB, OTHER = "tenant_t", "segunda_via", "ap_1", "ap_2"
SCHEMA_IN = {"type": "object", "required": ["linha"], "properties": {"linha": {"type": "string"}}}
POOL_DOC = {"pool_id": POOL, "channel_types": ["a2a"],
            "a2a": {"input_schema": SCHEMA_IN, "output_schema": {"type": "object"},
                    "principal_kinds": ["partner"]}}


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self._seq = 0

    async def get(self, k):            return self.kv.get(k)
    async def set(self, k, v, **_):    self.kv[k] = v
    async def setex(self, k, _ttl, v): self.kv[k] = v
    async def expire(self, *_):        return True
    async def delete(self, k):         self.kv.pop(k, None)
    async def rpush(self, k, v):       self.lists.setdefault(k, []).append(v)
    async def lrange(self, k, a, b):   return list(self.lists.get(k, []))
    async def sadd(self, k, v):        self.sets.setdefault(k, set()).add(v)
    async def smembers(self, k):       return set(self.sets.get(k, set()))
    async def zadd(self, k, m):        self.zsets.setdefault(k, {}).update(m)
    async def zrem(self, k, m):        self.zsets.get(k, {}).pop(m, None)
    async def zscore(self, k, m):      return self.zsets.get(k, {}).get(m)
    async def hgetall(self, k):        return dict(self.hashes.get(k, {}))

    async def zrevrange(self, k, a, b):
        return [m for m, _ in sorted(self.zsets.get(k, {}).items(), key=lambda x: -x[1])]

    async def xrange(self, k, a, b, count=None):
        return list(self.streams.get(k, []))

    def xadd(self, sid: str, entry: dict) -> str:
        self._seq += 1
        eid = f"{self._seq}-0"
        self.streams.setdefault(f"session:{sid}:stream", []).append(
            (eid, {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in entry.items()}))
        return eid


class Env:
    def __init__(self, pool=("exists", POOL_DOC, "")) -> None:
        self.r = FakeRedis()
        self.published: list[tuple[str, dict, str]] = []
        self.ctx: list[tuple[str, str, dict]] = []
        self.pool = pool

        async def publish(topic, payload, key):
            self.published.append((topic, payload, key))

        async def fetch(t, p):
            return self.pool

        async def write_ctx(t, sid, tags):
            self.ctx.append((t, sid, tags))

        self.svc = at.A2ATaskService(redis=self.r, publish=publish, fetch_pool=fetch,
                                     write_ctx=write_ctx, ceiling_s=0.3, poll_s=0.01)


def caller(sub=SUB, pool=POOL, kind="partner") -> at.Caller:
    return at.Caller(sub=sub, kind=kind, tenant_id=T, pool_id=pool, slug="sv")


def msg(text=None, data=None, **extra) -> dict:
    parts = ([{"text": text}] if text is not None else []) + ([{"data": data}] if data is not None else [])
    return {"role": "ROLE_USER", "messageId": "m1", "parts": parts, **extra}


async def create(env: Env, c=None, **kw) -> dict:
    return (await env.svc.send_message(c or caller(), {
        "message": msg("quero a segunda via", {"linha": "11999990000"}, **kw),
        "configuration": {"returnImmediately": True}}))["task"]


def agent_text(env: Env, sid: str, text: str) -> str:
    return env.r.xadd(sid, {"type": "message", "author_role": "specialist", "visibility": "all",
                            "timestamp": "2026-10-01T10:00:01+00:00",
                            "payload": {"content": {"type": "text", "text": text}}})


def agent_menu(env: Env, sid: str, menu_id="mn1") -> str:
    return env.r.xadd(sid, {"type": "interaction_request", "author_role": "specialist", "visibility": "all",
                            "timestamp": "2026-10-01T10:00:02+00:00",
                            "payload": {"menu_id": menu_id, "interaction": "button", "prompt": "Como pagar?",
                                        "options": [{"id": "boleto", "label": "Boleto"},
                                                    {"id": "pix", "label": "PIX"}]}})


def waiting(env: Env, sid: str) -> None:
    env.r.hashes[f"menu:waiting:{sid}"] = {"inst-1": json.dumps({"visibility": "all"})}


# ── criação ──────────────────────────────────────────────────────────────────

async def test_criacao_semeia_o_pedido_e_roteia_pelo_pool():
    env = Env()
    task = await create(env)
    sid = task["id"]
    assert task["contextId"] == sid and task["status"]["state"] == at.SUBMITTED
    (t, csid, tags), = env.ctx
    assert (t, csid) == (T, sid)
    assert tags["core.a2a.request"] == {"text": "quero a segunda via", "data": {"linha": "11999990000"}}
    assert tags["core.a2a.principal_id"] == SUB and tags["core.contact.root_session_id"] == sid
    meta = json.loads(env.r.kv[f"session:{sid}:meta"])
    assert meta["channel"] == "a2a" and meta["a2a_principal_id"] == SUB and "instance_id" not in meta
    topics = [(tp, p.get("event_type") or p.get("pool_id"), k) for tp, p, k in env.published]
    assert topics == [("conversations.events", "contact_open", sid), ("conversations.inbound", POOL, sid)]
    assert task["history"][0]["role"] == "ROLE_USER"


async def test_pedido_fora_do_input_schema_e_recusado_nomeando_o_campo():
    env = Env()
    with pytest.raises(at.A2AError) as e:
        await env.svc.send_message(caller(), {"message": msg("oi")})
    assert e.value.code == at.INVALID_PARAMS and "linha" in json.dumps(e.value.data)
    assert env.published == [] and env.ctx == []          # nada nasceu


@pytest.mark.parametrize("pool,code", [
    (("exists", {**POOL_DOC, "channel_types": ["webchat"]}, ""), at.UNSUPPORTED),
    (("exists", {**POOL_DOC, "a2a": {**POOL_DOC["a2a"], "principal_kinds": ["customer_agent"]}}, ""), at.UNSUPPORTED),
    (("not_found", None, "x"), at.UNSUPPORTED),
])
async def test_contrato_conferido_AGORA_nao_so_na_concessao(pool, code):
    env = Env(pool)
    with pytest.raises(at.A2AError) as e:
        await create(env)
    assert e.value.code == code and env.published == []


async def test_registry_fora_e_indisponivel_nunca_aprovacao():
    env = Env(("unavailable", None, "rede"))
    with pytest.raises(at.A2AUnavailable):
        await create(env)


async def test_contextId_so_do_mesmo_principal_e_recusa_nao_e_oraculo():
    env = Env()
    first = await create(env)
    ctx = first["contextId"]
    segunda = await create(env, contextId=ctx)            # o dono continua o contexto
    assert segunda["contextId"] == ctx and segunda["id"] != first["id"]
    erros = []
    for c, cx in ((caller(sub=OTHER), ctx), (caller(), "ctx-que-nao-existe")):
        with pytest.raises(at.A2AError) as e:
            await create(env, c=c, contextId=cx)
        erros.append((e.value.code, e.value.message))
    assert erros[0] == erros[1] and erros[0][0] == at.INVALID_PARAMS


def test_arquivo_em_part_e_tipo_nao_suportado():
    with pytest.raises(at.A2AError) as e:
        at.read_parts({"role": "ROLE_USER", "parts": [{"url": "https://x/y.pdf"}]})
    assert e.value.code == at.CONTENT_TYPE
    with pytest.raises(at.A2AError) as e2:
        at.accepts_json({"acceptedOutputModes": ["image/png"]})
    assert e2.value.code == at.CONTENT_TYPE


# ── estado deduzido ──────────────────────────────────────────────────────────

async def _state(env: Env, sid: str) -> dict:
    return await env.svc.get_task(caller(), {"id": sid})


async def test_estado_deduzido_dos_fatos():
    env = Env()
    sid = (await create(env))["id"]
    assert (await _state(env, sid))["status"]["state"] == at.SUBMITTED
    meta = json.loads(env.r.kv[f"session:{sid}:meta"]); meta["instance_id"] = "inst-1"
    env.r.kv[f"session:{sid}:meta"] = json.dumps(meta)
    assert (await _state(env, sid))["status"]["state"] == at.WORKING
    waiting(env, sid)                                      # espera SEM prompt ainda: não é input
    assert (await _state(env, sid))["status"]["state"] == at.WORKING
    agent_menu(env, sid)
    env.r.zsets[f"{T}:menu:deadlines"] = {sid: 1_790_000_000_000}
    task = await _state(env, sid)
    assert task["status"]["state"] == at.INPUT_REQUIRED
    parts = task["status"]["message"]["parts"]
    assert "1. Boleto" in parts[0]["text"] and "2. PIX" in parts[0]["text"]
    assert parts[1]["data"]["response_schema"]["properties"]["choice"]["enum"] == ["boleto", "pix"]
    assert task["metadata"]["plughub"]["input_deadline"].startswith("2026-")


@pytest.mark.parametrize("closed,state", [
    ("caller_cancel", at.CANCELED), ("no_resource", at.REJECTED), ("session_timeout", at.FAILED),
])
async def test_fechamento_sem_resultado(closed, state):
    env = Env()
    sid = (await create(env))["id"]
    env.r.kv[f"session:{sid}:closed_recorded"] = closed
    task = await _state(env, sid)
    assert task["status"]["state"] == state and task["status"]["message"]["parts"][0]["text"]


async def test_resultado_valido_vira_artefato_e_invalido_vira_falha_com_os_erros():
    env = Env()
    sid = (await create(env))["id"]
    env.r.kv[f"{T}:session:{sid}:result"] = json.dumps({
        "outcome": "resolved", "contract": {"checked": True, "valid": True},
        "result": {"from": "fatura", "value": {"linha": "1"}}})
    task = await _state(env, sid)
    assert task["status"]["state"] == at.COMPLETED
    assert task["artifacts"][0]["parts"][0]["data"] == {"linha": "1"}
    env.r.kv[f"{T}:session:{sid}:result"] = json.dumps({
        "outcome": "resolved", "contract": {"checked": True, "valid": False, "errors": ["linha: x"]},
        "result": {"from": "fatura", "value": {}}})
    task = await _state(env, sid)
    assert task["status"]["state"] == at.FAILED and "artifacts" not in task
    assert task["status"]["message"]["parts"][-1]["data"]["errors"] == ["linha: x"]


async def test_primeira_causa_do_fim_manda_sobre_resultado_tardio():
    """Cancelada, o menu estacionado ainda vence o prazo e o fluxo grava um `complete` FAILED
    depois — a task continua CANCELED. E o fim que É o do fluxo cede ao resultado (controle)."""
    env = Env()
    sid = (await create(env))["id"]
    env.r.kv[f"session:{sid}:closed_recorded"] = "caller_cancel"
    env.r.kv[f"{T}:session:{sid}:result"] = json.dumps({"outcome": "failed", "contract": {}})
    assert (await _state(env, sid))["status"]["state"] == at.CANCELED
    env.r.kv[f"session:{sid}:closed_recorded"] = "flow_complete"
    assert (await _state(env, sid))["status"]["state"] == at.FAILED      # o resultado decide


async def test_sem_fato_nenhum_responde_que_nao_sabe():
    env = Env()
    sid = (await create(env))["id"]
    del env.r.kv[f"session:{sid}:meta"]
    assert (await _state(env, sid))["status"]["state"] == at.UNSPECIFIED


# ── dono ─────────────────────────────────────────────────────────────────────

async def test_task_alheia_e_inexistente_tem_a_mesma_resposta():
    env = Env()
    sid = (await create(env))["id"]
    codes = []
    for c, tid in ((caller(sub=OTHER), sid), (caller(pool="outro"), sid), (caller(), "nao-existe")):
        with pytest.raises(at.A2AError) as e:
            await env.svc.get_task(c, {"id": tid})
        codes.append(e.value.code)
    assert codes == [at.TASK_NOT_FOUND] * 3


# ── continuação ──────────────────────────────────────────────────────────────

async def _interrompida(env: Env) -> str:
    sid = (await create(env))["id"]
    waiting(env, sid)
    agent_menu(env, sid)
    return sid


@pytest.mark.parametrize("reply,content", [
    (msg("2"), {"type": "menu_result", "payload": {"menu_id": "mn1", "interaction": "button", "result": "pix"}}),
    (msg(data={"choice": "boleto"}), {"type": "menu_result",
                                      "payload": {"menu_id": "mn1", "interaction": "button", "result": "boleto"}}),
    (msg("quero falar com alguém"), {"type": "text", "text": "quero falar com alguém"}),
])
async def test_resposta_ao_menu_vira_menu_result_ou_texto_cru(reply, content):
    env = Env()
    sid = await _interrompida(env)
    env.published.clear()
    reply["taskId"] = sid
    await env.svc.send_message(caller(), {"message": reply, "configuration": {"returnImmediately": True}})
    (topic, ev, key), = env.published
    assert topic == "conversations.inbound" and key == sid
    assert ev["author"] == {"type": "customer"} and ev["channel"] == "a2a" and ev["content"] == content


async def test_continuar_task_terminal_ou_que_nao_espera_e_recusado():
    env = Env()
    sid = (await create(env))["id"]
    with pytest.raises(at.A2AError) as e:
        await env.svc.send_message(caller(), {"message": msg("x", taskId=sid)})
    assert e.value.code == at.UNSUPPORTED
    env.r.kv[f"session:{sid}:closed_recorded"] = "session_timeout"
    with pytest.raises(at.A2AError) as e2:
        await env.svc.send_message(caller(), {"message": msg("x", taskId=sid)})
    assert e2.value.code == at.UNSUPPORTED and "contextId" in json.dumps(e2.value.data)


async def test_interrompida_so_assenta_com_fala_NOVA_do_agente():
    env = Env()
    env.svc._ceiling = 2.0
    sid = await _interrompida(env)

    async def agente_responde():
        await asyncio.sleep(0.1)
        agent_text(env, sid, "Qual o CPF?")

    tarefa = asyncio.create_task(agente_responde())
    task = (await env.svc.send_message(caller(), {"message": msg("1", taskId=sid)}))["task"]
    await tarefa
    assert task["status"]["state"] == at.INPUT_REQUIRED
    assert task["status"]["message"]["parts"][0]["text"] == "Qual o CPF?"   # nunca o menu já respondido


# ── cancelar e listar ────────────────────────────────────────────────────────

async def test_cancelar_e_caller_cancel_e_terminal_nao_cancela():
    env = Env()
    sid = (await create(env))["id"]
    env.published.clear()
    await env.svc.cancel_task(caller(), {"id": sid})
    (topic, ev, key), = env.published
    assert (topic, key) == ("conversations.events", sid)
    assert ev["event_type"] == "contact_closed" and ev["reason"] == ev["close_reason"] == "caller_cancel"
    env.r.kv[f"session:{sid}:closed_recorded"] = "caller_cancel"
    with pytest.raises(at.A2AError) as e:
        await env.svc.cancel_task(caller(), {"id": sid})
    assert e.value.code == at.TASK_NOT_CANCELABLE


async def test_listar_so_do_principal_no_pool_e_pelo_contexto():
    env = Env()
    a = await create(env)
    b = await create(env, contextId=a["contextId"])
    c = await create(env)
    await create(env, c=caller(sub=OTHER))
    todas = await env.svc.list_tasks(caller(), {})
    assert {x["id"] for x in todas["tasks"]} == {a["id"], b["id"], c["id"]}
    do_ctx = await env.svc.list_tasks(caller(), {"contextId": a["contextId"]})
    assert {x["id"] for x in do_ctx["tasks"]} == {a["id"], b["id"]}
    pag = await env.svc.list_tasks(caller(), {"pageSize": 2})
    assert len(pag["tasks"]) == 2 and pag["nextPageToken"] == "2" and pag["totalSize"] == 3


# ── despacho ─────────────────────────────────────────────────────────────────

async def test_despacho_jsonrpc():
    env = Env()
    r = await env.svc.handle(caller(), {"jsonrpc": "2.0", "id": 7, "method": "message/send"})
    assert r["error"]["code"] == at.METHOD_NOT_FOUND and r["id"] == 7
    r = await env.svc.handle(caller(), {"id": 1, "method": "GetTask"})
    assert r["error"]["code"] == at.INVALID_REQUEST
    r = await env.svc.handle(caller(), {"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": "x"}})
    assert r["error"]["code"] == at.TASK_NOT_FOUND
