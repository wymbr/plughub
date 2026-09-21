"""
VOZ-41 — número `voice` cadastrado sem tronco SIP no SFU tem de aparecer no log, e "não sei" nunca
vira "coberto".

Medido em 2026-09-21: o SFU subiu sem tronco (o seed viu o diretório vazio e saiu 0), e o serviço
SIP passou a descartar toda chamada como `flood` — sem 4xx e sem uma linha no gateway.
"""
from __future__ import annotations

import ast
import logging
import pathlib

import pytest

import plughub_channel_gateway.main as gw_main
from plughub_channel_gateway import sip_trunk_watch as w
from plughub_channel_gateway.adapters.webrtc_provider import MockWebRTCProvider

NUM = "+551130000001"


def _watch(numbers, trunks=None, trunks_error=None, numbers_error=None):
    prov = MockWebRTCProvider()
    prov.inbound_trunks = trunks or []
    prov.inbound_trunks_error = trunks_error
    state = {"numbers": numbers, "err": numbers_error}

    async def _numbers():
        if state["err"] is not None:
            raise state["err"]
        return state["numbers"]

    return w.SipTrunkWatch(prov.list_inbound_trunks, _numbers), prov, state


def _msgs(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level and "sip-trunk-watch" in r.getMessage()]


# ── cobertura pura ────────────────────────────────────────────────────────────

def test_cobertura_por_numero_e_por_tronco_curinga():
    assert w.coverage([NUM], [("t", [NUM])]).ok
    assert w.coverage([NUM], [("t", ["+5500"])]).uncovered == (NUM,)
    assert w.coverage([NUM], []).uncovered == (NUM,)          # o caso medido: nenhum tronco
    assert w.coverage([NUM, "+5511"], [("x", ["+5500"]), ("curinga", [])]).ok   # numbers vazio aceita tudo


# ── o defeito medido ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sem_tronco_e_ERROR_nomeando_o_numero(caplog):
    watch, _, _ = _watch([NUM], trunks=[])
    with caplog.at_level(logging.INFO, logger=w.logger.name):
        cov = await watch.tick()
    assert cov is not None and cov.uncovered == (NUM,)
    [erro] = _msgs(caplog, logging.ERROR)
    assert NUM in erro and "sip-seed" in erro


@pytest.mark.asyncio
async def test_controle_coberto_nao_acusa(caplog):
    """Controle positivo: com o tronco, zero ERROR — senão o teste acima passaria com qualquer log."""
    watch, _, _ = _watch([NUM], trunks=[("inbound_demo", [NUM])])
    with caplog.at_level(logging.INFO, logger=w.logger.name):
        cov = await watch.tick()
    assert cov is not None and cov.ok
    assert _msgs(caplog, logging.ERROR) == []
    assert len(_msgs(caplog, logging.INFO)) == 1


@pytest.mark.asyncio
async def test_error_nao_inunda_e_repete_de_hora_em_hora(caplog):
    watch, _, _ = _watch([NUM], trunks=[])
    with caplog.at_level(logging.INFO, logger=w.logger.name):
        for _ in range(w.REPEAT_ERROR_EVERY):
            await watch.tick()
        assert len(_msgs(caplog, logging.ERROR)) == 1
        await watch.tick()
    assert len(_msgs(caplog, logging.ERROR)) == 2


@pytest.mark.asyncio
async def test_volta_do_tronco_e_dita(caplog):
    watch, prov, _ = _watch([NUM], trunks=[])
    with caplog.at_level(logging.INFO, logger=w.logger.name):
        await watch.tick()
        prov.inbound_trunks = [("inbound_demo", [NUM])]
        await watch.tick()
        await watch.tick()                       # estável: não repete
    infos = _msgs(caplog, logging.INFO)
    assert len(infos) == 1 and "restaurado" in infos[0]


# ── "não sei" tem cara própria ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sfu_fora_e_WARNING_nao_conferido_nunca_coberto(caplog):
    watch, _, _ = _watch([NUM], trunks_error=ConnectionError("livekit:7880 recusou"))
    with caplog.at_level(logging.INFO, logger=w.logger.name):
        assert await watch.tick() is None
    [aviso] = _msgs(caplog, logging.WARNING)
    assert "NÃO conferida" in aviso and "livekit:7880" in aviso
    assert _msgs(caplog, logging.INFO) == [] and _msgs(caplog, logging.ERROR) == []


@pytest.mark.asyncio
async def test_registro_fora_e_WARNING(caplog):
    watch, _, _ = _watch([], numbers_error=RuntimeError("registry 503"))
    with caplog.at_level(logging.INFO, logger=w.logger.name):
        assert await watch.tick() is None
    assert any("registro de endpoints" in m for m in _msgs(caplog, logging.WARNING))


@pytest.mark.asyncio
async def test_sem_numero_voice_nao_pergunta_ao_sfu(caplog):
    """Sem número cadastrado não há o que cobrir — e o SFU nem é consultado (instalação sem SIP)."""
    watch, _, _ = _watch([], trunks_error=AssertionError("não devia ter perguntado"))
    with caplog.at_level(logging.INFO, logger=w.logger.name):
        cov = await watch.tick()
    assert cov is not None and cov.ok and _msgs(caplog, logging.WARNING) == []


@pytest.mark.asyncio
async def test_provider_ausente_e_nao_conferido(caplog):
    """Canal WebRTC fechado (sem provider): nem "coberto" nem task morta — WARNING dizendo por quê."""
    async def _nums():
        return [NUM]
    watch = w.SipTrunkWatch(w.trunk_lister(lambda: None), _nums)
    with caplog.at_level(logging.INFO, logger=w.logger.name):
        assert await watch.tick() is None
    assert any("provider do SFU" in m for m in _msgs(caplog, logging.WARNING))


# ── o provider e a fiação ─────────────────────────────────────────────────────

def test_livekit_provider_implementa_a_listagem():
    """O mock CRIA o alvo: confira que o provider real também o tem."""
    from plughub_channel_gateway.adapters.webrtc_provider import LiveKitProvider
    assert hasattr(LiveKitProvider, "list_inbound_trunks")
    src = pathlib.Path(__import__("inspect").getsourcefile(LiveKitProvider)).read_text(encoding="utf-8")
    assert "sip.list_inbound_trunk(" in src      # o nome não-deprecado do SDK


def test_a_task_e_ligada_no_lifespan_e_cancelada():
    tree = ast.parse(pathlib.Path(gw_main.__file__).read_text(encoding="utf-8"))
    chamadas = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "run_sip_trunk_watch"]
    cancel = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "cancel"
              and getattr(n.func.value, "id", None) == "sip_trunk_watch_task"]
    assert len(chamadas) == 1 and len(cancel) == 1
