"""
test_aas07_a2a_stream.py — AAS-07 (2026-10-01): streaming do canal `a2a` (SSE).

PROPOSIÇÕES (spec A2A v1.0 § 3.1.2, § 3.1.6, § 9.4.2):
  · o primeiro evento é o `Task`; depois `statusUpdate`/`artifactUpdate`; o stream FECHA em estado
    terminal ou interrompido — e não antes (a continuação não fecha no menu já respondido);
  · fala do agente no meio do trabalho chega como `statusUpdate` WORKING com a mensagem; o prompt de
    uma task interrompida vai UMA vez, no status final;
  · `SubscribeToTask`: task terminal é UnsupportedOperation, alheia é TaskNotFound, interrompida
    devolve o `Task` e fecha;
  · erro antes do stream sai como exceção (a porta responde JSON-RPC comum), sem evento nenhum.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from plughub_channel_gateway import a2a_tasks as at
from plughub_channel_gateway.tests.test_aas06_a2a_tasks import (
    Env, FakeRedis, T, agent_menu, agent_text, caller, msg, waiting, OTHER,
)


class StreamRedis(FakeRedis):
    async def xrevrange(self, k, a, b, count=None):
        return list(reversed(self.streams.get(k, [])))[:count or None]

    async def xread(self, streams: dict, block=None, count=None):
        (k, cur), = streams.items()
        novos = [(i, f) for i, f in self.streams.get(k, []) if _n(i) > _n(cur)]
        if novos:
            return [(k, novos)]
        await asyncio.sleep((block or 0) / 1000)
        return []


def _n(eid: str) -> int:
    return int(str(eid).split("-")[0])


def env_stream(**kw) -> Env:
    e = Env()
    e.r = StreamRedis()
    e.svc = at.A2ATaskService(redis=e.r, publish=e.svc._publish, fetch_pool=e.svc._fetch_pool,
                              write_ctx=e.svc._write_ctx, ceiling_s=0.3, poll_s=0.01,
                              block_ms=10, **kw)
    return e


def req(method: str, params: dict, rid="r1") -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


async def coleta(gen, eventos: list) -> None:
    async for ev in gen:
        eventos.append(ev)


def nome(ev) -> str:
    return "ping" if ev is None else next(iter(ev["result"]))


def alocada(e: Env, sid: str) -> None:
    meta = json.loads(e.r.kv[f"session:{sid}:meta"]); meta["instance_id"] = "inst-1"
    e.r.kv[f"session:{sid}:meta"] = json.dumps(meta)


async def abre(e: Env, **extra):
    m = msg("quero a segunda via", {"linha": "1"}, **extra)
    return await e.svc.open_stream(caller(), req("SendStreamingMessage", {"message": m}))


async def test_task_primeiro_fala_no_meio_e_fecha_no_prompt():
    e = env_stream()
    eventos: list = []
    gen = await abre(e)
    tarefa = asyncio.create_task(coleta(gen, eventos))
    await asyncio.sleep(0.05)
    sid = eventos[0]["result"]["task"]["id"]
    assert eventos[0]["result"]["task"]["status"]["state"] == at.SUBMITTED and eventos[0]["id"] == "r1"
    alocada(e, sid)
    await asyncio.sleep(0.05)
    agent_text(e, sid, "Achei sua fatura.")
    await asyncio.sleep(0.05)
    waiting(e, sid); agent_menu(e, sid)
    await asyncio.wait_for(tarefa, 2)
    tipos = [nome(x) for x in eventos]
    assert tipos[0] == "task" and tipos[-1] == "statusUpdate"
    estados = [x["result"]["statusUpdate"]["status"]["state"] for x in eventos[1:]]
    assert at.WORKING in estados and estados[-1] == at.INPUT_REQUIRED
    falas = [p["text"] for x in eventos[1:] for p in x["result"]["statusUpdate"]["status"].get("message", {}).get("parts", [])
             if "text" in p]
    assert falas.count("Achei sua fatura.") == 1
    assert sum("2. PIX" in f for f in falas) == 1                     # o prompt UMA vez, no status final


async def test_prompt_que_chega_ANTES_do_menu_waiting_sai_uma_vez():
    """A ordem real (medida ao vivo): o `notification_send` grava o prompt no stream e SÓ DEPOIS o
    engine marca `menu:waiting`. Entre os dois, a task está WORKING com uma fala nova — e o prompt
    saía como progresso E de novo no status INPUT_REQUIRED."""
    e = env_stream()
    eventos: list = []
    gen = await abre(e)
    tarefa = asyncio.create_task(coleta(gen, eventos))
    await asyncio.sleep(0.05)
    sid = eventos[0]["result"]["task"]["id"]
    alocada(e, sid)
    agent_menu(e, sid)                          # o prompt primeiro...
    await asyncio.sleep(0.005)                  # ...menos que um ciclo...
    waiting(e, sid)                             # ...e só então a espera
    await asyncio.wait_for(tarefa, 2)
    falas = [p["text"] for x in eventos[1:] for p in x["result"]["statusUpdate"]["status"].get("message", {}).get("parts", [])
             if "text" in p]
    assert sum("2. PIX" in f for f in falas) == 1, falas


async def test_concluida_manda_o_artefato_antes_do_status_e_fecha():
    e = env_stream()
    eventos: list = []
    gen = await abre(e)
    tarefa = asyncio.create_task(coleta(gen, eventos))
    await asyncio.sleep(0.05)
    sid = eventos[0]["result"]["task"]["id"]
    e.r.kv[f"{T}:session:{sid}:result"] = json.dumps({
        "outcome": "resolved", "contract": {"checked": True, "valid": True},
        "result": {"from": "fatura", "value": {"linha": "1"}}})
    await asyncio.wait_for(tarefa, 2)
    assert [nome(x) for x in eventos][-2:] == ["artifactUpdate", "statusUpdate"]
    art = eventos[-2]["result"]["artifactUpdate"]
    assert art["artifact"]["parts"][0]["data"] == {"linha": "1"} and art["lastChunk"] is True
    assert eventos[-1]["result"]["statusUpdate"]["status"]["state"] == at.COMPLETED


async def test_continuacao_nao_fecha_no_menu_ja_respondido():
    e = env_stream()
    sid = (await e.svc.send_message(caller(), {"message": msg("x", {"linha": "1"}),
                                                "configuration": {"returnImmediately": True}}))["task"]["id"]
    waiting(e, sid); agent_menu(e, sid)
    eventos: list = []
    gen = await e.svc.open_stream(caller(), req("SendStreamingMessage", {"message": msg("2", taskId=sid)}))
    tarefa = asyncio.create_task(coleta(gen, eventos))
    await asyncio.sleep(0.1)
    assert not tarefa.done()                    # o menu velho segue em menu:waiting — não é o fim
    agent_text(e, sid, "Qual o CPF?")
    await asyncio.wait_for(tarefa, 2)
    fim = eventos[-1]["result"]["statusUpdate"]["status"]
    assert fim["state"] == at.INPUT_REQUIRED and fim["message"]["parts"][0]["text"] == "Qual o CPF?"


async def test_subscribe_terminal_alheia_e_interrompida():
    e = env_stream()
    sid = (await e.svc.send_message(caller(), {"message": msg("x", {"linha": "1"}),
                                                "configuration": {"returnImmediately": True}}))["task"]["id"]
    with pytest.raises(at.A2AError) as alheia:
        await e.svc.open_stream(caller(sub=OTHER), req("SubscribeToTask", {"id": sid}))
    assert alheia.value.code == at.TASK_NOT_FOUND
    waiting(e, sid); agent_menu(e, sid)
    eventos: list = []
    await asyncio.wait_for(coleta(await e.svc.open_stream(caller(), req("SubscribeToTask", {"id": sid})), eventos), 1)
    assert [nome(x) for x in eventos] == ["task"]
    assert eventos[0]["result"]["task"]["status"]["state"] == at.INPUT_REQUIRED
    e.r.kv[f"session:{sid}:closed_recorded"] = "caller_cancel"
    with pytest.raises(at.A2AError) as term:
        await e.svc.open_stream(caller(), req("SubscribeToTask", {"id": sid}))
    assert term.value.code == at.UNSUPPORTED


async def test_erro_antes_do_stream_e_excecao_sem_evento():
    e = env_stream()
    with pytest.raises(at.A2AError) as err:
        await e.svc.open_stream(caller(), req("SendStreamingMessage", {"message": msg("sem dado")}))
    assert err.value.code == at.INVALID_PARAMS and e.published == []


async def test_ocioso_bate_e_fecha_no_teto():
    e = env_stream(heartbeat_s=0.02, stream_ceiling_s=0.15)
    eventos: list = []
    await asyncio.wait_for(coleta(await abre(e), eventos), 2)
    assert nome(eventos[0]) == "task"
    assert None in eventos                      # batimento enquanto nada acontece
    assert all(nome(x) in ("task", "ping") for x in eventos)   # e nenhum estado inventado no teto
