"""
test_aas19_identity_proof.py — AAS-19 (2026-10-02): a prova do titular FORA DE BANDA.

PROPOSIÇÕES:
  · o link só nasce para sessão do tenant, âncora entregável e AUTORITATIVA do cliente — e a recusa
    é nomeada, nunca um link que a pessoa descobriria inútil no navegador;
  · o link não é credencial: o GET não envia código, nenhuma página mostra o código (nem o `dev_code`),
    e a âncora aparece só como dica;
  · só a prova CONCLUÍDA vira evidência, pelo escritor único; código errado não grava nada;
  · concluída, o menu que espera é acordado por SINAL (`proof: settled`), e o link morre;
  · no canal `a2a`, link pendente + menu esperando = `TASK_STATE_AUTH_REQUIRED`, com o link no status;
    o chamador pode responder nesse estado, e o stream NÃO fecha nele (spec § 7.6.1).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from plughub_channel_gateway import a2a_tasks as at
from plughub_channel_gateway import identity_proof as ip
from plughub_channel_gateway.tests.test_aas06_a2a_tasks import (
    T, Env, agent_menu, caller, msg, waiting,
)
from plughub_channel_gateway.tests.test_aas07_a2a_stream import (
    alocada, coleta, env_stream, nome, req,
)

SID, CLI, FONE = "sess-1", "cus_a", "+5511999991234"


# ── O módulo da prova ─────────────────────────────────────────────────────────

class KV:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def get(self, k):           return self.kv.get(k)
    async def set(self, k, v, ex=None): self.kv[k] = v
    async def delete(self, *ks):
        for k in ks:
            self.kv.pop(k, None)


class Adapter:
    def __init__(self, challenge=None, verify=None) -> None:
        self.challenge_res = challenge or {"sent": True, "delivery": "dev_log", "dev_code": "424242"}
        self.verify_res = verify or {"verified": True, "provenance": "authoritative"}
        self.calls: list[tuple] = []

    async def otp_challenge(self, t, c, k, v):
        self.calls.append(("challenge", t, c, k, v)); return dict(self.challenge_res)

    async def otp_verify(self, t, c, k, v, code):
        self.calls.append(("verify", t, c, k, v, code)); return dict(self.verify_res)


class Identity:
    def __init__(self, prov="authoritative") -> None:
        self.prov = prov

    async def anchor_provenance(self, t, c, k, v):
        return self.prov


class Mundo:
    def __init__(self, *, prov="authoritative", refusal=None, base="https://pub.example",
                 evidence_status=200, adapter=None) -> None:
        self.r = KV()
        self.r.kv[f"session:{SID}:meta"] = json.dumps({"tenant_id": T, "channel": "a2a"})
        self.adapter = adapter or Adapter()
        self.publicados: list = []
        self.evidencias: list = []

        def tratar(request: httpx.Request) -> httpx.Response:
            self.evidencias.append((str(request.url), request.headers.get("x-service-token"),
                                    json.loads(request.content)))
            return httpx.Response(evidence_status, json={"status": "verified"})

        async def publish(topic, payload, key):
            self.publicados.append((topic, payload, key))

        self.deps = ip.ProofDeps(redis=self.r, adapter=self.adapter, identity=Identity(prov),
                                 otp_refusal=lambda kind: refusal, publish=publish, base_url=base,
                                 mcp_url="http://mcp:3100", service_token="svc",
                                 transport=httpx.MockTransport(tratar))

    async def link(self, **kw) -> dict:
        args = {"tenant_id": T, "session_id": SID, "customer_id": CLI, "kind": "phone", "value": FONE}
        args.update(kw)
        return await ip.create_link(self.deps, **args)


def codigo(url: str) -> str:
    return url.rsplit("/", 1)[1]


class TestCriarLink:
    async def test_controle_cria_link_e_o_fato_da_sessao(self):
        m = Mundo()
        out = await m.link()
        assert out["created"] is True and out["expires_in_s"] == ip.PROOF_TTL_S
        assert out["url"].startswith("https://pub.example/a2a/proof/prf_")
        assert out["anchor_hint"] == "•••• 34"
        fato = json.loads(m.r.kv[ip.session_key(T, SID)])
        assert fato["url"] == out["url"] and fato["mechanism"] == "otp"
        assert FONE not in json.dumps(fato)                  # a âncora não vai ao fato que o A2A lê
        assert json.loads(m.r.kv[ip.code_key(T, codigo(out["url"]))])["value"] == FONE

    @pytest.mark.parametrize("mundo,kw,motivo", [
        ({}, {"session_id": "inexistente"}, "session_unknown"),
        ({}, {"tenant_id": "outro_tenant"}, "session_unknown"),
        ({"refusal": {"sent": False, "reason": "undeliverable_kind"}}, {"kind": "cpf"}, "undeliverable_kind"),
        ({"prov": "declared"}, {}, "anchor_not_authoritative"),
        ({"prov": None}, {}, "anchor_not_authoritative"),
        ({"base": ""}, {}, "not_configured"),
    ])
    async def test_recusas_nomeadas_e_nada_gravado(self, mundo, kw, motivo):
        m = Mundo(**mundo)
        assert await m.link(**kw) == {"created": False, "reason": motivo}
        assert not any(":proof:" in k for k in m.r.kv)

    async def test_novo_link_substitui_o_anterior(self):
        m = Mundo()
        velho = codigo((await m.link())["url"])
        novo = codigo((await m.link())["url"])
        assert velho != novo
        assert ip.code_key(T, velho) not in m.r.kv and ip.code_key(T, novo) in m.r.kv


class TestPagina:
    async def test_get_nao_envia_codigo_e_mostra_so_a_dica(self):
        m = Mundo()
        c = codigo((await m.link())["url"])
        p = await ip.page_get(m.deps, T, c)
        assert p.status == 200 and "•••• 34" in p.html and FONE not in p.html
        assert m.adapter.calls == []

    @pytest.mark.parametrize("c", ["prf_inexistente", "qualquer", ""])
    async def test_codigo_desconhecido_e_410(self, c):
        m = Mundo()
        await m.link()
        assert (await ip.page_get(m.deps, T, c)).status == 410
        assert (await ip.page_post(m.deps, T, c, {"action": "send"})).status == 410

    async def test_enviar_desafia_a_ancora_do_registro_e_nunca_mostra_o_codigo(self):
        m = Mundo()
        c = codigo((await m.link())["url"])
        p = await ip.page_post(m.deps, T, c, {"action": "send"})
        assert p.status == 200 and 'name="code"' in p.html
        assert m.adapter.calls == [("challenge", T, CLI, "phone", FONE)]
        assert "424242" not in p.html                      # o dev_code do modo demo NÃO vai à página

    async def test_muitos_codigos_e_429(self):
        m = Mundo(adapter=Adapter(challenge={"sent": False, "reason": "rate_limited"}))
        c = codigo((await m.link())["url"])
        assert (await ip.page_post(m.deps, T, c, {"action": "send"})).status == 429

    async def test_codigo_errado_nao_grava_nada_e_o_link_continua(self):
        m = Mundo(adapter=Adapter(verify={"verified": False, "reason": "wrong_code", "attempts_left": 4}))
        c = codigo((await m.link())["url"])
        p = await ip.page_post(m.deps, T, c, {"action": "verify", "code": "111111"})
        assert p.status == 400 and "não confere" in p.html
        assert m.evidencias == [] and m.publicados == []
        assert ip.code_key(T, c) in m.r.kv and ip.session_key(T, SID) in m.r.kv

    async def test_controle_prova_concluida_grava_acorda_e_mata_o_link(self):
        m = Mundo()
        c = codigo((await m.link())["url"])
        p = await ip.page_post(m.deps, T, c, {"action": "verify", "code": "42 42-42"})
        assert p.status == 200 and "confirmada" in p.html
        assert m.adapter.calls[-1] == ("verify", T, CLI, "phone", FONE, "424242")
        url, tok, corpo = m.evidencias[0]
        assert url == "http://mcp:3100/internal/identity-evidence" and tok == "svc"
        assert corpo == {"tenant_id": T, "session_id": SID, "mechanism": "otp", "status": "verified",
                         "anchor_kind": "phone", "customer_id": CLI, "source": "authoritative"}
        topic, ev, key = m.publicados[0]
        assert topic == "conversations.inbound" and key == SID
        assert ev["content"] == {"type": "menu_result", "payload": {"proof": "settled"}}
        assert ip.code_key(T, c) not in m.r.kv and ip.session_key(T, SID) not in m.r.kv
        assert (await ip.page_get(m.deps, T, c)).status == 410      # uso único

    async def test_evidencia_recusada_e_503_e_nada_some(self):
        m = Mundo(evidence_status=422)
        c = codigo((await m.link())["url"])
        p = await ip.page_post(m.deps, T, c, {"action": "verify", "code": "424242"})
        assert p.status == 503 and m.publicados == []
        assert ip.session_key(T, SID) in m.r.kv

    def test_dica_da_ancora(self):
        assert ip.anchor_hint("email", "maria@exemplo.com") == "m•••@exemplo.com"
        assert ip.anchor_hint("phone", "+55 11 98765-4321") == "•••• 21"


# ── O canal a2a: AUTH_REQUIRED ───────────────────────────────────────────────

LINK = "https://pub.example/a2a/proof/prf_abc"


def prova_pendente(e: Env, sid: str) -> None:
    e.r.kv[f"{T}:proof:session:{sid}"] = json.dumps({
        "url": LINK, "expires_at": "2026-10-02T12:10:00+00:00", "mechanism": "otp", "anchor_hint": "•••• 34"})


async def nova(e: Env) -> str:
    return (await e.svc.send_message(caller(), {"message": msg("x", {"linha": "1"}),
                                                 "configuration": {"returnImmediately": True}}))["task"]["id"]


async def task(e: Env, sid: str) -> dict:
    return (await e.svc.get_task(caller(), {"id": sid}))


class TestAuthRequired:
    async def test_link_pendente_e_menu_esperando_e_auth_required_com_o_link(self):
        e = Env()
        sid = await nova(e)
        waiting(e, sid); agent_menu(e, sid); prova_pendente(e, sid)
        t = await task(e, sid)
        assert t["status"]["state"] == at.AUTH_REQUIRED
        assert t["metadata"]["plughub"]["reason"] == "identity_proof"
        assert t["metadata"]["plughub"]["auth_deadline"] == "2026-10-02T12:10:00+00:00"
        partes = t["status"]["message"]["parts"]
        assert any(LINK in str(p.get("text") or "") for p in partes)      # o link, mesmo que o prompt não o traga
        auth = [p["data"]["authorization"] for p in partes if "data" in p and "authorization" in p["data"]]
        assert auth == [{"type": "identity_proof", "url": LINK, "mechanism": "otp",
                         "anchor_hint": "•••• 34", "expires_at": "2026-10-02T12:10:00+00:00"}]

    async def test_controle_sem_link_pendente_e_input_required(self):
        e = Env()
        sid = await nova(e)
        waiting(e, sid); agent_menu(e, sid)
        t = await task(e, sid)
        assert t["status"]["state"] == at.INPUT_REQUIRED
        assert not any("authorization" in (p.get("data") or {}) for p in t["status"]["message"]["parts"])

    async def test_link_sem_menu_esperando_nao_e_auth_required(self):
        e = Env()
        sid = await nova(e)
        prova_pendente(e, sid)
        assert (await task(e, sid))["status"]["state"] != at.AUTH_REQUIRED

    async def test_prompt_que_ja_traz_o_link_nao_o_repete(self):
        e = Env()
        sid = await nova(e)
        waiting(e, sid)
        e.r.xadd(sid, {"type": "message", "author_role": "specialist", "visibility": "all",
                       "timestamp": "2026-10-01T10:00:01+00:00",
                       "payload": {"content": {"type": "text", "text": f"Confirme aqui: {LINK}"}}})
        prova_pendente(e, sid)
        t = await task(e, sid)
        textos = [p["text"] for p in t["status"]["message"]["parts"] if "text" in p]
        assert sum(LINK in x for x in textos) == 1

    async def test_cliente_so_texto_nao_recebe_data_part(self):
        e = Env()
        sid = await nova(e)
        waiting(e, sid); agent_menu(e, sid); prova_pendente(e, sid)
        t = await e.svc.get_task(caller(), {"id": sid})
        # GetTask sem modos declarados aceita JSON; o ramo só-texto é o de quem declara só text/plain
        out = e.svc._task_json(sid, await e.svc._record(caller(), sid),
                               await e.svc._facts(T, sid, await e.svc._record(caller(), sid)), False, None)
        assert t["status"]["state"] == out["status"]["state"] == at.AUTH_REQUIRED
        assert not any("data" in p for p in out["status"]["message"]["parts"])

    async def test_o_chamador_pode_responder_em_auth_required(self):
        e = Env()
        sid = await nova(e)
        waiting(e, sid); agent_menu(e, sid); prova_pendente(e, sid)
        e.published.clear()
        await e.svc.send_message(caller(), {"message": msg("a pessoa recusou", taskId=sid),
                                            "configuration": {"returnImmediately": True}})
        inbound = [p for tp, p, _ in e.published if tp == "conversations.inbound"]
        assert len(inbound) == 1 and inbound[0]["session_id"] == sid

    def test_o_prazo_do_link_e_publicado_no_card(self):
        assert at.task_lifetime_extension()["params"]["auth_deadline_field"] == "metadata.plughub.auth_deadline"


class TestStreamEmAuthRequired:
    async def test_stream_nao_fecha_em_auth_required_e_segue_ate_o_fim(self):
        e = env_stream()
        e.svc._stream_ceiling = 3.0
        eventos: list = []
        gen = await e.svc.open_stream(caller(), req("SendStreamingMessage", {"message": msg("x", {"linha": "1"})}))
        tarefa = asyncio.create_task(coleta(gen, eventos))
        await asyncio.sleep(0.05)
        sid = eventos[0]["result"]["task"]["id"]
        alocada(e, sid)
        prova_pendente(e, sid); waiting(e, sid); agent_menu(e, sid)
        await asyncio.sleep(0.15)
        assert not tarefa.done(), "o stream fechou em AUTH_REQUIRED — a prova chega fora de banda"
        # a pessoa provou: o link some, o menu sai, o fluxo conclui
        del e.r.kv[f"{T}:proof:session:{sid}"]; e.r.hashes.pop(f"menu:waiting:{sid}", None)
        e.r.kv[f"{T}:session:{sid}:result"] = json.dumps({
            "outcome": "resolved", "contract": {"checked": True, "valid": True},
            "result": {"from": "x", "value": "ok"}})
        await asyncio.wait_for(tarefa, 2)
        estados = [x["result"]["statusUpdate"]["status"]["state"] for x in eventos if nome(x) == "statusUpdate"]
        assert at.AUTH_REQUIRED in estados and estados[-1] == at.COMPLETED
        # o prompt (com o link) sai UMA vez, no status AUTH_REQUIRED — nunca também como progresso
        progresso = [x for x in eventos if nome(x) == "statusUpdate"
                     and x["result"]["statusUpdate"]["status"]["state"] == at.WORKING
                     and "Como pagar" in json.dumps(x)]
        assert progresso == []

    async def test_controle_input_required_ainda_fecha(self):
        e = env_stream()
        eventos: list = []
        gen = await e.svc.open_stream(caller(), req("SendStreamingMessage", {"message": msg("x", {"linha": "1"})}))
        tarefa = asyncio.create_task(coleta(gen, eventos))
        await asyncio.sleep(0.05)
        sid = eventos[0]["result"]["task"]["id"]
        alocada(e, sid); waiting(e, sid); agent_menu(e, sid)
        await asyncio.wait_for(tarefa, 2)
        assert eventos[-1]["result"]["statusUpdate"]["status"]["state"] == at.INPUT_REQUIRED
