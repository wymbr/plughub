"""
test_supervisor_tenant_guard.py — o guard de tenant do `/supervisor/join`.

POR QUE ESTA POSIÇÃO É A ÚNICA QUE JULGA AUTORIDADE:
`/supervisor/message` e `/supervisor/leave` comparam o `tenant_id` do corpo com o
estado gravado em `supervisor:{sid}:active` — que foi escrito, no join, a partir do
corpo do PRÓPRIO chamador. Eles conferem consistência, não autoridade. O único
confronto contra um fato da plataforma (`session:{id}:meta`) acontece no join, e é
ele que estes testes fixam.

O DEFEITO QUE ELES IMPEDEM DE VOLTAR (2026-08-21):
    meta.get("tenant_id", body.tenant_id) != body.tenant_id
Com o campo ausente, o default É o valor comparado — a igualdade é sempre verdadeira
e o 403 nunca dispara. Escrito assim, parece uma comparação. Nenhum teste existia
nesta posição, e é por isso que sobreviveu.

Alcance medido antes do conserto (`infra/test/probe_session_meta_ownership.sh`):
8 metas vivos, 8 com `tenant_id`, 0 sem, 0 malformados — defeito REAL no código e
LATENTE no dado. 0 em 8 é evidência fraca de "nunca"; daí o fail-closed.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plughub_analytics_api.pool_auth import PoolPrincipal, require_pool_principal
from plughub_analytics_api.supervisor import router

TENANT  = "tenant_a"
OUTRO   = "tenant_b"
SESSION = "sess_20260821T120000_01HX5K3MNJP8QVWZ4RABC"


class _FakeRedis:
    """Só o suficiente para o caminho do join: get/set/xadd."""

    def __init__(self, meta_raw: str | None) -> None:
        self._meta = meta_raw
        self.store: dict[str, str] = {}
        self.sets: dict[str, set] = {}
        self.streams: list[dict] = []

    async def get(self, key: str):
        if key.endswith(":meta"):
            return self._meta
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value

    async def smembers(self, key: str):
        return self.sets.get(key, set())

    async def xadd(self, key: str, fields: dict):
        self.streams.append({"key": key, **fields})
        return "1-0"


def _client(
    meta_raw: str | None,
    principal: PoolPrincipal | None = None,
) -> TestClient:
    """
    Por padrao injeta um principal IRRESTRITO do tenant certo.

    Sem isso, o `require_pool_principal` (2026-08-27) recusaria antes de o guard de
    TENANT ser alcancado, e estes testes passariam a medir a porta errada — verdes
    pelo motivo errado, que e a familia de defeito que este repositorio cataloga.
    O portao de auth tem testes proprios mais abaixo.
    """
    app = FastAPI()
    app.include_router(router)
    app.state.redis = _FakeRedis(meta_raw)
    app.dependency_overrides[require_pool_principal] = lambda: (
        principal or PoolPrincipal(accessible_pools=None, tenant_id=TENANT, sub="sup1")
    )
    return TestClient(app)


def _join(client: TestClient, tenant: str = TENANT):
    return client.post("/supervisor/join", json={
        "tenant_id": tenant, "session_id": SESSION, "operator_id": "op1",
    })


# ── CONTROLE ──────────────────────────────────────────────────────────────────
# Sem esta metade, uma implementação que recusasse TUDO passaria em todos os casos
# de recusa abaixo sem provar nada.

def test_meta_com_tenant_correto_ENTRA():
    r = _join(_client(json.dumps({"tenant_id": TENANT, "channel": "webchat"})))
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == SESSION


# ── O caminho que o guard antigo deixava passar ───────────────────────────────

def test_meta_SEM_tenant_id_e_RECUSADO():
    # Era exatamente aqui que `meta.get("tenant_id", body.tenant_id)` devolvia o
    # valor comparado e a desigualdade nunca era verdadeira.
    r = _join(_client(json.dumps({"channel": "webchat", "contact_id": "c1"})))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "tenant_unverifiable"


def test_meta_MALFORMADO_e_RECUSADO():
    # O `except: meta = {}` produzia a mesma ausência por outro caminho.
    r = _join(_client("{isto não é json"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "tenant_unverifiable"


def test_meta_que_nao_e_OBJETO_e_RECUSADO():
    # JSON válido e inútil: `json.loads("[]")` não levanta, e `.get` explodiria
    # mais adiante — ou, pior, o meta viraria `{}` e cairia no fail-open.
    r = _join(_client("[1, 2, 3]"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "tenant_unverifiable"


# ── A metade que SEMPRE funcionou (e precisa continuar funcionando) ───────────

def test_tenant_DIVERGENTE_e_recusado_com_mismatch():
    r = _join(_client(json.dumps({"tenant_id": OUTRO})), tenant=TENANT)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Tenant mismatch"


def test_sessao_sem_meta_e_404_nao_403():
    # Distinção que importa para quem depura: "não existe" ≠ "não posso conferir".
    r = _join(_client(None))
    assert r.status_code == 404, r.text


# ══════════════════════════════════════════════════════════════════════════════
# O PORTÃO DE AUTORIZAÇÃO (2026-08-27)
# ══════════════════════════════════════════════════════════════════════════════
#
# Até esta data o router não tinha `Depends` NENHUM: qualquer um que alcançasse a
# porta entrava numa conferência de cliente ao vivo e ESCREVIA no stream dela,
# declarando o próprio `tenant_id` no corpo. Os testes acima fixavam o guard de
# TENANT — que confere um fato da plataforma — mas o tenant vinha do CORPO, então
# eles nunca puderam medir autoridade. Esta metade mede.

_META_OK = json.dumps({"tenant_id": TENANT, "pool_id": "pool_x", "channel": "webchat"})


def _client_sem_override(meta_raw: str | None) -> TestClient:
    """Cliente SEM `dependency_overrides` — exerce o `require_pool_principal` de verdade."""
    app = FastAPI()
    app.include_router(router)
    app.state.redis = _FakeRedis(meta_raw)
    return TestClient(app, raise_server_exceptions=False)


def test_sem_token_e_401_nao_200():
    # O defeito em estado puro. Antes: 200 + participant_id, sem credencial alguma.
    r = _client_sem_override(_META_OK).post("/supervisor/join", json={
        "tenant_id": TENANT, "session_id": SESSION,
    })
    assert r.status_code == 401, r.text


# ── ESCOPO DE POOL ────────────────────────────────────────────────────────────
# Decisão do dono (2026-08-26): o admin respeita a ABAC como qualquer um — não há
# bypass por papel. Por isso nenhum teste aqui menciona `roles`.

def _restrito(*pools: str) -> PoolPrincipal:
    return PoolPrincipal(accessible_pools=list(pools), tenant_id=TENANT, sub="sup1")


def test_escopo_que_BATE_entra():
    # CONTROLE do bloco de escopo: sem este 200, os 403 abaixo seriam compatíveis
    # com "recusa tudo", que passaria sem provar nada.
    r = _join(_client(_META_OK, principal=_restrito("pool_x")))
    assert r.status_code == 200, r.text


def test_escopo_que_NAO_bate_e_403():
    r = _join(_client(_META_OK, principal=_restrito("pool_outro")))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "pool_scope_denied"


def test_pool_INDETERMINAVEL_e_403_e_nao_200():
    # meta sem `pool_id` e nenhuma instância nos SETs de agente. "Não sei qual pool"
    # NÃO pode virar "pode entrar": é escrita em conferência de cliente.
    meta = json.dumps({"tenant_id": TENANT, "channel": "webhook"})
    r = _join(_client(meta, principal=_restrito("pool_x")))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "session_pools_undeterminable"


def test_pool_vem_da_INSTANCIA_quando_o_meta_nao_tem():
    # TESTEMUNHA DA UNIÃO. `meta.pool_id` significa coisas diferentes por canal
    # (entrada no webchat/webrtc, AUSENTE no webhook, atendimento depois que o bridge
    # ativa humano). Autorizar só por ele colapsaria a semântica de escopo do
    # analytics, que é "entrou por pool meu OU pool meu atendeu".
    meta   = json.dumps({"tenant_id": TENANT, "channel": "webhook"})
    client = _client(meta, principal=_restrito("pool_da_instancia"))
    redis  = client.app.state.redis
    redis.sets[f"session:{SESSION}:ai_agents"] = {"inst-1"}
    redis.store[f"{TENANT}:instance:inst-1"] = json.dumps(
        {"instance_id": "inst-1", "pools": ["pool_da_instancia"]}
    )
    r = _join(client)
    assert r.status_code == 200, r.text


def test_identidade_gravada_e_o_SUB_do_token_nao_o_corpo():
    # O `operator_id` do corpo tinha default literal "operator" (`hooks.ts`), então a
    # trilha de auditoria do stream registrava uma CONSTANTE, não uma pessoa.
    client = _client(_META_OK)
    r = client.post("/supervisor/join", json={
        "tenant_id": TENANT, "session_id": SESSION, "operator_id": "mentira",
    })
    assert r.status_code == 200, r.text
    joined = [e for e in client.app.state.redis.streams
              if e.get("type") == "participant_joined"]
    assert len(joined) == 1
    assert json.loads(joined[0]["payload"])["operator_id"] == "sup1"
