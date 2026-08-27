#!/usr/bin/env python3
"""
q_nav_gates_matrix.py — quem ve o que no menu, e POR QUE.

O menu do platform-ui tem DOIS mecanismos empilhados:
  · `roles: [...]`  — portao de PAPEL no grupo (grosso)
  · `abac: {...}`   — portao ABAC por item (fino)

⚠️ ATUALIZADO NO PASSO 5 (2026-08-27). Ate aqui havia TRES mecanismos: o `roles:` do
Sidebar, o `abac:` por item, e um terceiro invisivel dentro de `passesAbacRule` — o
ramo nao-estrito, que liberava para papel admin/supervisor E para `module_config`
vazio. Os tres cairam:

  · os 7 `roles:` sairam do Sidebar (5 eram cabecalho de grupo, ja derivavel; 1 era o
    nav.home; 1 virou grant em `nav.billing`);
  · o ramo nao-estrito saiu INTEIRO de `passesAbacRule` — nao virou flag por regra,
    porque flag esquecida numa entrada nova reabriria os dois bypasses em silencio.

Sobrou UM portao (o grant) e UMA porta larga, DECLARADA: `unrestricted`.

Este script NAO decide nada — mede, para que a decisao seja tomada com numero:

  §1  os portoes de PAPEL que restam, derivados do fonte
  §2  os usuarios e o que o `module_config` deles concede
  §3  por item de menu: quem ve, e por qual RAZAO (grant · bypass de papel ·
      degradacao de conta legada)
  §4  o placar do bypass: quantos itens cada usuario ve APENAS por ele — e a
      pergunta a fazer sobre cada um (o alvo NAO e zero; ver a nota no §4)

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
    # ⚠️ A associacao e do `abac:` para o label ANTERIOR mais proximo — nunca do
    # label para o `abac:` seguinte. Duas correcoes, ambas medidas em 2026-08-27:
    #
    #  (a) para FRENTE, o `.*?` com `re.S` atravessava a fronteira da entrada:
    #      `nav.billing` (que tem `roles:` e nao `abac:`) aparecia associado ao
    #      `config.users` do `nav.access` logo abaixo — regra inventada.
    #  (b) restringir com "nao pode haver outro label no meio" nao bastava: num
    #      GRUPO com filhos (`{ label: G, children: [{ label: F, abac: A }] }`) o
    #      label do grupo vem antes e absorvia o `abac:` do PRIMEIRO filho — o
    #      filho sumia da tabela e o grupo ganhava uma regra que nao e dele.
    #
    # De tras para frente as duas somem: o label anterior mais proximo de um `abac:`
    # e, por construcao da estrutura, o dono dele.
    for m in re.finditer(r"abac:\s*\{([^}]*)\}", src):
        body = m.group(1)
        antes = src[:m.start()]
        lab = None
        for lm in re.finditer(r"label:\s*t\('([^']+)'\)", antes):
            lab = lm.group(1)
        if lab is None:
            continue
        label = lab
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
    # ⚠️ Contar `roles:` por regex casa tambem as ocorrencias dentro de COMENTARIO.
    # Medido em 2026-08-27: havia um `roles:` citado num comentario de historico, e o
    # §1 relatava 8 portoes onde ha 7. Linha comentada nao e portao.
    roles_gates = []
    for m in re.finditer(r"roles:\s*\[([^\]]*)\]", src):
        ini = src.rfind("\n", 0, m.start()) + 1
        if src[ini:m.start()].lstrip().startswith("//"):
            continue
        roles_gates.append({"line": src[:m.start()].count("\n") + 1,
                            "roles": re.findall(r"'([^']+)'", m.group(1))})

    # ── portao de papel do GRUPO que contem cada item ────────────────────────
    # O menu filtra em dois niveis (`Sidebar.tsx:224`): o `roles:` do grupo decide
    # ANTES do `abac:` do filho. Um filho com grant perfeito fica invisivel se o
    # cabecalho barrar o papel — e a matriz precisa dizer isso, senao ela responde
    # "o grant passa?" quando a pergunta e "a pessoa ve?".
    for it in items:
        pos = src.find("label: t('%s')" % it["label"])
        grupo = None
        if pos > 0:
            # o `roles:` de grupo mais proximo ANTES do item, dentro do mesmo bloco
            for gm in re.finditer(r"^      roles:\s*\[([^\]]*)\]", src[:pos], re.M):
                grupo = re.findall(r"'([^']+)'", gm.group(1))
            # ...mas so vale se nao houve outro `navKey:` entre ele e o item
            ultimo_grupo = src[:pos].rfind("navKey:")
            ultimo_roles = src[:pos].rfind("\n      roles:")
            if ultimo_roles < ultimo_grupo:
                grupo = None
        it["group_roles"] = grupo
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
    if not roles_gates:
        print("   \033[32m(nenhum)\033[0m — o menu nao tem mais portao de papel.")
    print("\n   Um portao de papel e um SEGUNDO mecanismo ao lado do ABAC. Enquanto")
    print("   existir, remover so um dos dois nao muda o que a pessoa ve — foi assim")
    print("   que os grants do supervisor ficaram inertes ate o passo 5.")

    hr("2. USUARIOS e o que o module_config concede")
    for u in sorted(users, key=lambda x: x["email"]):
        mc = u["_mc"]
        n = sum(len(v) for v in mc.values() if isinstance(v, dict))
        print(f"   {u['email']:<28} roles={','.join(u['roles']):<20} "
              f"modulos={len(mc):<3} campos={n}")

    hr("3. POR ITEM: quem ve, e por QUAL razao")
    print("   grant   = o module_config concede de fato")
    print("   IRREST  = passa pelo claim `unrestricted` (a unica porta larga que restou)")
    print("   gr-BLOQ = o GRANT existe e e INERTE: um `roles:` de cabecalho de grupo")
    print("             barra o papel antes de a ABAC ser consultada. NAO deveria mais")
    print("             existir depois do passo 5 — se aparecer, e regressao.")
    print("   -       = nao ve\n")
    hdr = f"   {'item':<34} {'modulo.campo':<28} "
    emails = [u["email"].split("@")[0] for u in sorted(users, key=lambda x: x["email"])]
    print(hdr + " ".join(f"{e[:10]:<11}" for e in emails))

    bypass_count = {u["email"]: 0 for u in users}
    bloq_count: dict[str, int] = {}
    irrest_count: dict[str, int] = {}
    for it in items:
        alvo = it["field"] or ("anyOf:" + "|".join(it["anyOf"]))
        cells = []
        for u in sorted(users, key=lambda x: x["email"]):
            mc, role = u["_mc"], (u["roles"] or [""])[0]
            gr = it.get("group_roles")
            if gr and role not in gr:
                # O cabecalho do grupo barra o PAPEL — o grant do filho e inerte.
                cells.append("gr-BLOQ")
                bloq_count[u["email"]] = bloq_count.get(u["email"], 0) + 1
                continue
            if grants(mc, it):
                cells.append("grant")
            elif u.get("unrestricted") is True:
                # Espelha `passesAbacRule`: o claim vem ANTES da regra, e e a unica
                # forma de ver um item sem ter o grant dele.
                cells.append("IRREST")
                irrest_count[u["email"]] = irrest_count.get(u["email"], 0) + 1
            else:
                cells.append("-")
        flag = ""
        print(f"   {it['label'][:33]:<34} {it['module'] + '.' + alvo:<28.28} "
              + " ".join(f"{c:<11}" for c in cells) + flag)

    if bloq_count:
        hr("3b. GRANTS INERTES — barrados pelo portao de papel do GRUPO")
        for email, n in sorted(bloq_count.items()):
            print(f"   \033[33m{email:<28} tem grant em {n} item(ns) que NAO ve\033[0m")
        print("\n   Enquanto o cabecalho do grupo tiver `roles:`, conceder o campo do filho")
        print("   nao muda o que a pessoa ve. Os dois mecanismos tem de cair juntos.")

    hr("4. QUEM DEPENDE DA PORTA LARGA (`unrestricted`)")
    print(f"   itens com regra ABAC: {len(items)} — todos grant-first\n")
    for u in sorted(users, key=lambda x: x["email"]):
        email = u["email"]
        n = irrest_count.get(email, 0)
        if n:
            print(f"   \033[33m{email:<28} ve {n} item(ns) SO pelo claim unrestricted\033[0m")
        else:
            print(f"   {email:<28} 0 — tudo o que ve, ve por grant")
    print("\n   \033[1mComo ler\033[0m: `unrestricted` e DECLARADO por usuario, entao um numero")
    print("   alto aqui nao e defeito — e a definicao do principal. O que seria defeito e")
    print("   um principal OPERACIONAL aparecendo nesta lista: significaria que ele ve por")
    print("   ausencia de recorte, nao por permissao.")
    print("\n   Historico: ate o passo 5 esta secao contava o BYPASS DE PAPEL. O criterio")
    print("   de prontidao que ela trazia ('tem de ir a zero') estava errado e foi")
    print("   corrigido no passo 4 — depois de conceder tudo o que o dono decidiu, o")
    print("   supervisor ficou em 8, e os 8 eram justamente os que ele NAO deve alcancar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
