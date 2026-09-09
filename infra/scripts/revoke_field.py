#!/usr/bin/env python3
"""
revoke_field.py — remove um campo de capacidade de quem o detem CONTRA a declaracao.

MOD-04 / fase G3 do `adr-abac-module-granularity-and-delegation.md`.

POR QUE ELE E O IRMAO DO `backfill_preset_fields.py`, E NAO UM `--remover` DELE
-------------------------------------------------------------------------------
Preencher e revogar tem posturas de seguranca OPOSTAS. O backfill erra para o lado
de dar de menos (nunca rebaixa); este erra para o lado de tirar de menos, mas o dano
de um engano e o inverso — tirar a chave-mestra de quem precisa dela para operar. Um
flag `--remover` no mesmo script faria as duas posturas dividirem o mesmo caminho de
codigo, e a diferenca entre elas viraria um `if`.

A REGRA VEM DO CENSO, NAO DE UMA LISTA
--------------------------------------
`probe_config_permissions_census.sh` classifica a populacao em tres:

  A  detem E o papel declara      -> coerente com a certidao de nascimento
  B  detem E nenhum papel declara -> concedido depois, ou deriva
  C  nao detem E o papel declara

Este script revoga a classe **B**, e RECUSA tocar na A sem `--forcar`: quem detem
porque o papel declara nao e deriva, e revoga-lo seria contrariar a declaracao em vez
de reconcilia-la. Medido em 2026-09-08: A = `admin@` + `probe@` (ambos papel `admin`,
que declara o campo); B = os quatro com papel `supervisor`.

⚠️ ANTES DE REVOGAR, MEDIR O QUE O ALVO PERDE
----------------------------------------------
Revogar `config.permissions` rebaixa o alvo de MASTER a DELEGADO, e sob o guard de
RANK (MOD-02) o delegado so concede o que detem. Um supervisor sem os campos do
preset vira um administrador de pessoas **incapaz de contratar**, e o sintoma e "a
tela parou de deixar". Por isso este script imprime, para cada alvo, se ele ainda
consegue conceder o preset de um papel de referencia (`--simular-contratacao`).

· Escreve pela API OFICIAL. · DRY-RUN por default. · Idempotente.

Uso:
    revoke_field.py --campo config.permissions --classe B [--simular-contratacao operator]
    revoke_field.py --campo config.permissions --emails a@x,b@x --aplicar
Env:
    PLUGHUB_TOKEN   Bearer de MASTER
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

RANK = {"none": 0, "read_only": 1, "read_write": 2}


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


def preset_de(catalogo, papeis):
    """`{modulo.campo: access}` — o maior entre os papeis, como `build_module_config`."""
    out = {}
    for modulo, schema in catalogo.items():
        for campo, d in (schema or {}).items():
            melhor = "none"
            for p in papeis:
                v = ((d or {}).get("role_defaults") or {}).get(p, "none")
                if RANK.get(v, 0) > RANK.get(melhor, 0):
                    melhor = v
            if RANK.get(melhor, 0) > 0:
                out[f"{modulo}.{campo}"] = melhor
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--campo", required=True, help="`modulo.campo` a revogar")
    ap.add_argument("--classe", choices=["B"], help="alvo derivado do censo")
    ap.add_argument("--emails", help="alvo explicito, separado por virgula")
    ap.add_argument("--simular-contratacao", metavar="PAPEL",
                    help="apos a revogacao, o alvo ainda concede o preset deste papel?")
    ap.add_argument("--forcar", action="store_true",
                    help="permite revogar da classe A (o papel DECLARA o campo)")
    ap.add_argument("--aplicar", action="store_true", help="escreve (default: dry-run)")
    a = ap.parse_args()
    if not a.classe and not a.emails:
        print("ERRO — declare o alvo (--classe B ou --emails)")
        return 2

    base = os.environ.get("AUTH_BASE", "http://localhost:3202").rstrip("/")
    tenant = os.environ.get("TENANT", "tenant_demo")
    token = os.environ.get("PLUGHUB_TOKEN", "")
    if not token:
        print("ERRO — sem PLUGHUB_TOKEN (precisa ser MASTER)")
        return 2

    modulo, _, campo = a.campo.partition(".")
    st, mods = call(base, "/auth/modules?active_only=true", token)
    if st != 200:
        print(f"ERRO — /auth/modules devolveu {st}")
        return 2
    mods = mods if isinstance(mods, list) else mods.get("modules", [])
    catalogo = {m["module_id"]: (m.get("permission_schema") or {}) for m in mods}
    declarantes = {p for p, v in
                   ((catalogo.get(modulo) or {}).get(campo, {}).get("role_defaults") or {}).items()
                   if RANK.get(v, 0) > 0}
    print(f"{'APLICANDO' if a.aplicar else 'DRY-RUN'} · campo={a.campo} · "
          f"papeis que o DECLARAM: {sorted(declarantes) or '(nenhum)'}\n")

    ref = preset_de(catalogo, [a.simular_contratacao]) if a.simular_contratacao else None

    st, users = call(base, f"/auth/users?tenant_id={tenant}", token)
    if st != 200:
        print(f"ERRO — /auth/users devolveu {st}")
        return 2
    alvos_email = {e.strip() for e in (a.emails or "").split(",") if e.strip()}

    tocados = 0
    for u in sorted(users, key=lambda x: x.get("email", "")):
        email, papeis = u.get("email", "?"), list(u.get("roles") or [])
        if alvos_email and email not in alvos_email:
            continue
        st, cfg = call(base, f"/auth/users/{u['id']}/module-config", token)
        if st != 200:
            print(f"  {email}: PULADO — nao consegui ler o config ({st})")
            continue
        cfg = cfg or {}
        atual = ((cfg.get(modulo) or {}).get(campo) or {}).get("access", "none")
        if RANK.get(atual, 0) == 0:
            if alvos_email:
                print(f"  {email}: ja nao detem")
            continue
        classe = "A" if (set(papeis) & declarantes) else "B"
        if a.classe and classe != a.classe:
            continue
        if classe == "A" and not a.forcar:
            print(f"  {email}: RECUSADO — classe A (papel {sorted(set(papeis) & declarantes)} "
                  f"DECLARA o campo). Revogar contraria a declaracao; use --forcar se for isso mesmo")
            continue

        # o que ele perde: deixa de ser master e passa a valer o guard de rank
        aviso = ""
        if ref is not None:
            tem = {f"{m}.{c}": v.get("access", "none")
                   for m, campos in cfg.items() for c, v in campos.items()
                   if not (m == modulo and c == campo)}
            falta = [k for k, v in ref.items() if RANK[v] > RANK.get(tem.get(k, "none"), 0)]
            aviso = (f" · apos a revogacao CONTRATA {a.simular_contratacao}"
                     if not falta else
                     f" · ⚠️ apos a revogacao NAO contrata {a.simular_contratacao} "
                     f"(falta {len(falta)}: {', '.join(sorted(falta)[:4])})")

        tocados += 1
        print(f"  {email} ({','.join(papeis)}) classe {classe}: {a.campo} {atual} -> REMOVIDO{aviso}")
        if a.aplicar:
            cfg[modulo].pop(campo, None)
            if not cfg[modulo]:
                cfg.pop(modulo, None)
            st, resp = call(base, f"/auth/users/{u['id']}/module-config", token,
                            data=cfg, method="PUT")
            print(f"      PUT -> {st}" + ("" if st == 200 else f" {str(resp)[:160]}"))
            if st != 200:
                return 1

    print(f"\n{tocados} usuario(s) " + ("revogado(s)" if a.aplicar else "seriam revogados"))
    if not a.aplicar and tocados:
        print("Nada foi escrito. Repita com --aplicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
