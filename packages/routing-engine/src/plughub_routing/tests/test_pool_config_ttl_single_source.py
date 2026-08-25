"""
test_pool_config_ttl_single_source.py

A PROPOSIÇÃO: `PoolRegistry.save_pool_config` toma o TTL de
`{t}:pool_config:{p}` do Config API (namespace `session`), e NÃO do env
`PLUGHUB_POOL_CONFIG_TTL_SECONDS` que vivia em `Settings`.

Contexto (medido em 2026-08-25 parando o orchestrator-bridge): a chave tem DOIS
escritores — este e o `_heartbeat_tick` do bridge, que a re-SETa a cada 15 s.
Enquanto cada lado carregava o próprio número, o 86 400 daqui era sobrescrito
por 3 600 quinze segundos por vez, e o conserto de
`docs/guias/changelog-2026-04-16.md` estava desfeito sem nada ficar vermelho.
A prova de que o renovador é único: com o bridge parado o TTL decai
(3587 → 3546 → 3462) e NÃO reseta; ao religar, volta a 3594.

⚠️ O `4242` é a população discriminante, não enfeite. Se o teste usasse 86 400
— o valor que a config e o env antigo TÊM — ele passaria idêntico sobre o
código velho. É o mesmo defeito do gate com `discord = 0` que esta linha de
trabalho já pegou duas vezes: um teste que não contém o caso em que os dois
lados diferem não julga nada.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock

from plughub_routing import routing_config as rc_mod
from plughub_routing.config import Settings
from plughub_routing.registry import PoolRegistry
from plughub_routing.routing_config import (
    RoutingConfigCache,
    pool_config_ttl_s,
    routing_config,
    session_config,
)

DISTINCTIVE_TTL = 4242


@pytest.fixture
def config_ttl():
    previous = dict(session_config._data)
    session_config._data = {**previous, "pool_config_ttl_s": DISTINCTIVE_TTL}
    yield DISTINCTIVE_TTL
    session_config._data = previous


# ─── O env não decide mais ────────────────────────────────────────────────────

def test_settings_no_longer_carries_the_pool_config_ttl():
    """Estrutural sobre a CLASSE carregada — `grep` contaria o comentário que
    documenta a remoção, que escreve o nome do campo de novo."""
    assert "pool_config_ttl_seconds" not in Settings.model_fields, (
        "o campo voltou. Ele prometia efeito e não tinha: o bridge sobrescrevia "
        "o TTL a cada 15 s. Ver o cabeçalho deste arquivo."
    )


def test_an_unknown_env_var_does_not_break_settings(monkeypatch):
    """`PLUGHUB_POOL_CONFIG_TTL_SECONDS` segue definida em ambientes reais
    (`ecosystem.config.js`). Ela tem de ser IGNORADA, nunca fatal."""
    monkeypatch.setenv("PLUGHUB_POOL_CONFIG_TTL_SECONDS", "999")
    s = Settings()
    assert not hasattr(s, "pool_config_ttl_seconds")


# ─── A fonte única ────────────────────────────────────────────────────────────

def test_ttl_comes_from_the_session_namespace(config_ttl):
    assert pool_config_ttl_s() == DISTINCTIVE_TTL


def test_the_local_default_mirrors_the_seed():
    assert rc_mod._SESSION_DEFAULTS["pool_config_ttl_s"] == 86_400


def test_the_two_caches_read_different_namespaces():
    """Se o singleton de sessão apontasse para `routing`, ele leria um namespace
    onde a chave não existe e cairia no default para sempre — funcionando por
    acidente e imune a qualquer mudança na tela."""
    assert routing_config._namespace == "routing"
    assert session_config._namespace == "session"


@pytest.mark.asyncio
async def test_reload_fetches_the_namespace_of_the_instance():
    """A URL é derivada do namespace. Sem isto, a segunda cache buscaria
    `/config/routing` e o teste acima passaria mesmo assim."""
    cache = RoutingConfigCache(namespace="session", defaults={})
    http  = AsyncMock()
    resp  = AsyncMock()
    resp.json = lambda: {"entries": {"pool_config_ttl_s": {"value": 7}}}
    resp.raise_for_status = lambda: None
    http.get.return_value = resp

    await cache.reload("http://config-api:3600", http)

    assert http.get.call_args.args[0].endswith("/config/session")
    assert cache.get("pool_config_ttl_s") == 7


# ─── A escrita real usa esse valor ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_pool_config_writes_with_the_configured_ttl(config_ttl):
    redis    = AsyncMock()
    registry = PoolRegistry(redis)

    from plughub_routing.models import PoolConfig
    cfg = PoolConfig(
        tenant_id="tenant_demo",
        pool_id="retencao_humano",
        channel_types=["webchat"],
        sla_target_ms=300_000,
    )
    await registry.save_pool_config(cfg)

    writes = [c for c in redis.set.call_args_list
              if c.args and "pool_config" in str(c.args[0])]
    assert len(writes) == 1, "save_pool_config deixou de escrever a chave"
    assert writes[0].kwargs.get("ex") == DISTINCTIVE_TTL, (
        "o TTL não veio do Config API — provável volta do "
        "`self._settings.pool_config_ttl_seconds`"
    )
    # A chave continua sendo a mesma que o bridge escreve — se este nome mudar,
    # os dois escritores param de colidir e o problema vira outro.
    assert writes[0].args[0] == "tenant_demo:pool_config:retencao_humano"
    assert json.loads(writes[0].args[1])["pool_id"] == "retencao_humano"


# ─── O ramo de invalidação ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_config_changed_invalidates_the_session_cache():
    """Sem este ramo, mudar o TTL na tela valeria no bridge e não aqui — a
    divergência de volta, por outro caminho."""
    from plughub_routing.kafka_listener import ConfigChangedHandler

    handler = ConfigChangedHandler("http://config-api:3600", AsyncMock())
    session_config._invalidated = False
    await handler.handle({"namespace": "session", "key": "pool_config_ttl_s"})
    assert session_config.is_stale


@pytest.mark.asyncio
async def test_the_session_branch_reads_the_module_singleton():
    """Espelha `test_routing_namespace_triggers_invalidation` para o ramo novo.

    Sem isto, o ramo `session` poderia congelar o binding no import (dict de
    módulo ou atributo de classe) e ficar imune a `patch` — que foi exatamente
    o vermelho de 2026-08-25. O ramo `routing` tem guarda; o novo precisa da
    mesma, senão a regressão volta pela metade não coberta.
    """
    from unittest.mock import MagicMock, patch
    from plughub_routing.kafka_listener import ConfigChangedHandler

    handler = ConfigChangedHandler("http://config-api:3600", AsyncMock())
    with patch("plughub_routing.kafka_listener.session_config") as mock_cache:
        mock_cache.invalidate = MagicMock()
        mock_cache.reload     = AsyncMock()
        tasks = []
        with patch("asyncio.create_task", side_effect=lambda c: tasks.append(c)):
            await handler.handle({"namespace": "session",
                                  "key": "pool_config_ttl_s"})
        mock_cache.invalidate.assert_called_once()
        assert len(tasks) == 1


@pytest.mark.asyncio
async def test_config_changed_still_ignores_foreign_namespaces():
    from plughub_routing.kafka_listener import ConfigChangedHandler

    handler = ConfigChangedHandler("http://config-api:3600", AsyncMock())
    routing_config._invalidated = False
    session_config._invalidated = False
    await handler.handle({"namespace": "masking", "key": "whatever"})
    assert not routing_config.is_stale
    assert not session_config.is_stale
