#!/usr/bin/env python3
"""
backfill_preset_fields.py — completa, em quem JA EXISTE, campos que o preset passou
a declarar depois do nascimento.

POR QUE ELE PRECISA EXISTIR
---------------------------
`role_defaults` e CERTIDAO DE NASCIMENTO: aplicado uma vez, em `create_user`. Editar
o preset nao muda quem ja existe — e isso e decisao, nao defeito (deduzir a mudanca
apagaria, em silencio, grants concedidos a mao).

A consequencia e que toda edicao de preset deixa uma populacao para tras. Medido em
2026-09-08, na MOD-08: o preset do `supervisor` ganhou `approvals.decide`,
`approvals.operacao` e `evaluation.contestar` — os tres campos sem os quais ele nao
consegue contratar um `operator` sob o guard de RANK da MOD-02. Nenhum supervisor
existente os recebeu, e o sintoma seria *"a tela parou de deixar"*.

⚠️ A CLASSE C DO CENSO NAO E UMA FILA DE TRABALHO
--------------------------------------------------
`probe_config_permissions_census.sh` lista em C quem *nao detem e o papel declara*.
Isso mistura TRES causas, e so a primeira e backfill:

  1. o preset mudou depois do nascimento          -> e isto aqui
  2. foi revogado/nunca concedido de proposito    -> deixar em paz
  3. o preset falhou ao aplicar no nascimento     -> investigar (foi a AUT-12)

Medido no mesmo dia: dos 5 em C, um era `navprobe@` — papel `operator`, sem campos
que o preset de operator SEMPRE declarou. Nao e caso 1: e fixture deliberadamente
minima do probe de navegacao, e completa-la mudaria o que aquele probe mede.

Por isso o alvo aqui e EXPLICITO (`--emails` ou `--papel`) e os campos tambem
(`--campos`). Um script que varresse a classe C inteira consertaria o caso 1 e
estragaria o caso 2, sem que nada ficasse vermelho.

REGRAS
------
· Escreve pela API OFICIAL (invariante da casa: nada de UPDATE direto no banco).
· NUNCA REBAIXA. So preenche onde `rank(preset) > rank(atual)`; se o usuario tem
  mais do que o preset declara, fica como esta.
· O VALOR vem do catalogo (`role_defaults` dos papeis DAQUELE usuario), nunca de
  literal no script — quem decide o que o papel concede e a declaracao.
· Idempotente: rodar de novo nao muda nada.
· DRY-RUN por default. `--aplicar` para escrever.

Uso:
    backfill_preset_fields.py --campos approvals.decide,evaluation.contestar \\
                              --papel supervisor [--aplicar]
Env:
    PLUGHUB_TOKEN   Bearer de MASTER (`config.permissions: read_write`)
    AUTH_BASE       default http://localhost:3202
    TENANT          default tenant_demo
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

RANK = {"none": 0, "read_only": 1, "write_only": 1, "read_write": 2}


def call(base, path, token, data=None, method=None):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method or ("POST" if data is not None else "GET"),
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            bruto = r.read()
            return r.status, (json.loads(bruto) if bruto.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--campos", required=True,
                    help="lista `modulo.campo` separada por virgula")
    ap.add_argument("--papel", help="alvo: todos os usuarios com este papel")
    ap.add_argument("--emails", help="alvo: lista de e-mails separada por virgula")
    ap.add_argument("--aplicar", action="store_true", help="escreve (default: dry-run)")
    a = ap.parse_args()
    if not a.papel and not a.emails:
        print("ERRO — declare o alvo (--papel ou --emails). Varrer a classe C inteira")
        print("       consertaria o preset mudado e estragaria a fixture minima.")
        return 2

    base = os.environ.get("AUTH_BASE", "http://localhost:3202").rstrip("/")
    tenant = os.environ.get("TENANT", "tenant_demo")
    token = os.environ.get("PLUGHUB_TOKEN", "")
    if not token:
        print("ERRO — sem PLUGHUB_TOKEN (precisa ser MASTER)")
        return 2

    campos = [c.strip() for c in a.campos.split(",") if c.strip()]
    alvos_email = {e.strip() for e in (a.emails or "").split(",") if e.strip()}

    st, mods = call(base, "/auth/modules?active_only=true", token)
    if st != 200:
        print(f"ERRO — /auth/modules devolveu {st}")
        return 2
    mods = mods if isinstance(mods, list) else mods.get("modules", [])
    catalogo = {m["module_id"]: (m.get("permission_schema") or {}) for m in mods}

    st, users = call(base, f"/auth/users?tenant_id={tenant}", token)
    if st != 200:
        print(f"ERRO — /auth/users devolveu {st}")
        return 2

    print(f"{'APLICANDO' if a.aplicar else 'DRY-RUN'} · campos={campos} · "
          f"alvo={'papel=' + a.papel if a.papel else 'emails'}\n")
    tocados = 0
    for u in sorted(users, key=lambda x: x.get("email", "")):
        papeis = list(u.get("roles") or [])
        email = u.get("email", "?")
        if alvos_email and email not in alvos_email:
            continue
        if a.papel and a.papel not in papeis:
            continue

        st, cfg = call(base, f"/auth/users/{u['id']}/module-config", token)
        if st != 200:
            print(f"  {email}: PULADO — nao consegui ler o config ({st})")
            continue
        cfg = cfg or {}
        mudancas = []
        for alvo in campos:
            modulo, _, campo = alvo.partition(".")
            defs = (catalogo.get(modulo) or {}).get(campo) or {}
            # O valor vem do catalogo, para os papeis DESTE usuario — nunca literal.
            declarado = "none"
            for papel in papeis:
                v = (defs.get("role_defaults") or {}).get(papel, "none")
                if RANK.get(v, 0) > RANK.get(declarado, 0):
                    declarado = v
            if RANK.get(declarado, 0) == 0:
                continue  # o papel deste usuario nao declara o campo
            atual = ((cfg.get(modulo) or {}).get(campo) or {}).get("access", "none")
            if RANK.get(declarado, 0) <= RANK.get(atual, 0):
                continue  # ja tem, ou tem mais — nunca rebaixa
            cfg.setdefault(modulo, {})[campo] = {"access": declarado, "scope": []}
            mudancas.append(f"{alvo}: {atual} -> {declarado}")

        if not mudancas:
            print(f"  {email}: nada a fazer")
            continue
        tocados += 1
        print(f"  {email} ({','.join(papeis)}): " + " · ".join(mudancas))
        if a.aplicar:
            st, resp = call(base, f"/auth/users/{u['id']}/module-config", token,
                            data=cfg, method="PUT")
            print(f"      PUT -> {st}" + ("" if st == 200 else f" {str(resp)[:160]}"))
            if st != 200:
                return 1

    print(f"\n{tocados} usuario(s) " + ("alterado(s)" if a.aplicar else "seriam alterados"))
    if not a.aplicar and tocados:
        print("Nada foi escrito. Repita com --aplicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
