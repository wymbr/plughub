"""
_a2a_sdk_exercise.py — o SDK OFICIAL do A2A (a2a-sdk, Python) contra a porta `a2a` (AAS-08).

Peça do `probe_aas08_a2a_sdk.sh`, que roda isto num container `python:3.11-slim` com o SDK numa
versão FIXA. Imprime UMA linha JSON por ramo (`{"ramo","ok","detalhe"}`) e não julga nada além do
próprio ramo: quem decide verde/vermelho é o probe.

Por que o SDK e não `curl`: o SDK lê nossas respostas com `json_format.ParseDict` ESTRITO (campo que
o proto não tem, enum ou timestamp fora do formato derrubam a leitura) e escolhe transporte, versão e
método pelo CARD. É o leitor do contrato — o que o `curl` dos outros probes não é.

Entrada por env: A2A_BASE (ex.: http://localhost:8010/a2a/probe-aas06), A2A_CRED, A2A_CRED2.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx
from google.protobuf.json_format import MessageToDict, ParseDict

from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import (
    CancelTaskRequest, GetTaskRequest, ListTasksRequest, SendMessageRequest, SubscribeToTaskRequest,
)

BASE = os.environ["A2A_BASE"].rstrip("/")
CRED, CRED2 = os.environ["A2A_CRED"], os.environ["A2A_CRED2"]


def out(ramo: str, ok: bool, detalhe: object = "") -> None:
    print(json.dumps({"ramo": ramo, "ok": bool(ok), "detalhe": str(detalhe)[:400]}), flush=True)


def estado(task) -> str:
    return MessageToDict(task).get("status", {}).get("state", "")


def msg(parts: list[dict], **extra) -> SendMessageRequest:
    m = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": parts, **extra}
    return ParseDict({"message": m}, SendMessageRequest())


async def cliente(cred: str, streaming: bool):
    http = httpx.AsyncClient(headers={"Authorization": f"Bearer {cred}"}, timeout=60)
    card = await A2ACardResolver(http, BASE).get_agent_card()
    fac = ClientFactory(ClientConfig(streaming=streaming, httpx_client=http))
    return card, fac.create(card)


async def eventos(gen) -> list[dict]:
    return [MessageToDict(e) async for e in gen]


async def main() -> None:
    # K1 — o card, lido pelo resolvedor do SDK no caminho padrão relativo ao endereço do agente
    try:
        card, c = await cliente(CRED, streaming=False)
        d = MessageToDict(card)
        url = d["supportedInterfaces"][0]["url"]
        out("K1", d["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC" and url.endswith("/a2a/probe-aas06/")
            and d["capabilities"].get("streaming") is True,
            f"interface={url} streaming={d['capabilities'].get('streaming')}")
    except Exception as exc:  # noqa: BLE001 — sem card, nada a medir
        out("K1", False, f"{type(exc).__name__}: {exc}")
        return

    # K2 — task nova, sem streaming: o SDK lê o Task estritamente
    try:
        ev = await eventos(c.send_message(msg([{"text": "segunda via"}, {"data": {"linha": "11999990000"}}])))
        t = ev[0]["task"]
        tid, ctx = t["id"], t["contextId"]
        out("K2", t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED", f"estado={t['status']['state']}")
    except Exception as exc:  # noqa: BLE001
        out("K2", False, f"{type(exc).__name__}: {exc}")
        return

    # K3 — continuação "2" → COMPLETED com o artefato
    try:
        ev = await eventos(c.send_message(msg([{"text": "2"}], taskId=tid, contextId=ctx)))
        t = ev[-1]["task"]
        art = t.get("artifacts", [{}])[0].get("parts", [{}])[0].get("data")
        out("K3", t["status"]["state"] == "TASK_STATE_COMPLETED" and art == "pix", f"estado={t['status']['state']} artefato={art}")
    except Exception as exc:  # noqa: BLE001
        out("K3", False, f"{type(exc).__name__}: {exc}")

    # K4 — GetTask com histórico, lido estritamente
    try:
        t = MessageToDict(await c.get_task(GetTaskRequest(id=tid)))
        papeis = [m["role"] for m in t.get("history", [])]
        out("K4", t["status"]["state"] == "TASK_STATE_COMPLETED" and "ROLE_USER" in papeis and "ROLE_AGENT" in papeis,
            f"estado={t['status']['state']} historico={papeis}")
    except Exception as exc:  # noqa: BLE001
        out("K4", False, f"{type(exc).__name__}: {exc}")

    # K5 — ListTasks pelo contextId
    try:
        r = MessageToDict(await c.list_tasks(ListTasksRequest(context_id=ctx)))
        ids = [t["id"] for t in r.get("tasks", [])]
        out("K5", tid in ids, f"ids={ids}")
    except Exception as exc:  # noqa: BLE001
        out("K5", False, f"{type(exc).__name__}: {exc}")

    # K6 — o SDK escolhe STREAMING pelo card: Task primeiro, INPUT_REQUIRED por último
    tid2 = ""
    try:
        _, cs = await cliente(CRED, streaming=True)
        ev = await eventos(cs.send_message(msg([{"data": {"linha": "1"}}], contextId=ctx)))
        tipos = [next(iter(e)) for e in ev]
        tid2 = ev[0]["task"]["id"]
        ult = ev[-1]["statusUpdate"]["status"]["state"] if "statusUpdate" in ev[-1] else ev[-1]["task"]["status"]["state"]
        out("K6", tipos[0] == "task" and ult == "TASK_STATE_INPUT_REQUIRED", f"tipos={tipos} ultimo={ult}")
    except Exception as exc:  # noqa: BLE001
        out("K6", False, f"{type(exc).__name__}: {exc}")

    # K7 — SubscribeToTask na interrompida: o Task e fecha
    if tid2:
        try:
            ev = await asyncio.wait_for(eventos(cs.subscribe(SubscribeToTaskRequest(id=tid2))), 30)
            out("K7", [next(iter(e)) for e in ev] == ["task"], f"tipos={[next(iter(e)) for e in ev]}")
        except Exception as exc:  # noqa: BLE001
            out("K7", False, f"{type(exc).__name__}: {exc}")

        # K8 — CancelTask
        try:
            await c.cancel_task(CancelTaskRequest(id=tid2))
            s = ""
            for _ in range(40):
                s = estado(await c.get_task(GetTaskRequest(id=tid2)))
                if s == "TASK_STATE_CANCELED":
                    break
                await asyncio.sleep(0.5)
            out("K8", s == "TASK_STATE_CANCELED", f"estado={s}")
        except Exception as exc:  # noqa: BLE001
            out("K8", False, f"{type(exc).__name__}: {exc}")

    # K9 — os erros chegam TIPADOS no SDK (código JSON-RPC certo): assinar terminal, task alheia
    try:
        await eventos(cs.subscribe(SubscribeToTaskRequest(id=tid)))
        out("K9", False, "assinar task terminal NÃO levantou erro")
    except Exception as exc:  # noqa: BLE001
        out("K9", type(exc).__name__ == "UnsupportedOperationError", f"{type(exc).__name__}: {exc}")
    try:
        _, c2 = await cliente(CRED2, streaming=False)
        await c2.get_task(GetTaskRequest(id=tid))
        out("K10", False, "outro principal LEU a task")
    except Exception as exc:  # noqa: BLE001
        out("K10", type(exc).__name__ == "TaskNotFoundError", f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
