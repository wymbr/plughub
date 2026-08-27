#!/usr/bin/env python3
"""
q_nav_gates_matrix.py — quem ve o que no menu, e POR QUE.

O menu do platform-ui tem DOIS mecanismos empilhados:
  · `roles: [...]`  — portao de PAPEL no grupo (grosso)
  · `abac: {...}`   — portao ABAC por item (fino)

e um TERCEIRO, invisivel, dentro de `passesAbacRule`: no ramo NAO-estrito, papel
`admin` ou `supervisor` passa por cima do ABAC. Isso contradiz a decisao do dono de
2026-08-26 ("o admin RESPEITA a ABAC como qualquer um. Nao ha bypass por papel").

Este script NAO decide nada — mede, para que a decisao seja tomada com numero:

  §1  os portoes de PAPEL que restam, derivados do fonte
  §2  os usuarios e o que o `module_config` deles concede
  §3  por item de menu: quem ve, e por qual RAZAO (grant · bypass de papel ·
      degradacao de conta legada)
  §4  o placar do bypass: quantos itens cada usuario ve APENAS por ele

As regras sao DERIVADAS de `Sidebar.tsx` por regex, nunca copiadas: uma copia aqui
divergiria do menu no primeiro ajuste, e o relatorio ficaria confiante e errado.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIDEBAR = ROOT / "packages/platform-ui/src/shell/Sidebar.tsx"
AUTH = os.environ.get("AUTH", "http://localhost:3202/auth")
TENANT = os.environ.get("TENANT", "tenant_demo")
EMAIL = os.environ.get("PLUGHUB_TEST_EMAIL", "admin@plughub.local")
PASSWD = os.environ.get("PLUGHUB_TEST_PASS", "changeme_admin")

# `read_only`/`write_only` colapsam em 1; `read_write` = 2 (espelha PermissionChecker)
ACCESS_ORDER = {"none": 0, "read_only": 1, "write_only": 1, "read_write": 2}


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


def api(path: str, token: str) -> object:
    out = sh("curl", "-s", "--max-time", "20", f"{AUTH}{path}",
             "-H", f"Authorization: Bearer {token}")
    try:
        return json.loads(out)
    except Exception:
        return None


def login() -> str:
    out = sh("curl", "-s", "-X", "POST", f"{AUTH}/login",
             "-H", "content-type: application/json",
             "-d", json.dumps({"email": EMAIL, "password": PASSWD, "tenant_id": TENANT}))
    try:
        return json.loads(out).get("access_token", "")
    except Exception:
        return ""


def parse_sidebar() -> tuple[list[dict], list[dict]]:
    """Devolve (itens_com_abac, portoes_de_papel), derivados do fonte."""
    src = SIDEBAR.read_text(encoding="utf-8")
    items: list[dict] = []
    # ⚠️ NAO usar `label: ... .*? abac:` num `re.S` solto: o `.*?` atravessa a
    # fronteira da entrada e casa o `abac:` do item SEGUINTE. Medido em 2026-08-27:
    # `nav.billing` (que tem `roles:`, nao `abac:`) aparecia associado ao
    # `config.users` do `nav.access` logo abaixo — o relatorio inventava uma regra.
    #
    # A guarda: entre o label e o `abac:` nao pode haver OUTRO `label:`. Assim a
    # associacao morre na fronteira da entrada, que e o que "mesma entrada" significa.
    for m in re.finditer(
        r"label:\s*t\('([^']+)'\)((?:(?!label:).)*?)abac:\s*\{([^}]*)\}", src, re.S
    ):
        label, body = m.group(1), m.group(3)
        mod = re.search(r"module:\s*'([^']+)'", body)
        fld = re.search(r"field:\s*'([^']+)'", body)
        anyof = re.search(r"anyOf:\s*\[([^\]]*)\]", body)
        strict = "strict: true" in body
        if not mod:
            continue
        items.append({
            "label": label,
            "module": mod.group(1),
            "field": fld.group(1) if fld else None,
            "anyOf": re.findall(r"'([^']+)'", anyof.group(1)) if anyof else [],
            "strict": strict,
        })
    roles_gates = [
        {"line": src[:m.start()].count("\n") + 1,
         "roles": re.findall(r"'([^']+)'", m.group(1))}
        for m in re.finditer(r"roles:\s*\[([^\]]*)\]", src)
    ]
    return items, roles_gates


def can(mc: dict, module: str, field: str, minimum: str = "read_only") -> bool:
    f = (mc.get(module) or {}).get(field) or {}
    acc = f.get("access", "none") if isinstance(f, dict) else "none"
    return ACCESS_ORDER.get(acc, 0) >= ACCESS_ORDER.get(minimum, 1)


def grants(mc: dict, it: dict) -> bool:
    if it["anyOf"]:
        return any(can(mc, it["module"], f) for f in it["anyOf"])
    return can(mc, it["module"], it["field"]) if it["field"] else True


def hr(t: str) -> None:
    print(f"\n\033[1m── {t} " + "─" * max(0, 68 - len(t)) + "\033[0m")


def main() -> int:
    if not SIDEBAR.exists():
        print(f"INCONCLUSIVO: {SIDEBAR} nao encontrado", file=sys.stderr)
        return 2
    items, roles_gates = parse_sidebar()
    if not items:
        print("INCONCLUSIVO: nenhuma regra `abac:` extraida do Sidebar.tsx — o regex",
              "provavelmente deixou de casar. Um relatorio vazio aqui seria lido como",
              "'nao ha regras', que e o oposto da verdade.", file=sys.stderr)
        return 2

    tok = login()
    if not tok:
        print(f"INCONCLUSIVO: login falhou para {EMAIL} em {AUTH}", file=sys.stderr)
        return 2
    users = api(f"/users?tenant_id={TENANT}", tok)
    if not isinstance(users, list) or not users:
        print("INCONCLUSIVO: nao consegui listar usuarios", file=sys.stderr)
        return 2

    # module_config vem por endpoint proprio
    for u in users:
        mc = api(f"/users/{u['id']}/module-config", tok)
        u["_mc"] = mc if isinstance(mc, dict) else {}

    print(f"\033[1mq_nav_gates_matrix\033[0m — {len(items)} itens com ABAC · "
          f"{len(roles_gates)} portoes de papel · {len(users)} usuarios")

    hr("1. PORTOES DE PAPEL que restam (derivados do fonte)")
    for g in roles_gates:
        print(f"   Sidebar.tsx:{g['line']:<4}  roles = {', '.join(g['roles'])}")
    print("\n   Um portao de papel e um SEGUNDO mecanismo ao lado do ABAC. Enquanto")
    print("   existir, remover so um dos dois nao muda o que a pessoa ve.")

    hr("2. USUARIOS e o que o module_config concede")
    for u in sorted(users, key=lambda x: x["email"]):
        mc = u["_mc"]
        n = sum(len(v) for v in mc.values() if isinstance(v, dict))
        print(f"   {u['email']:<28} roles={','.join(u['roles']):<20} "
              f"modulos={len(mc):<3} campos={n}")

    hr("3. POR ITEM: quem ve, e por QUAL razao")
    print("   grant   = o module_config concede de fato")
    print("   BYPASS  = passa APENAS porque o papel e admin/supervisor (ramo nao-estrito)")
    print("   legado  = passa porque o module_config esta VAZIO (degradacao graciosa)")
    print("   -       = nao ve\n")
    hdr = f"   {'item':<34} {'modulo.campo':<28} "
    emails = [u["email"].split("@")[0] for u in sorted(users, key=lambda x: x["email"])]
    print(hdr + " ".join(f"{e[:10]:<11}" for e in emails))

    bypass_count = {u["email"]: 0 for u in users}
    for it in items:
        alvo = it["field"] or ("anyOf:" + "|".join(it["anyOf"]))
        cells = []
        for u in sorted(users, key=lambda x: x["email"]):
            mc, role = u["_mc"], (u["roles"] or [""])[0]
            g = grants(mc, it)
            if g:
                cells.append("grant")
            elif it["strict"]:
                cells.append("-")
            elif not mc:
                cells.append("legado")
            elif role in ("admin", "supervisor"):
                cells.append("BYPASS")
                bypass_count[u["email"]] += 1
            else:
                cells.append("-")
        flag = " [strict]" if it["strict"] else ""
        print(f"   {it['label'][:33]:<34} {it['module'] + '.' + alvo:<28.28} "
              + " ".join(f"{c:<11}" for c in cells) + flag)

    hr("4. PLACAR DO BYPASS de papel")
    nao_estritos = sum(1 for i in items if not i["strict"])
    print(f"   itens NAO-estritos (sujeitos ao bypass): {nao_estritos} de {len(items)}\n")
    for email, n in sorted(bypass_count.items()):
        if n:
            print(f"   \033[33m{email:<28} ve {n} item(ns) SO pelo bypass de papel\033[0m")
        else:
            print(f"   {email:<28} 0 — nao depende do bypass")
    print("\n   Zero em todos = o bypass pode cair sem tirar nada de ninguem, e a")
    print("   decisao do dono ('nao ha bypass por papel') vira `strict: true` em bloco.")
    print("   Numero > 0 = conceder o grant ANTES, senao a remocao tira tela de quem usa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
