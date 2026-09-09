#!/usr/bin/env python3
"""
_config_permissions_census.py — quem DETEM um campo de capacidade x quem o DECLARA.

Auxiliar de `probe_config_permissions_census.sh` (MOD-01 / fase G0 do
`adr-abac-module-granularity-and-delegation.md`). Vive em arquivo proprio porque a
comparacao e por-usuario e o `jq` nao esta em toda maquina que roda os probes — dois
gates da familia ja saem INCONCLUSIVO por causa dele.

POR QUE ELE EXISTE
------------------
O ADR pede um censo ANTES de qualquer mudanca (D7: "nenhum corte entra sem um censo
re-executavel comparando quem detem o campo no banco contra o que `role_defaults` +
seed declaram"). O defeito que ele mede nao e um grant errado — e **nao haver
mecanismo que confira a populacao contra a declaracao**.

Medido em 2026-09-08, e e a razao de a ficha existir: em oito dias a populacao de
`config.permissions` mudou nos DOIS sentidos sem que nada acusasse — um portador
sumiu (o `pending.md` ainda o citava) e tres apareceram. Deriva so se corrige onde
ha probe.

O QUE ELE NAO MEDE, DE PROPOSITO
--------------------------------
**Seed x catalogo** ja tem casa: `_seed_vs_preset.py`, arquivo x arquivo. Repetir a
comparacao aqui seria uma segunda casa afirmando o mesmo fato, com a mais nova
vencendo em silencio. Este censo mede o eixo VIZINHO: **banco x catalogo deployado**.

E ele **nao julga** as classes B e C abaixo. Deter um campo que o preset nao declara
e legitimo (a tela concede depois do nascimento); nao deter um que ele declara
tambem (a tela revoga). O censo torna a populacao VISIVEL — quem decide o alvo e a
MOD-04. Quando ela decidir, a classe B vira assercao e este helper ganha o ramo.

AS TRES CLASSES
---------------
  A  detem  E  o papel declara      -> coerente com a certidao de nascimento
  B  detem  E  nenhum papel declara -> concedido depois (tela) ou deriva
  C  nao detem E o papel declara    -> revogado depois, ou preset que nao aplicou

O QUE O DEIXA VERMELHO
----------------------
  R1  o campo nao existe em NENHUMA das duas declaracoes -> o censo nao mede nada,
      e um censo que conta zero por medir a coisa errada e pior que censo nenhum
  R2  o ARQUIVO (`infra/modules.yaml`) e o CATALOGO DEPLOYADO (`GET /auth/modules`)
      discordam sobre quem declara o campo -> "existe != esta aplicado": editar o
      YAML sem reiniciar o auth-api produz exatamente isso

Sem credencial ou sem servico: INCONCLUSIVO (2), nunca 0. "Nenhum portador" e uma
resposta legitima do censo; "nao consegui perguntar" nao e.

Uso:
    _config_permissions_census.py <auth_url> <tenant_id> [modules.yaml]
Env:
    PLUGHUB_TOKEN   Bearer (obrigatorio)
    CAMPO           default "config.permissions" — o par modulo.campo a censar
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

VERDE, VERMELHO, INCONCLUSIVO = 0, 1, 2

# stdout em UTF-8 e independente do console: sem isto, um `⊆` no relatorio
# derruba o helper por `UnicodeEncodeError` em terminal cp1252 — instrumento que
# falha por AMBIENTE, exatamente o que ele existe para nao ser.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# Tabela canonica em `plughub_authz` (tres degraus desde a AUT-40). Aqui basta
# "detem ou nao", entao o rank nao entra: qualquer coisa != none conta.
NAO_DETEM = ("none", "", None)


def _get(url: str, token: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _papeis_que_declaram(schema: dict, campo: str) -> set[str] | None:
    """Papeis com `role_defaults` != none para `campo`. `None` = campo ausente."""
    definicao = (schema or {}).get(campo)
    if not isinstance(definicao, dict):
        return None
    defaults = definicao.get("role_defaults") or {}
    return {p for p, a in defaults.items() if a not in NAO_DETEM}


def main() -> int:
    if len(sys.argv) < 3:
        print("uso: _config_permissions_census.py <auth_url> <tenant_id> [modules.yaml]")
        return INCONCLUSIVO
    auth_url, tenant = sys.argv[1].rstrip("/"), sys.argv[2]
    yaml_path = sys.argv[3] if len(sys.argv) > 3 else "infra/modules.yaml"
    token = os.environ.get("PLUGHUB_TOKEN", "")
    campo_full = os.environ.get("CAMPO", "config.permissions")
    if "." not in campo_full:
        print(f"INCONCLUSIVO — CAMPO deve ser 'modulo.campo', veio {campo_full!r}")
        return INCONCLUSIVO
    modulo, campo = campo_full.split(".", 1)
    if not token:
        print("INCONCLUSIVO — sem PLUGHUB_TOKEN: o censo nao pode perguntar nada")
        return INCONCLUSIVO

    print(f"== censo de `{campo_full}` — banco x catalogo deployado ==")

    # ── declaracao 1: o ARQUIVO ───────────────────────────────────────────────
    try:
        import yaml  # type: ignore
        with open(yaml_path, encoding="utf-8") as fh:
            cat_arquivo = yaml.safe_load(fh)
        mod_arq = next((m for m in cat_arquivo.get("modules", [])
                        if m.get("module_id") == modulo), None)
        decl_arquivo = _papeis_que_declaram((mod_arq or {}).get("permission_schema", {}), campo)
    except Exception as exc:  # noqa: BLE001
        print(f"INCONCLUSIVO — nao consegui ler {yaml_path}: {exc}")
        return INCONCLUSIVO

    # ── declaracao 2: o CATALOGO DEPLOYADO ────────────────────────────────────
    try:
        mods = _get(f"{auth_url}/auth/modules?active_only=true", token)
        mods = mods if isinstance(mods, list) else mods.get("modules", [])
        mod_dep = next((m for m in mods if m.get("module_id") == modulo), None)
        decl_deploy = _papeis_que_declaram((mod_dep or {}).get("permission_schema", {}), campo)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        print(f"INCONCLUSIVO — auth-api nao respondeu /auth/modules: {exc}")
        return INCONCLUSIVO

    # R1 — o campo tem de existir em ALGUMA declaracao, senao o censo mede o vazio
    if decl_arquivo is None and decl_deploy is None:
        print(f"R1. VERMELHO — `{campo_full}` nao existe nem no arquivo nem no catalogo "
              f"deployado. Um censo que conta zero por medir a coisa errada e pior que "
              f"censo nenhum.")
        return VERMELHO

    # R2 — arquivo x deployado: "existe != esta aplicado"
    if decl_arquivo != decl_deploy:
        print(f"R2. VERMELHO — arquivo e catalogo deployado discordam sobre quem declara "
              f"`{campo_full}`:\n"
              f"      {yaml_path}: {sorted(decl_arquivo) if decl_arquivo is not None else 'AUSENTE'}\n"
              f"      /auth/modules: {sorted(decl_deploy) if decl_deploy is not None else 'AUSENTE'}\n"
              f"    Editar o YAML sem reiniciar o auth-api produz exatamente isto.")
        return VERMELHO

    declarantes = decl_deploy or set()
    print(f"R1/R2. verde — campo declarado nas duas casas; papeis que o dao no "
          f"nascimento: {sorted(declarantes) or '(nenhum)'}")

    # ── populacao ─────────────────────────────────────────────────────────────
    try:
        users = _get(f"{auth_url}/auth/users?tenant_id={tenant}", token)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        print(f"INCONCLUSIVO — nao consegui listar usuarios: {exc}")
        return INCONCLUSIVO

    classes: dict[str, list[str]] = {"A": [], "B": [], "C": []}
    sem_leitura = []
    for u in users:
        uid, email = u.get("id"), u.get("email", "?")
        papeis = set(u.get("roles") or [])
        try:
            cfg = _get(f"{auth_url}/auth/users/{uid}/module-config", token)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
            sem_leitura.append(email)
            continue
        acesso = ((cfg or {}).get(modulo) or {}).get(campo, {}).get("access")
        detem = acesso not in NAO_DETEM
        declarado = bool(papeis & declarantes)
        if detem and declarado:
            classes["A"].append(f"{email} [{acesso}] papeis={','.join(sorted(papeis))}")
        elif detem:
            classes["B"].append(f"{email} [{acesso}] papeis={','.join(sorted(papeis))}")
        elif declarado:
            classes["C"].append(f"{email} papeis={','.join(sorted(papeis))}")

    if sem_leitura:
        print(f"INCONCLUSIVO — {len(sem_leitura)} usuario(s) sem leitura de module-config: "
              f"{', '.join(sem_leitura)}")
        return INCONCLUSIVO

    print(f"\npopulacao: {len(users)} usuario(s) em {tenant}")
    rotulos = {
        "A": "detem E o papel declara      — coerente com a certidao de nascimento",
        "B": "detem E nenhum papel declara — concedido depois (tela) ou DERIVA",
        "C": "nao detem E o papel declara  — revogado depois, ou preset que nao aplicou",
    }
    for c in ("A", "B", "C"):
        print(f"\n  {c} ({len(classes[c])}) {rotulos[c]}")
        for linha in sorted(classes[c]):
            print(f"      {linha}")

    print(f"\n== censo executado — {len(classes['A'])}A / {len(classes['B'])}B / "
          f"{len(classes['C'])}C ==")
    print("As classes B e C NAO sao veredicto: deter um campo que o preset nao declara e")
    print("legitimo (a tela concede depois), e nao deter um que ele declara tambem. Quem")
    print("decide o alvo e a MOD-04; quando ela decidir, a classe B vira assercao aqui.")
    return VERDE


if __name__ == "__main__":
    sys.exit(main())
