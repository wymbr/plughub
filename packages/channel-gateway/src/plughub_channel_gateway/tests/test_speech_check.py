"""
tests/test_speech_check.py — verificação ativa do caminho de fala (VOZ-23), sem rede.

Proposições: o julgamento é por item e só com números; a orquestração nunca deixa o endpoint temporário
para trás, nunca entrega "completed" sem o resumo do gateway, e cada falha vira resultado NOMEADO; as
transcrições vão para o item em cuja janela chegaram; o pedido é recusado antes de abrir chamada quando
não pode valer; a rota exige credencial e não libera com credencial vazia.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from ..config import Settings
from ..speech_check import reference
from ..speech_check.runner import CheckFailed, CheckRequest, Timing, run_check
from ..speech_check.server import build_app

RAPIDO = Timing(settle_s=0, window_s=0.02, bot_voice_s=0.1, summary_s=0.2)


class TestReference:
    def test_normaliza_acento_pontuacao_e_caixa(self):
        assert reference.normalize("Não entendi a PERGUNTA!") == ["nao", "entendi", "a", "pergunta"]

    def test_wer(self):
        assert reference.wer(["segunda", "via", "da", "fatura"], ["segunda", "via", "da", "batura"]) == 0.25
        assert reference.wer(["a"], []) == 1.0 and reference.wer(["a", "b"], ["a", "b"]) == 0.0

    def test_frase_certa_errada_e_nao_transcrita(self):
        it = reference.Item("p", "phrase", "Cancelar.")
        assert reference.judge(it, [("cancelar", 0.8)]) == {
            "id": "p", "kind": "phrase", "transcripts": 1, "confidence": 0.8,
            "hallucinated": None, "correct": True, "wer": 0.0}
        errada = reference.judge(it, [("Calendar.", 0.4), ("", None)])
        assert (errada["correct"], errada["wer"], errada["transcripts"]) == (False, 1.0, 1)
        vazia = reference.judge(it, [])
        assert (vazia["correct"], vazia["confidence"], vazia["transcripts"]) == (False, None, 0)

    def test_frase_em_dois_pedacos_junta_e_leva_a_menor_confianca(self):
        it = reference.Item("p", "phrase", "Quero falar com um atendente.")
        j = reference.judge(it, [("Quero falar", 0.9), ("com um atendente.", 0.6)])
        assert j["correct"] is True and j["confidence"] == 0.6

    def test_ruido_alucinado(self):
        it = reference.Item("n", "noise")
        assert reference.judge(it, [("Obrigado.", 0.5)])["hallucinated"] is True
        assert reference.judge(it, [])["hallucinated"] is False

    def test_julgamento_nao_carrega_texto(self):
        j = reference.judge(reference.Item("p", "phrase", "Cancelar."), [("meu cpf e 123", 0.5)])
        assert "cpf" not in json.dumps(j)

    def test_resumo_sem_frase_e_nulo_nao_zero(self):
        s = reference.summarize([])
        assert s["accuracy"] is None and s["confidence_p50"] is None and s["phrases_total"] == 0

    def test_conjunto_so_em_portugues_e_ruido_deterministico(self):
        assert reference.items_for("pt-BR") and reference.items_for("pt") and reference.items_for("en") is None
        assert reference.noise_pcm(0.1) == reference.noise_pcm(0.1) and len(reference.noise_pcm(0.1)) == 3200


class _Listener:
    def __init__(self, deps):
        self.d = deps

    async def start(self):
        if self.d.listener_falha:
            raise RuntimeError("kafka fora")
        self.d.eventos.append("listener.start")

    def transcripts(self, sid, since, until):
        return [(t, c) for chegada, s, t, c in self.d.falas if s == sid and since <= chegada < until]

    async def summary(self, sid, timeout_s):
        return self.d.resumo

    async def stop(self):
        self.d.eventos.append("listener.stop")


class _Call:
    def __init__(self, deps):
        self.d = deps
        self.n = 0

    async def open(self, identifier):
        self.d.eventos.append(f"open {identifier}")
        if self.d.atende is False:
            raise CheckFailed("call_not_answered", "ninguem")
        return "sid-1"

    async def wait_bot_voice(self, timeout_s):
        return True

    async def speak(self, pcm):
        # o gateway "transcreve" durante a fala: a transcrição chega DENTRO da janela do item
        roteiro = self.d.roteiro[self.n] if self.n < len(self.d.roteiro) else []
        self.n += 1
        for texto, conf in roteiro:
            self.d.falas.append((time.monotonic(), "sid-1", texto, conf))
        self.d.falas.append((time.monotonic(), "outra-sessao", "Cancelar.", 0.9))   # não é desta chamada

    async def close(self):
        self.d.eventos.append("close")


class _Deps:
    def __init__(self, **kw):
        self.perfis = kw.get("perfis", {"sip": {"stt_language": "pt-BR"}})
        self.tts_falha = kw.get("tts_falha", False)
        self.listener_falha = kw.get("listener_falha", False)
        self.atende = kw.get("atende", True)
        self.resumo = kw.get("resumo", {"speech_profile_id": "sip", "stt_model": "m", "discarded_vad": 1,
                                        "utterances_sent": 7, "segmentation": {"end_silence_ms": 700}})
        self.roteiro = kw.get("roteiro", [[(it.text, 0.8)] if it.kind == "phrase" else []
                                          for it in reference.ITEMS_PT])
        self.eventos: list[str] = []
        self.falas: list = []
        self.criados: list = []

    async def get_profiles(self, tenant):
        return self.perfis

    async def create_endpoint(self, tenant, identifier, pool_id, settings):
        self.criados.append((identifier, pool_id, settings))
        return "ep-1"

    async def delete_endpoint(self, tenant, ep):
        self.eventos.append(f"delete {ep}")

    async def synthesize(self, text):
        if self.tts_falha:
            raise RuntimeError("speaches fora")
        return b"\x01\x00" * 10

    def listener(self):
        return _Listener(self)

    def call(self):
        return _Call(self)


def _roda(deps, perfil="sip"):
    req = CheckRequest(tenant_id="t", speech_profile_id=perfil, requested_by="ana@x")
    return asyncio.run(run_check(req, deps, pool_id="speech_check", default_language="pt-BR", timing=RAPIDO)), req


class TestRunner:
    def test_verificacao_completa_pelo_endpoint_temporario(self):
        d = _Deps()
        ev, req = _roda(d)
        assert ev["status"] == "completed" and ev["failure_reason"] is None
        assert d.criados == [(f"speech-check-{req.check_id[:12]}", "speech_check", {"speech_profile_id": "sip"})]
        assert ev["phrases_total"] == 7 and ev["phrases_correct"] == 7 and ev["accuracy"] == 1.0
        assert ev["noise_total"] == 1 and ev["hallucinations"] == 0
        assert (ev["profile_in_effect"], ev["stt_model"], ev["discarded_vad"]) == ("sip", "m", 1)
        assert ev["reference_version"] == reference.REFERENCE_VERSION and ev["session_id"] == "sid-1"
        assert d.eventos.index("listener.start") < d.eventos.index(f"open speech-check-{req.check_id[:12]}")
        assert d.eventos[-1] == "delete ep-1"
        assert all(set(i) == {"id", "kind", "transcripts", "confidence", "hallucinated", "correct", "wer"}
                   for i in ev["items"])

    def test_transcricao_vai_para_o_item_da_janela_e_ruido_alucinado_conta(self):
        roteiro = [[(it.text, 0.8)] if it.kind == "phrase" else [("Obrigado.", 0.5)] for it in reference.ITEMS_PT]
        roteiro[3] = [("Segunda via da batura.", 0.45)]
        ev, _ = _roda(_Deps(roteiro=roteiro))
        porid = {i["id"]: i for i in ev["items"]}
        assert porid["p04"]["correct"] is False and porid["p04"]["wer"] == 0.25
        assert porid["n01"]["hallucinated"] is True and ev["hallucinations"] == 1
        assert porid["p05"]["correct"] is True                      # o erro do p04 não vazou

    def test_sem_perfil_nao_manda_settings(self):
        d = _Deps(resumo={"speech_profile_id": None})
        ev, _ = _roda(d, perfil=None)
        assert d.criados[0][2] == {} and ev["speech_profile_id"] is None and ev["status"] == "completed"

    @pytest.mark.parametrize("kw,perfil,razao,abriu_endpoint", [
        ({"perfis": {}}, "sip", "profile_not_found", False),
        ({"perfis": {"en": {"stt_language": "en"}}}, "en", "unsupported_language", False),
        ({"tts_falha": True}, "sip", "tts_unavailable", False),
        ({"listener_falha": True}, "sip", "listener_unavailable", True),
        ({"atende": False}, "sip", "call_not_answered", True),
        ({"resumo": None}, "sip", "summary_missing", True),
    ])
    def test_falha_vira_resultado_nomeado_e_nunca_deixa_endpoint(self, kw, perfil, razao, abriu_endpoint):
        d = _Deps(**kw)
        ev, _ = _roda(d, perfil=perfil)
        assert (ev["status"], ev["failure_reason"]) == ("failed", razao)
        assert ev["accuracy"] is None and ev["items"] == [] and ev["phrases_total"] == 0
        assert ("delete ep-1" in d.eventos) is abriu_endpoint
        assert bool(d.criados) is abriu_endpoint

    def test_perfil_pedido_diferente_do_aplicado_e_dito(self, caplog):
        with caplog.at_level(logging.ERROR):
            ev, _ = _roda(_Deps(resumo={"speech_profile_id": None}))
        assert ev["profile_in_effect"] is None and "o resultado NAO e do perfil" in caplog.text


def _settings(**kw):
    return Settings(tenant_id="t", speech_check_service_token=kw.get("token", "seg"))


def _cliente(deps, token="seg"):
    producer = AsyncMock()
    app = build_app(_settings(token=token), deps_factory=lambda: deps, producer=producer)
    return TestClient(app), producer


class TestServer:
    def test_sem_credencial_configurada_recusa_tudo(self):
        c, _ = _cliente(_Deps(), token="")
        with c:
            assert c.post("/v1/speech-checks", json={"requested_by": "a"}, headers={"x-service-token": ""}).status_code == 503

    def test_credencial_errada_401(self):
        c, _ = _cliente(_Deps())
        with c:
            assert c.post("/v1/speech-checks", json={"requested_by": "a"}, headers={"x-service-token": "x"}).status_code == 401
            assert c.post("/v1/speech-checks", json={"requested_by": "a"}).status_code == 401

    @pytest.mark.parametrize("corpo,codigo", [
        ({"requested_by": "a", "tenant_id": "outro"}, 422),
        ({"requested_by": "a", "speech_profile_id": "Com Espaco"}, 422),
        ({"requested_by": "a", "speech_profile_id": "inexistente"}, 422),
    ])
    def test_recusas_antes_de_abrir_chamada(self, corpo, codigo):
        d = _Deps()
        c, _ = _cliente(d)
        with c:
            r = c.post("/v1/speech-checks", json=corpo, headers={"x-service-token": "seg"})
        assert r.status_code == codigo and d.criados == []

    def test_o_que_corre_agora_e_visivel_e_exige_credencial(self):
        c, _ = _cliente(_Deps())
        with c:
            assert c.get("/v1/speech-checks").status_code == 401
            corpo = c.get("/v1/speech-checks", headers={"x-service-token": "seg"}).json()
        assert corpo == {"running": None, "checks": []}

    def test_aceita_publica_com_chave_da_verificacao_e_recusa_segunda_em_curso(self, monkeypatch):
        from ..speech_check import server as srv
        d = _Deps()
        liberar = asyncio.Event()
        real = srv.run_check

        async def lento(req, deps, **kw):
            await liberar.wait()
            return await real(req, deps, timing=RAPIDO, **kw)
        monkeypatch.setattr(srv, "run_check", lento)
        c, producer = _cliente(d)
        with c:
            r = c.post("/v1/speech-checks", json={"requested_by": "ana", "speech_profile_id": "sip"},
                       headers={"x-service-token": "seg"})
            assert r.status_code == 202
            cid = r.json()["check_id"]
            r2 = c.post("/v1/speech-checks", json={"requested_by": "ana"}, headers={"x-service-token": "seg"})
            assert r2.status_code == 409 and r2.json()["detail"]["check_id"] == cid
            c.portal.call(liberar.set)
            for _ in range(100):
                if c.get(f"/v1/speech-checks/{cid}", headers={"x-service-token": "seg"}).json()["status"] != "running":
                    break
                time.sleep(0.05)
            st = c.get(f"/v1/speech-checks/{cid}", headers={"x-service-token": "seg"}).json()
            assert st["status"] == "completed"
            (chamada,) = producer.send.call_args_list
            assert chamada.args[0] == "speech.metrics" and chamada.kwargs["key"] == cid.encode()
            assert json.loads(chamada.kwargs["value"])["event_type"] == "speech_check_result"
            assert c.post("/v1/speech-checks", json={"requested_by": "ana"},
                          headers={"x-service-token": "seg"}).status_code == 202       # liberou o tenant
