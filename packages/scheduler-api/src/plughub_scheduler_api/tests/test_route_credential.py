"""
test_route_credential.py — SCH-01 (2026-09-17): as rotas de Agenda passam a ter portão.

O QUE ESTAVA ABERTO. As 9 rotas de `/v1/agendas` decidiam com o header `X-Tenant-ID` e nada mais:
sem Bearer, sem token de serviço, sem ABAC. O portão existia só na UI (`RequireAbac`), e o proxy
dela repassa o prefixo sem credencial — quem alcançasse a porta criava agenda, trocava o alvo e
disparava com `POST /fire`. **Agenda aciona POOL**, e há pools que promovem deploy e que contatam
cliente: disparar é EFEITO, não leitura.

DUAS CLASSES DE TESTE, E ELAS NÃO SE SUBSTITUEM:

  · **censo (AST)** — TODA função de rota do `router.py` chama `_principal`. É o único que pega a
    rota NOVA que alguém acrescentar amanhã sem portão; um teste por rota existente não pega.
  · **comportamento** — o que cada porta decide, incluindo os dois controles positivos (o token de
    serviço passa; o usuário com o grant passa). Só o negativo passaria por proteção mesmo se a
    rota estivesse quebrada para todo mundo.

⚠️ O tenant do caminho de USUÁRIO sai do TOKEN, e há teste para o header discordante: deixar o
chamador escolher o tenant transformaria o portão num filtro preenchido por quem é filtrado.
"""
from __future__ import annotations

import ast
import pathlib
import time
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from plughub_scheduler_api import router as R

SECRET = "segredo-de-teste-hs256-do-scheduler"
SVC    = "token-de-servico-do-seed"

CONFIGURAR = {"scheduler": {"configurar": {"access": "read_write"}}}
OPERAR     = {"scheduler": {"operacao":   {"access": "read_write"}}}
SO_VER     = {"scheduler": {"operacao":   {"access": "read_only"}}}
OUTRO      = {"contacts":  {"visualizar": {"access": "read_write"}}}


def _token(tenant="tenant_a", module_config=None, sub="ana"):
    return pyjwt.encode({"sub": sub, "tenant_id": tenant, "module_config": module_config or {},
                         "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")


def _req(headers=None):
    return SimpleNamespace(headers={k.lower(): v for k, v in (headers or {}).items()})


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    s = R.get_settings()
    monkeypatch.setattr(s, "jwt_secret", SECRET, raising=False)
    monkeypatch.setattr(s, "service_token", SVC, raising=False)
    return s


# ── Censo: nenhuma rota sem portão ────────────────────────────────────────────

_ROUTER_PY = pathlib.Path(R.__file__)


def _rotas_sem_portao() -> list[str]:
    """Funções decoradas com `@router.<verbo>` que não chamam `_principal` no corpo."""
    arvore = ast.parse(_ROUTER_PY.read_text(encoding="utf-8"))
    faltando: list[str] = []
    for no in ast.walk(arvore):
        if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        e_rota = any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
            for d in no.decorator_list
        )
        if not e_rota:
            continue
        chama = any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "_principal"
            for c in ast.walk(no)
        )
        if not chama:
            faltando.append(no.name)
    return faltando


class TestCenso:
    def test_toda_rota_passa_pelo_portao(self):
        assert _rotas_sem_portao() == []

    def test_o_censo_ENXERGA_rota_sem_portao(self):
        """Controle do instrumento: um censo que nunca acusa passaria verde sobre um router aberto.
        Aqui a testemunha é a própria função — se o padrão de decorador mudar, ela vê zero rotas e
        este caso denuncia, em vez de o censo virar um `assert [] == []` sempre verde."""
        arvore = ast.parse(_ROUTER_PY.read_text(encoding="utf-8"))
        rotas = [n.name for n in ast.walk(arvore)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                         and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
                         for d in n.decorator_list)]
        assert len(rotas) >= 9, f"o censo enxergou {len(rotas)} rota(s) — o padrao mudou"


# ── Porta de serviço (aditiva) ────────────────────────────────────────────────

class TestPortaDeServico:
    def test_token_certo_passa_e_o_tenant_vem_do_header(self):
        t = R._principal(_req({"x-service-token": SVC}), R.CONFIGURAR, "criar agenda", "tenant_z")
        assert t == "tenant_z"

    def test_token_errado_nao_passa(self):
        with pytest.raises(HTTPException) as e:
            R._principal(_req({"x-service-token": "outro"}), R.CONFIGURAR, "criar agenda", "tenant_z")
        assert e.value.status_code == 401

    def test_token_vazio_no_servico_FECHA_a_porta(self, _ambiente, monkeypatch):
        """Aditivo quer dizer: sem token configurado, a porta não existe — nunca 'sem token, entra'."""
        monkeypatch.setattr(_ambiente, "service_token", "", raising=False)
        with pytest.raises(HTTPException) as e:
            R._principal(_req({"x-service-token": ""}), R.CONFIGURAR, "criar agenda", "tenant_z")
        assert e.value.status_code == 401

    def test_servico_sem_tenant_no_header_recusa(self):
        with pytest.raises(HTTPException) as e:
            R._principal(_req({"x-service-token": SVC}), R.CONFIGURAR, "criar agenda", None)
        assert e.value.status_code == 400


# ── Porta de usuário ──────────────────────────────────────────────────────────

class TestPortaDeUsuario:
    def test_sem_credencial_401(self):
        with pytest.raises(HTTPException) as e:
            R._principal(_req(), R.VER, "listar agendas", "tenant_a")
        assert e.value.status_code == 401

    def test_segredo_ausente_recusa_503_nomeando(self, _ambiente, monkeypatch):
        monkeypatch.setattr(_ambiente, "jwt_secret", "", raising=False)
        with pytest.raises(HTTPException) as e:
            R._principal(_req({"authorization": "Bearer " + _token()}), R.VER, "listar", "tenant_a")
        assert e.value.status_code == 503
        assert "PLUGHUB_SCHEDULER_JWT_SECRET" in e.value.detail

    def test_token_de_outro_segredo_401(self):
        alheio = pyjwt.encode({"sub": "x", "tenant_id": "t", "exp": int(time.time()) + 600},
                              "outro-segredo-qualquer", algorithm="HS256")
        with pytest.raises(HTTPException) as e:
            R._principal(_req({"authorization": "Bearer " + alheio}), R.VER, "listar", "tenant_a")
        assert e.value.status_code == 401

    def test_sem_o_grant_403(self):
        with pytest.raises(HTTPException) as e:
            R._principal(_req({"authorization": "Bearer " + _token(module_config=OUTRO)}),
                         R.VER, "listar agendas", "tenant_a")
        assert e.value.status_code == 403

    def test_leitura_nao_basta_para_AGIR(self):
        """`operacao` em leitura vê a lista e NÃO dispara — os dois níveis do mesmo campo decidem
        coisas diferentes, e é isso que o catálogo declara (`domain: [none, read_only, read_write]`)."""
        claims_ver = _req({"authorization": "Bearer " + _token(module_config=SO_VER)})
        assert R._principal(claims_ver, R.VER, "listar agendas", "tenant_a") == "tenant_a"
        with pytest.raises(HTTPException) as e:
            R._principal(claims_ver, R.OPERAR, "disparar agenda agora", "tenant_a")
        assert e.value.status_code == 403

    def test_configurar_nao_da_operacao_nem_o_contrario(self):
        """Campos distintos, capacidades distintas: quem cria agenda não necessariamente dispara."""
        so_config = _req({"authorization": "Bearer " + _token(module_config=CONFIGURAR)})
        so_operar = _req({"authorization": "Bearer " + _token(module_config=OPERAR)})
        assert R._principal(so_config, R.CONFIGURAR, "criar agenda", "tenant_a") == "tenant_a"
        assert R._principal(so_operar, R.OPERAR, "disparar", "tenant_a") == "tenant_a"
        with pytest.raises(HTTPException):
            R._principal(so_config, R.OPERAR, "disparar", "tenant_a")
        with pytest.raises(HTTPException):
            R._principal(so_operar, R.CONFIGURAR, "criar agenda", "tenant_a")

    def test_o_tenant_e_o_do_TOKEN_e_o_header_NAO_manda(self):
        req = _req({"authorization": "Bearer " + _token(tenant="tenant_do_token", module_config=SO_VER),
                    "x-tenant-id": "tenant_do_header"})
        assert R._principal(req, R.VER, "listar agendas", "tenant_do_header") == "tenant_do_token"

    def test_token_sem_tenant_401(self):
        sem = pyjwt.encode({"sub": "x", "module_config": SO_VER, "exp": int(time.time()) + 600},
                           SECRET, algorithm="HS256")
        with pytest.raises(HTTPException) as e:
            R._principal(_req({"authorization": "Bearer " + sem}), R.VER, "listar", "tenant_a")
        assert e.value.status_code == 401
