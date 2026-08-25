"""
test_pool_config_ttl_single_source.py

A PROPOSIÇÃO: o TTL de `{t}:pool_config:{p}` escrito pelo bridge vem do Config
API (namespace `session`, chave `pool_config_ttl_s`), e NÃO de uma constante
deste módulo.

Por que ela importa (medido em 2026-08-25, parando o serviço):
  · o `_heartbeat_tick` re-SETa a chave a cada 15 s, então o bridge é o
    renovador ÚNICO — TTL 3587 → 3546 → 3462 com ele parado (decai, não reseta),
    e de volta a 3594 ao religar;
  · logo o `86 400` que o routing-engine escrevia era sobrescrito 15 segundos
    por vez, e o conserto de `docs/guias/changelog-2026-04-16.md` (300 s →
    86 400) estava desfeito em silêncio desde então;
  · a expiração esvazia `PoolRegistry.get_candidate_pools` e enfileira TODO
    contato. Não é o ledger de SLA — é o apagão de roteamento.

⚠️ NENHUMA asserção aqui é textual. `grep -c "_POOL_CONFIG_TTL_S"` no fonte
devolve 1 mesmo depois da remoção, porque o comentário que a documenta escreve
o nome de novo. Testa-se o MÓDULO CARREGADO (`hasattr`) e a CHAMADA feita ao
Redis (`call_args`), não o arquivo.

O valor distintivo `4242` não é enfeite: é a população discriminante. Um teste
que usasse 86 400 passaria idêntico sobre o código velho, onde a constante
também valia 86 400 depois da mudança de valor — seria o gate sem população que
este repositório já pagou duas vezes.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock

from plughub_orchestrator_bridge import instance_bootstrap as ib
from plughub_orchestrator_bridge.instance_bootstrap import (
    InstanceBootstrap,
    ReconciliationReport,
)
from plughub_orchestrator_bridge.session_config import session_config

TENANT = "tenant_demo"
POOL   = "retencao_humano"

# TTL que NENHUM default do repositório produz — nem o 3 600 antigo, nem o
# 86 400 novo. Se um `ex=` sair diferente disto, ele não veio da config.
DISTINCTIVE_TTL = 4242


@pytest.fixture
def config_ttl():
    """Injeta `pool_config_ttl_s` no cache e restaura no fim."""
    previous = dict(session_config._data)
    session_config._data = {**previous, "pool_config_ttl_s": DISTINCTIVE_TTL}
    yield DISTINCTIVE_TTL
    session_config._data = previous


def make_bootstrap(redis_mock) -> InstanceBootstrap:
    return InstanceBootstrap(
        redis=redis_mock,
        registry_url="http://agent-registry:3200",
        tenant_ids=[TENANT],
    )


# ─── A constante não voltou ───────────────────────────────────────────────────

def test_the_module_no_longer_carries_its_own_ttl_constant():
    """Estrutural sobre o módulo CARREGADO — o comentário que cita o nome antigo
    não conta aqui, e é por isso que a asserção não é um grep."""
    assert not hasattr(ib, "_POOL_CONFIG_TTL_S"), (
        "_POOL_CONFIG_TTL_S ressuscitou. Ela era o segundo escritor do TTL e "
        "vencia o routing-engine a cada 15 s — ver o cabeçalho deste arquivo."
    )


def test_the_local_fallback_mirrors_the_seed():
    """O fallback existe para o Config API inalcançável, e espelhar o seed é
    contrato: divergir aqui recria a discordância por outro caminho."""
    assert ib._POOL_CONFIG_TTL_FALLBACK_S == 86_400


# ─── O valor vem da config ────────────────────────────────────────────────────

def test_ttl_comes_from_the_config_api(config_ttl):
    assert ib._pool_config_ttl_s() == DISTINCTIVE_TTL


def test_ttl_falls_back_when_the_key_is_absent():
    previous = dict(session_config._data)
    session_config._data = {k: v for k, v in previous.items()
                            if k != "pool_config_ttl_s"}
    try:
        # Sem a chave no cache, cai no _DEFAULTS do session_config, que também
        # espelha o seed. Ausência NUNCA vira zero: `ex=0` é rejeitado pelo
        # Redis e derrubaria a escrita inteira.
        assert ib._pool_config_ttl_s() == 86_400
    finally:
        session_config._data = previous


def test_a_non_numeric_config_value_degrades_LOUDLY(caplog):
    """Config corrompida não pode derrubar a renovação nem passar calada."""
    previous = dict(session_config._data)
    session_config._data = {**previous, "pool_config_ttl_s": "muito"}
    try:
        with caplog.at_level("WARNING"):
            assert ib._pool_config_ttl_s() == 86_400
        assert any("pool_config_ttl_s" in r.getMessage() for r in caplog.records), (
            "degradou em silêncio — o fallback não disse por que caiu"
        )
    finally:
        session_config._data = previous


# ─── O reload precisa do tenant, senão nada disto vale ────────────────────────

@pytest.mark.asyncio
async def test_reload_sends_tenant_id():
    """⚠️ Este é o teste que impede a fatia inteira de funcionar por acidente.

    `GET /config/{namespace}` tem `tenant_id` como Query OBRIGATÓRIO
    (`config-api/router.py:174`). Sem ele: 422 → ramo de warning → o cache fica
    nos `_DEFAULTS` para sempre. O TTL viraria 86 400 (que é o que se queria),
    mas pelo motivo errado — a chave continuaria órfã e a tela seguiria
    prometendo efeito que não tem.

    O routing-engine teve exatamente este defeito e o consertou em 2026-06-05;
    aqui ele sobreviveu porque ficar no default quase sempre parece certo.
    """
    captured: dict = {}

    class _Resp:
        status = 200
        async def json(self):
            return {"entries": {}}
        async def __aenter__(self):  return self
        async def __aexit__(self, *a):  return False

    class _Http:
        def get(self, url, **kwargs):
            captured["url"]    = url
            captured["params"] = kwargs.get("params")
            return _Resp()

    from plughub_orchestrator_bridge.session_config import SessionConfigCache
    cache = SessionConfigCache()
    cache.configure_tenant("tenant_demo")
    await cache.reload("http://config-api:3600", _Http())

    assert captured["url"].endswith("/config/session")
    assert captured["params"] == {"tenant_id": "tenant_demo"}, (
        "o reload voltou a sair sem tenant_id — 422 e cache preso nos defaults"
    )


@pytest.mark.asyncio
async def test_a_failed_reload_names_the_keys_that_lose_effect(caplog):
    """A terceira causa empilhada (env ausente + porta errada + sem tenant) só
    passou meses despercebida porque o aviso dizia "using cached/default values"
    sem dizer O QUE deixava de valer. Agora nomeia as chaves."""
    from plughub_orchestrator_bridge.session_config import SessionConfigCache

    class _Boom:
        def get(self, *a, **k):
            raise OSError("Cannot connect to host config-api:3600")

    cache = SessionConfigCache()
    with caplog.at_level("WARNING"):
        await cache.reload("http://config-api:3600", _Boom())

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "pool_config_ttl_s" in msgs, "o aviso não nomeia as chaves afetadas"
    assert cache.is_stale


# ─── A escrita real usa esse valor ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heartbeat_renews_with_the_configured_ttl(config_ttl):
    redis = AsyncMock()
    boot  = make_bootstrap(redis)
    boot._pool_configs = {TENANT: {POOL: {"pool_id": POOL}}}
    boot._registered   = {}

    await boot._heartbeat_tick()

    writes = [c for c in redis.set.call_args_list
              if c.args and c.args[0] == f"{TENANT}:pool_config:{POOL}"]
    assert len(writes) == 1, "o heartbeat deixou de renovar o pool_config"
    assert writes[0].kwargs.get("ex") == DISTINCTIVE_TTL


@pytest.mark.asyncio
async def test_reconcile_writes_a_new_key_with_the_configured_ttl(config_ttl):
    redis = AsyncMock()
    redis.get.return_value  = None       # chave inexistente → SET
    redis.scan.return_value = (0, [])    # seção B (orphans) sem nada a varrer
    boot  = make_bootstrap(redis)

    report = ReconciliationReport(tenant_id=TENANT)
    await boot._reconcile_pool_configs(
        AsyncMock(), TENANT, [{"pool_id": POOL}], report, dry_run=False,
    )

    assert report.errors == [], f"a reconciliação registrou erro: {report.errors}"
    writes = [c for c in redis.set.call_args_list
              if c.args and c.args[0] == f"{TENANT}:pool_config:{POOL}"]
    assert len(writes) == 1
    assert writes[0].kwargs.get("ex") == DISTINCTIVE_TTL


@pytest.mark.asyncio
async def test_reconcile_renews_an_identical_key_with_the_configured_ttl(config_ttl):
    """O ramo do EXPIRE (conteúdo idêntico) é o mais fácil de esquecer numa
    migração — e é o que roda em toda reconciliação de pool que não mudou."""
    redis = AsyncMock()
    redis.get.return_value  = json.dumps({"pool_id": POOL})
    redis.scan.return_value = (0, [])
    boot  = make_bootstrap(redis)

    report = ReconciliationReport(tenant_id=TENANT)
    await boot._reconcile_pool_configs(
        AsyncMock(), TENANT, [{"pool_id": POOL}], report, dry_run=False,
    )

    assert report.errors == [], f"a reconciliação registrou erro: {report.errors}"
    expires = [c for c in redis.expire.call_args_list
               if c.args and c.args[0] == f"{TENANT}:pool_config:{POOL}"]
    assert len(expires) == 1, "o ramo de conteúdo idêntico parou de renovar"
    assert expires[0].args[1] == DISTINCTIVE_TTL
