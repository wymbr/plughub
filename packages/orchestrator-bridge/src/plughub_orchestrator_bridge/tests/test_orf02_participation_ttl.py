# -*- coding: utf-8 -*-
"""ORF-02 mecanismo 1 — a janela de PARTICIPACAO dura o que dura o ITEM.

Medido em 2026-09-12 (`59485f70`): as chaves que fecham o segmento do claimante
nasciam com o TTL da SESSAO (4 h) e o item de wrap-up vive ate 25 h. Claim as
18:52, submit 17h30 depois: chaves expiradas, `participant_left` NUNCA publicado,
segmento aberto para sempre no analytics -- enquanto o stream da sessao tinha o
par certo (a perda era na traducao do bridge).

⚠️ O caso que decide NAO e "usa o prazo": e o **contato sem item parqueado**, que
tem de continuar com o TTL da sessao. Um helper que esticasse tudo trocaria um
defeito por chaves imortais em toda sessao do tenant.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from plughub_orchestrator_bridge.main import _participation_ttl_s, _stl


class RedisFake:
    def __init__(self, chaves=None, erro=False):
        self.chaves = chaves or {}
        self.erro = erro

    async def get(self, k):
        if self.erro:
            raise RuntimeError("redis fora")
        return self.chaves.get(k)


def _ledger(horas: float) -> str:
    prazo = datetime.now(timezone.utc) + timedelta(hours=horas)
    return json.dumps({"pool_id": "retencao_humano-int", "deadline": prazo.isoformat()})


CHAVE = "tenant_demo:work_task:sess-1"


@pytest.mark.asyncio
async def test_sem_item_parqueado_nada_muda():
    # O contato normal e a maioria: esticar o TTL dele seria trocar um defeito por
    # chaves imortais em toda sessao.
    ttl = await _participation_ttl_s(RedisFake({}), "tenant_demo", "sess-1")
    assert ttl == _stl()


@pytest.mark.asyncio
async def test_item_de_24h_estica_a_janela_alem_do_TTL_da_sessao():
    ttl = await _participation_ttl_s(RedisFake({CHAVE: _ledger(24)}), "tenant_demo", "sess-1")
    # 24 h de prazo + 1 h de folga, com tolerancia para o relogio do teste.
    assert 89_000 < ttl <= 90_100
    assert ttl > _stl()


@pytest.mark.asyncio
async def test_o_caso_MEDIDO_sobrevive_ao_intervalo_que_quebrou():
    # `59485f70`: 17h30 entre o claim e o submit. Com o TTL antigo (4 h) as chaves
    # ja tinham morrido; com o novo, a janela cobre o intervalo.
    ttl = await _participation_ttl_s(RedisFake({CHAVE: _ledger(24)}), "tenant_demo", "sess-1")
    assert ttl > 17.5 * 3600


@pytest.mark.asyncio
async def test_item_ja_vencido_nunca_encolhe_abaixo_do_TTL_da_sessao():
    # Prazo no passado daria negativo; o piso e o comportamento anterior.
    ttl = await _participation_ttl_s(RedisFake({CHAVE: _ledger(-5)}), "tenant_demo", "sess-1")
    assert ttl == _stl()


@pytest.mark.asyncio
async def test_prazo_ABSURDO_bate_no_teto():
    # `deadline` vem de dado gravado: corrompido, nao pode virar chave imortal.
    ttl = await _participation_ttl_s(RedisFake({CHAVE: _ledger(24 * 365)}), "tenant_demo", "sess-1")
    assert ttl == 97_200


@pytest.mark.asyncio
async def test_ledger_sem_deadline_cai_no_TTL_da_sessao():
    r = RedisFake({CHAVE: json.dumps({"pool_id": "retencao_humano-int"})})
    assert await _participation_ttl_s(r, "tenant_demo", "sess-1") == _stl()


@pytest.mark.asyncio
async def test_falha_de_leitura_degrada_BARULHENTO(caplog):
    ttl = await _participation_ttl_s(RedisFake(erro=True), "tenant_demo", "sess-1")
    assert ttl == _stl()
    # Sem log, o sintoma (segmento aberto) aparece horas depois e longe daqui.
    assert any("ORF-02" in reg.getMessage() for reg in caplog.records)


@pytest.mark.asyncio
async def test_json_corrompido_nao_estoura_a_ativacao():
    r = RedisFake({CHAVE: "{nao é json"})
    assert await _participation_ttl_s(r, "tenant_demo", "sess-1") == _stl()
