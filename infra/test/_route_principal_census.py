#!/usr/bin/env python3
"""
Censo de COBERTURA DE CREDENCIAL por ROTA — o TERCEIRO eixo de autorizacao.

POR QUE ELE EXISTE (2026-08-29)
===============================
Ja havia dois censos, e nenhum responde a pergunta deste:

  · `probe_authz_single_verifier.sh` (C1) conta **quem decodifica JWT**;
  · `_scope_resolver_census.py`      (C4) conta **quem resolve escopo de pool**.

Os dois medem QUEM DECIDE. Nenhum mede **quais rotas exigem que alguem decida** — e
uma rota que nao declara dependencia nenhuma passa por baixo dos dois sem tocar em
nada. Medido em 2026-08-28 (achado colateral da T3 do ADR de relatorios): 12 rotas
`/reports/*` sem principal, quatro respondendo **200 sem credencial**, uma delas
`/reports/customers/{id}/360`. E a regra ja escrita no CLAUDE.md recorrendo pela
terceira vez: *"um censo desenhado para um eixo nao prova nada sobre o eixo vizinho"*.

Medido de novo em 2026-08-29, ao construir este arquivo: o eixo NAO para no prefixo
`/reports/*`. A varredura acusou mais quatro em `sessions.py` — entre elas
`GET /sessions/{id}/stream`, que serve a **transcricao inteira** do contato e
respondia 200 anonimo, enquanto `/v1/transcript/sessions/{id}`, a rota que existe
para servir esse mesmo dado, ja exigia credencial. Duas portas para o mesmo dado, uma
aberta. O recorte do TODO (`/reports/*`) era o do achado, nao o do eixo.

POR QUE NAO E UM GREP
=====================
`grep -L Depends` mede a proposicao adjacente: acusa a rota que usa outro guard e
absolve a que tem um `Depends` qualquer (paginacao, store). O que caracteriza
cobertura e a COMBINACAO de:
  (a) ser uma funcao decorada com `@router.<metodo>("...")` ou `@app.<metodo>("...")`;
  (b) ter, entre os defaults dos parametros, um `Depends(<principal>)` cujo nome
      esta na lista de PRINCIPAIS declarada abaixo — ou chamar um guard conhecido
      no CORPO (o caso de `audit.py`, que decide dentro do handler DE PROPOSITO,
      porque a recusa precisa gravar trilha antes de virar resposta).

ERRO QUE ESTE ARQUIVO JA COMETEU, e por isso o terceiro ramo existe
-------------------------------------------------------------------
A primeira versao tinha a lista de PRINCIPAIS incompleta (faltavam
`require_principal` e `require_dashboard_principal`) e acusou `/admin/*` e
`/dashboard/*` — sete falsos positivos, todos gateados. Um censo com lista fechada
falha para o lado ERRADO: acusa quem esta coberto por um guard que ele nao conhece.
Dai o ramo `INDECIDIVEL`: rota sem principal conhecido **mas com algum outro
`Depends`** nao e acusada — e reportada, para alguem dizer se aquele nome e um
portao. Nao-medido DITO, nunca omitido.

Saida (uma linha por rota, `|`-separada):
    <estado>|<metodo>|<path>|<arquivo>:<linha>|<deps>
com <estado> em COBERTA | DESCOBERTA | INDECIDIVEL. Codigo de saida sempre 0 —
quem julga e o shell.
"""
from __future__ import annotations

import ast
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ESCOPO = "packages/analytics-api/src"

# Dependencias que EXIGEM identidade verificavel. Cada uma recusa quando nao ha como
# autorizar; a diferenca entre elas e o segredo que aceitam e o que fazem no ramo
# sem-segredo, nao se exigem ou nao credencial.
PRINCIPAIS = {
    "optional_pool_principal",     # JWT do auth-api; 401 sem header (desde 2026-08-27)
    "require_pool_principal",      # irmao ESTRITO: 401 tambem no ramo sem-segredo
    "sse_pool_principal",          # idem, lendo `?token=` alem do header (EventSource)
    "require_principal",           # token de SISTEMA (`admin_jwt_secret`), /admin/*
    "require_dashboard_principal", # sistema OU usuario, header OU `?token=` (SSE)
}

# Guards que decidem no CORPO do handler, nao na assinatura. `audit.py` e o UNICO, e e
# deliberado: a recusa dele GRAVA trilha antes de virar resposta, e um `Depends` que ja
# responde nao deixa isso acontecer (ver `probe_audit_surface.sh` P4).
#
# ⚠️ Esta lista e a excecao ao que o censo confere. Cada nome novo aqui e uma rota cuja
# assinatura ele deixa de olhar — entao a pergunta antes de acrescentar um e sempre
# *"por que isto nao pode ser um `Depends`?"*. O SSE parecia exigir uma entrada (o
# `?token=` do `EventSource`), e nao exigia: virou `sse_pool_principal`, uma dependencia
# como as outras.
GUARDS_NO_CORPO = {"_check_audit_access"}

METODOS = {"get", "post", "put", "delete", "patch"}


def _prefixo_do_router(arvore: ast.Module) -> str:
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Assign):
            continue
        for alvo in no.targets:
            if isinstance(alvo, ast.Name) and alvo.id == "router" and isinstance(no.value, ast.Call):
                for kw in no.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        return str(kw.value.value)
    return ""


def _rota(no: ast.AST) -> tuple[str, str, str] | None:
    """(metodo, path, objeto) do decorador de rota, ou None."""
    for dec in no.decorator_list:
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
            continue
        if dec.func.attr not in METODOS:
            continue
        if not (dec.args and isinstance(dec.args[0], ast.Constant)):
            continue
        obj = dec.func.value.id if isinstance(dec.func.value, ast.Name) else ""
        return dec.func.attr.upper(), str(dec.args[0].value), obj
    return None


def _depends(no: ast.AST) -> set[str]:
    """Nomes passados a `Depends(...)` nos defaults dos parametros."""
    achados: set[str] = set()
    defaults = list(no.args.defaults) + [d for d in no.args.kw_defaults if d is not None]
    for d in defaults:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "Depends" and d.args:
            alvo = d.args[0]
            if isinstance(alvo, ast.Name):
                achados.add(alvo.id)
            elif isinstance(alvo, ast.Attribute):
                achados.add(alvo.attr)
    return achados


def _chamados(no: ast.AST) -> set[str]:
    nomes: set[str] = set()
    for sub in ast.walk(no):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Name):
                nomes.add(sub.func.id)
            elif isinstance(sub.func, ast.Attribute):
                nomes.add(sub.func.attr)
    return nomes


def main() -> int:
    for caminho in sorted((RAIZ / ESCOPO).glob("**/*.py")):
        rel = caminho.relative_to(RAIZ).as_posix()
        if "/tests/" in rel:
            continue
        try:
            arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue
        prefixo = _prefixo_do_router(arvore)
        for no in ast.walk(arvore):
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            rota = _rota(no)
            if rota is None:
                continue
            metodo, path, obj = rota
            # `@app.get` nao passa pelo prefixo do router do arquivo.
            path_completo = (prefixo + path) if obj == "router" else path
            deps = _depends(no)
            if (deps & PRINCIPAIS) or (_chamados(no) & GUARDS_NO_CORPO):
                estado = "COBERTA"
            elif deps:
                estado = "INDECIDIVEL"
            else:
                estado = "DESCOBERTA"
            print("%s|%s|%s|%s:%d|%s" % (
                estado, metodo, path_completo, rel, no.lineno, ",".join(sorted(deps)) or "-",
            ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
