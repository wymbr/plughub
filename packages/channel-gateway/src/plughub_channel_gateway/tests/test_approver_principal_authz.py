"""
test_approver_principal_authz.py — o portão A5/E2 do resume interno
(`main._resolve_approver_principal`), único consumidor dos verificadores migrados.

POR QUE ESTE ARQUIVO NASCEU NO PASSO 3
======================================
A migração do `channel-gateway` para `plughub_authz` (2026-08-28) trocou três funções
sob uma decisão de AUTORIZAÇÃO — `verify_user_jwt`, `abac_can`, `bearer_from_header` —
e a medição prévia devolveu **zero testes** tocando qualquer uma delas ou o call site.
Aceitar o verde das 680 outras seria aceitar um verde que não podia ficar vermelho:
nenhuma delas exercita esta função.

O que o arquivo pina é o VEREDICTO, não a implementação. Ele tem de continuar verde
depois do passo 3 do plano de `accessible_pools` (a inversão de `[]`) para os casos que
declaram escopo, e os dois casos que dependem do ramo legado estão marcados como tais.

TRÊS CLASSES DE SAÍDA, e a do meio é a que ninguém lembra
========================================================
  1. `None`  — caminho EXTERNO/sistema (claimed). Sem header, ou sem segredo wirado.
  2. dict    — humano verificado (possessed).
  3. 403     — sei quem é (ou não consigo saber) e a decisão não pode ser atribuída.

A classe 2 tem DOIS regimes, e confundi-los quebra o wrap-up: com `required_abac=None`
(form-fill genérico) o portão **não exige grant nenhum** — quem autoriza é o binding do
claim, conferido depois, no `handle_resume`. Endurecer isto "por simetria" faria todo
agente de wrap-up tomar 403. Por isso a testemunha `test_form_fill_generico_*` existe.
"""
from __future__ import annotations

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway.main import WebhookResumeRequest

SECRET = "segredo-de-teste-hs256"
TENANT = "tenant_demo"
SUB = "user_42"


class _Req:
    """Stub de Request. Só `headers` é lido pelo alvo."""

    def __init__(self, authorization: str | None = None):
        self.headers = {"Authorization": authorization} if authorization else {}


def _token(**claims) -> str:
    base = {"sub": SUB, "tenant_id": TENANT}
    base.update(claims)
    return pyjwt.encode(base, SECRET, algorithm="HS256")


def _bearer(**claims) -> _Req:
    return _Req(f"Bearer {_token(**claims)}")


def _body(**kw) -> WebhookResumeRequest:
    return WebhookResumeRequest(tenant_id=TENANT, **kw)


@pytest.fixture(autouse=True)
def _segredo_wirado(monkeypatch):
    """Segredo presente por padrão. O caso do segredo AUSENTE sobrescreve."""
    settings = cg_main.get_settings()
    monkeypatch.setattr(settings, "auth_jwt_secret", SECRET, raising=False)
    return settings


# ── classe 1: caminho externo/sistema ────────────────────────────────────────

def test_sem_header_e_caminho_externo_nao_403():
    """Ausência de credencial NÃO é recusa aqui — é o caminho externo/claimed.

    Este é o ramo que separa este portão do `enforce_write` canônico (que devolve 401
    em credencial ausente). São perguntas diferentes: lá a rota é só de escrita
    administrativa; aqui a MESMA rota serve resume de sistema.
    """
    assert cg_main._resolve_approver_principal(_Req(), _body()) is None


def test_header_malformado_cai_no_externo():
    """`bearer_from_header` canônico devolve None para header sem o esquema."""
    assert cg_main._resolve_approver_principal(_Req("Basic abc"), _body()) is None
    assert cg_main._resolve_approver_principal(_Req("Bearer   "), _body()) is None


def test_sem_segredo_degrada_para_externo_e_AVISA(monkeypatch, caplog):
    """Segredo não wirado ⇒ trata como externo, **avisando**.

    A alternativa tentadora seria fingir `possessed` (o token "parece" bom). Atribuir a
    decisão a quem não se verificou é pior que não atribuir — e a degradação tem de
    nomear o que deixou de valer, senão vira 'using default values'.
    """
    settings = cg_main.get_settings()
    monkeypatch.setattr(settings, "auth_jwt_secret", "", raising=False)
    with caplog.at_level("WARNING"):
        assert cg_main._resolve_approver_principal(_bearer(), _body()) is None
    assert any("AUTH_JWT_SECRET" in r.message for r in caplog.records)


# ── classe 3: recusa ─────────────────────────────────────────────────────────

def test_assinatura_errada_e_403():
    """Token assinado com OUTRO segredo — `verify_user_jwt` canônico devolve None."""
    alheio = pyjwt.encode({"sub": SUB, "tenant_id": TENANT}, "outro-segredo", algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(_Req(f"Bearer {alheio}"), _body())
    assert e.value.status_code == 403


def test_token_expirado_e_403():
    expirado = pyjwt.encode(
        {"sub": SUB, "tenant_id": TENANT, "exp": 1_000_000_000}, SECRET, algorithm="HS256"
    )
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(_Req(f"Bearer {expirado}"), _body())
    assert e.value.status_code == 403


def test_tenant_do_token_diferente_do_corpo_e_403():
    req = _bearer(tenant_id="outro_tenant")
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(req, _body())
    assert e.value.status_code == 403
    assert "tenant" in e.value.detail


def test_instance_id_de_outro_usuario_e_403():
    """Auto-consistência: a instância que o Console manda é do dono do JWT."""
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(_bearer(), _body(instance_id="human-outro"))
    assert e.value.status_code == 403


# ── classe 2, regime SEM ABAC (form-fill genérico: wrap-up) ──────────────────

def test_form_fill_generico_passa_SEM_grant_nenhum():
    """`required_abac=None` ⇒ nenhum grant é exigido. É o wrap-up.

    Testemunha contra o endurecimento "por simetria": quem autoriza aqui é o binding do
    claim (`instance==human-{sub}` + caller==claimant no `handle_resume`), e o operador
    comum não tem `approvals.decide`.
    """
    out = cg_main._resolve_approver_principal(_bearer(module_config={}), _body())
    assert out == {
        "principal_type": "human",
        "decided_by": SUB,
        "verification_class": "possessed",
    }


def test_form_fill_generico_ignora_pool_fora_do_escopo():
    """Sem `required_abac` o pool-scope também não é exigido — o claim já amarra."""
    req = _bearer(accessible_pools=["outro_pool"], module_config={})
    assert cg_main._resolve_approver_principal(req, _body(pool_id="retencao_humano")) is not None


# ── classe 2/3, regime COM ABAC (aprovação: approvals.decide) ────────────────

APROVACAO = ("approvals", "decide")


def _mc(access: str) -> dict:
    return {"module_config": {"approvals": {"decide": {"access": access}}}}


def test_aprovacao_sem_module_config_e_403():
    """Grant-first: ausência de grants nunca é autorização.

    Divergência 3 da tabela do `py-authz` — a `evaluation-api` LIBERA neste caso, no
    ramo legado. Aqui recusa, e o passo 6 do arco fecha o outro lado.
    """
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(_bearer(), _body(), APROVACAO)
    assert e.value.status_code == 403
    assert "approvals.decide" in e.value.detail


def test_aprovacao_com_grant_none_e_403():
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(_bearer(**_mc("none")), _body(), APROVACAO)
    assert e.value.status_code == 403


def test_aprovacao_com_read_write_passa():
    req = _bearer(**_mc("read_write"), unrestricted=True)
    assert cg_main._resolve_approver_principal(req, _body(), APROVACAO) is not None


def test_grant_de_OUTRO_modulo_nao_serve():
    """Supervisor tem `evaluation.revisar`, não `approvals.decide`. É o desejado."""
    req = _bearer(module_config={"evaluation": {"revisar": {"access": "read_write"}}})
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(req, _body(), APROVACAO)
    assert e.value.status_code == 403


def test_read_only_satisfaz_write_only_e_isso_e_a_tabela_canonica():
    """`read_only` e `write_only` COLAPSAM em rank 1 — pinado de propósito.

    O call site pede `write_only`, então um grant `read_only` passaria. Não é defeito
    vivo: `infra/modules.yaml` declara `approvals.decide` com domínio
    `[none, read_write]`, e `auth-api/db.py:661` RECUSA gravar access fora do domínio —
    logo `read_only` neste campo não é cunhável. O que este teste protege é a descoberta
    dessa dependência: se alguém alargar o domínio do campo, é aqui que se lembra de que
    o portão pede o grau lateral, e não `read_write`.
    """
    req = _bearer(**_mc("read_only"), unrestricted=True)
    assert cg_main._resolve_approver_principal(req, _body(), APROVACAO) is not None


# ── escopo de pool (o SEGUNDO verificador, consolidado no passo 1) ───────────

def test_aprovacao_com_pool_fora_do_escopo_e_403():
    req = _bearer(**_mc("read_write"), accessible_pools=["pool_a"])
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(req, _body(pool_id="pool_b"), APROVACAO)
    assert e.value.status_code == 403
    assert "pool" in e.value.detail


def test_aprovacao_com_pool_dentro_do_escopo_passa():
    req = _bearer(**_mc("read_write"), accessible_pools=["pool_a", "pool_b"])
    assert cg_main._resolve_approver_principal(req, _body(pool_id="pool_b"), APROVACAO) is not None


def test_lista_declarada_VENCE_unrestricted_setado_por_engano():
    """O restritivo vence: alargar domínio não aparece na tela como erro."""
    req = _bearer(**_mc("read_write"), accessible_pools=["pool_a"], unrestricted=True)
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(req, _body(pool_id="pool_b"), APROVACAO)
    assert e.value.status_code == 403


def test_claim_unrestricted_NAO_alcanca_nada():
    """O claim `unrestricted` foi REMOVIDO em 2026-08-31 (AUT-13) — e nao volta.

    ⚠️ Este teste afirmava o oposto (`... is not None`). Escopo e capacidade sao eixos
    distintos: um claim de ESCOPO nunca concede acesso, e pools sao do TENANT, nao da
    plataforma — logo escopo de usuario e sempre uma lista enumerada. Reescrito como
    TESTEMUNHA, nao apagado: assim reintroduzir a porta larga reprova aqui (AUT-27).
    """
    req = _bearer(**_mc("read_write"), accessible_pools=[], unrestricted=True)
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(req, _body(pool_id="qualquer"), APROVACAO)
    assert e.value.status_code == 403


def test_pools_vazio_RECUSA_e_este_teste_avisou_a_virada():
    """`[]` = NENHUM pool (AUT-03, virada de 2026-08-31).

    ⚠️ Registro de que o INSTRUMENTO funcionou: a versao anterior deste teste afirmava
    o ramo legado (`[]` = irrestrito, com WARNING contado) e trazia no proprio docstring
    a frase *"e este teste que vai ficar vermelho para avisar, em vez de o escopo vazar
    mudo"*. Foi o que aconteceu — ele ficou vermelho na virada, exatamente como
    prometido. O que restava era escrever a versao pos-virada, e e esta.

    Nao ha mais log a conferir aqui: o ramo legado nao existe, entao nao ha o que contar.
    O que se conta agora e o desfecho — 403 — e ele e o produto, nao um marcador.
    """
    req = _bearer(**_mc("read_write"))
    with pytest.raises(HTTPException) as e:
        cg_main._resolve_approver_principal(req, _body(pool_id="qualquer"), APROVACAO)
    assert e.value.status_code == 403
