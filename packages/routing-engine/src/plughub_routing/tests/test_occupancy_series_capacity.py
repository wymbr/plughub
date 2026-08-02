"""
test_occupancy_series_capacity.py — F4c: o defeito C na SÉRIE (achado 2).

**O defeito.** `pool_occupancy_peaks` guardava, na linha agregada `__total__`, a soma
das capacidades POR POOL. Um humano `max_concurrent 3` logado em dois pools publicava
3 + 3 = 6 como "capacidade do tenant" — para 3 vagas. A tela foi corrigida na F4a/F4b,
mas o HISTÓRICO seguia inflado, e histórico inflado é pior que tela inflada: vira base
de dimensionamento meses depois, quando ninguém lembra do viés.

**A correção mantém a linha do pool.** Ela está certa — aquele pool alcança mesmo 3
vagas — e é não-aditiva, exatamente como na superfície viva. O que muda é o agregado:
`__total__` passa a usar a capacidade DEDUPLICADA, e entram linhas
`__capacity_{kind}__` com a capacidade por tipo de licença (o número de planejamento,
que não é derivável do `__total__` porque humano e IA não se substituem).

**Como estes testes reprovam.** O primeiro compara o publicado com a soma ingênua: com
o código anterior, `__total__` traz 6 onde o recurso tem 3. Os demais prendem a
ausência de linha por tipo, o carimbo de capacidade no instante do pico, e o fallback
barulhento quando não há rollup.

Testes de unidade sobre `_flush_occupancy` — sem Redis nem Kafka reais (produtor e
cliente falsos). O que se julga é a MENSAGEM publicada, que é o contrato da série.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from plughub_routing.main import _flush_occupancy


class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_and_wait(self, _topic: str, value: dict) -> None:
        self.sent.append(value)


class _FakeRedis:
    """Só precisa responder ao que o caminho agregado do item 7b consulta."""
    async def scan_iter(self, *_a, **_k):
        return
        yield  # pragma: no cover — generator vazio

    async def get(self, _key):
        return None

    async def sunion(self, *_a, **_k):
        return set()


TENANT = "t_serie"
MINUTE = datetime(2026, 8, 2, 17, 30, tzinfo=timezone.utc)
# A linha de base: 1 humano de 3 vagas em 2 pools. Cada linha de pool diz 3 (correto);
# a soma ingênua diz 6 (o defeito).
POOL_CAPS = {(TENANT, "retencao_humano"): 3, (TENANT, "retencao_humano-int"): 3}
PEAKS     = {(TENANT, "retencao_humano"): 1, (TENANT, "retencao_humano-int"): 0}


async def _flush(**kw) -> list[dict]:
    prod = _FakeProducer()
    await _flush_occupancy(
        _FakeRedis(), prod, MINUTE,
        kw.pop("peaks", PEAKS),
        kw.pop("total_peaks", {TENANT: 1}),
        caps=kw.pop("caps", POOL_CAPS),
        **kw,
    )
    return prod.sent


def _row(rows: list[dict], pool_id: str) -> dict | None:
    for r in rows:
        if r["pool_id"] == pool_id:
            return r
    return None


# ── 1. O defeito medido ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_total_row_uses_deduplicated_capacity_not_the_sum_of_pools():
    """`__total__` publica a capacidade do RECURSO, não a soma das linhas.

    Com o código anterior à F4c o valor era 3 + 3 = 6 — um recurso de 3 vagas contado
    uma vez por pool, gravado para sempre no histórico.
    """
    rows = await _flush(
        kind_peaks={(TENANT, "human"): 1},
        kind_caps={(TENANT, "human"): 3},
    )
    total = _row(rows, "__total__")
    assert total is not None, "a linha agregada sumiu"
    assert total["provisioned_capacity"] == 3, (
        f"__total__ publicou capacidade {total['provisioned_capacity']}: é a soma das "
        f"linhas por pool ({sum(POOL_CAPS.values())}), contando o mesmo recurso duas vezes"
    )


@pytest.mark.asyncio
async def test_per_pool_rows_keep_the_pool_capacity():
    """A linha do POOL não muda — ela está certa e é não-aditiva.

    Rebaixá-la para uma fatia do recurso inventaria frações de vaga e quebraria a
    invariante `peak <= capacity` na própria linha.
    """
    rows = await _flush(
        kind_peaks={(TENANT, "human"): 1},
        kind_caps={(TENANT, "human"): 3},
    )
    for pool_id, cap in ((k[1], v) for k, v in POOL_CAPS.items()):
        r = _row(rows, pool_id)
        assert r is not None and r["provisioned_capacity"] == cap, (
            f"{pool_id}: capacidade da linha do pool mudou — deveria seguir {cap}"
        )
        assert r["peak_concurrency"] <= r["provisioned_capacity"]


# ── 2. As moedas separadas no histórico ───────────────────────────────────────

@pytest.mark.asyncio
async def test_capacity_rows_are_emitted_per_license_kind():
    """Uma linha por tipo, com a capacidade deduplicada daquele tipo.

    É o número de planejamento. Não é derivável do `__total__`: somar humano com IA
    responde "há 356 vagas" para quem precisa saber se há atendente humano.
    """
    rows = await _flush(
        kind_peaks={(TENANT, "human"): 1, (TENANT, "ai"): 12},
        kind_caps={(TENANT, "human"): 3, (TENANT, "ai"): 353},
    )
    human = _row(rows, "__capacity_human__")
    ai    = _row(rows, "__capacity_ai__")
    assert human is not None and ai is not None, (
        f"faltam linhas por tipo na série: {[r['pool_id'] for r in rows]}"
    )
    assert (human["provisioned_capacity"], human["peak_concurrency"]) == (3, 1)
    assert (ai["provisioned_capacity"],    ai["peak_concurrency"])    == (353, 12)
    # E o `__total__` soma os BALDES (cada instância está em exatamente um), não os pools.
    assert _row(rows, "__total__")["provisioned_capacity"] == 356


@pytest.mark.asyncio
async def test_unknown_kind_gets_its_own_row_instead_of_being_folded():
    """Config contraditória vira `__capacity_unknown__` no histórico também.

    Dobrar em human/ai escolheria a moeda em silêncio — e no histórico o erro
    sobrevive a quem o cometeu.
    """
    rows = await _flush(
        kind_peaks={(TENANT, "unknown"): 2},
        kind_caps={(TENANT, "unknown"): 5},
    )
    unk = _row(rows, "__capacity_unknown__")
    assert unk is not None and unk["provisioned_capacity"] == 5
    assert _row(rows, "__capacity_human__") is None


# ── 3. Degradação barulhenta ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_without_rollup_total_falls_back_to_the_sum_and_says_so(caplog):
    """Sem rollup por tipo, `__total__` volta ao `Σ` por pool — INFLADO — e loga.

    Escolha deliberada: buraco mudo na série é pior que valor conhecido-ruim. O log é o
    que permite descobrir depois quais minutos não são confiáveis.
    """
    import logging
    with caplog.at_level(logging.INFO, logger="plughub.routing"):
        rows = await _flush(kind_peaks={}, kind_caps={})
    total = _row(rows, "__total__")
    assert total["provisioned_capacity"] == sum(POOL_CAPS.values()), (
        "sem rollup o fallback deveria ser a soma por pool"
    )
    # `getMessage()`, não `.message`: este último só existe depois que um formatter
    # roda, e sem ele a asserção passaria por AttributeError silenciado no `any`.
    assert any("INFLA" in r.getMessage() for r in caplog.records), (
        "o fallback inflado passou em SILÊNCIO — ninguém saberá que aquele minuto mente"
    )
    assert not [r for r in rows if r["pool_id"].startswith("__capacity_")], (
        "linhas por tipo inventadas sem rollup"
    )


@pytest.mark.asyncio
async def test_kind_capacity_is_the_one_at_the_peak_instant():
    """Mesma lição do achado 1, agora por tipo: capacidade e pico do MESMO instante.

    O flusher carimba `kind_caps` quando o pico AVANÇA. Aqui a asserção é sobre o
    contrato da mensagem: a capacidade publicada é a que veio junto com o pico, não uma
    lida no momento do flush — senão `peak > capacity` reaparece por skew temporal.
    """
    rows = await _flush(
        kind_peaks={(TENANT, "human"): 3},
        kind_caps={(TENANT, "human"): 3},   # capacidade do instante do pico
    )
    human = _row(rows, "__capacity_human__")
    assert human["peak_concurrency"] <= human["provisioned_capacity"], (
        f"peak {human['peak_concurrency']} > capacity {human['provisioned_capacity']}: "
        "impossível por construção — as duas grandezas vieram de instantes diferentes"
    )
