#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_masking_display_rule.py — `mascara.display` para o domínio abstrato (ALW-10).

── O que muda ───────────────────────────────────────────────────────────────────

    display_screen: "<v>"            →  token_display: "<v>"      (mesmos valores)
    display_voice:  "<qualquer>"     →  (sai; canal traduz o token_display)
    echo_to_customer: false | true   →  "none" | "masked"
    echo_to_operator: false | true   →  "none" | "masked"

⚠️ **A migração do booleano segue o COMPORTAMENTO, não o nome do campo.**
`echo_to_operator: true` lia-se *"ecoa"*, mas o que as casas fazem com ele é
`••••••` (`_MASKED_FIELD_PLACEHOLDER` no bridge, e o mesmo literal no webchat e no
Console). Mapear `true → "plain"` transformaria a política vigente num vazamento no
instante em que alguém ligasse o fio. `true` vira **`masked`**.

── Por que é script e não `seed.py` ─────────────────────────────────────────────

O seed é **seed-if-absent**: `masking.types` já existe, então ele pula. E o pulo é o
comportamento certo (o DB é a fonte depois do primeiro boot) — só que aqui a forma
do documento mudou, e um documento na forma velha não degrada com barulho: o Zod
descarta chave desconhecida e `token_display` cai no **default**. Para o tipo
`opaque`, cujo `display_screen` é `hidden`, isso seria um rebaixamento silencioso
para `display_partial`. Ou seja: não migrar não deixa nada vermelho, deixa uma folha
menos protegida.

── Por que escreve por ESCOPO, e não uma vez ────────────────────────────────────

A resolução do config-api é `LIMIT 1` com o tenant na frente: **override de tenant
vence o global POR INTEIRO**. Escrever só o `__global__` deixaria intacto qualquer
tenant com linha própria — e `tenant_demo` tem uma (medida em 2026-09-02: cópia
byte-idêntica do global, que não acrescenta nada e só engole edição futura).
Por isso o script ENUMERA os escopos por `/_provenance` em vez de supor um.

Uso:
    python3 infra/scripts/migrate_masking_display_rule.py            # relatório
    python3 infra/scripts/migrate_masking_display_rule.py --aplicar
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

CFG = os.environ.get("CONFIG_API", "http://localhost:3600")
TENANT = os.environ.get("TENANT", "tenant_demo")
ADMIN = os.environ.get("CONFIG_ADMIN_TOKEN", "demo_config_admin_token")
NS, KEY = "masking", "types"

CAMPOS_VELHOS = ("display_screen", "display_voice", "echo_to_customer", "echo_to_operator")
CAMPOS_NOVOS = ("token_display", "echo_to_customer", "echo_to_operator")


def _get(url: str):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def _put(tenant_id, valor) -> dict:
    corpo = json.dumps({"tenant_id": tenant_id, "value": valor}).encode()
    req = urllib.request.Request(
        f"{CFG}/config/{NS}/{KEY}", data=corpo, method="PUT",
        headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def migrar_display(d: dict) -> tuple[dict, list[str]]:
    """Devolve `(display_novo, mudancas)`. Idempotente: forma nova passa intacta."""
    mud: list[str] = []
    novo: dict = {k: v for k, v in d.items() if k not in CAMPOS_VELHOS}

    if "token_display" in d:
        novo["token_display"] = d["token_display"]
        # ⚠️ Os dois convivendo é forma híbrida, e ela EXISTE: um documento migrado
        # que alguém reeditou com a forma velha. `novo` já descarta o antigo, mas o
        # descarte tem de ser CONTADO — senão o script diz "0 mudanças" enquanto
        # remove um campo, e o gate de idempotência fica verde sobre store sujo.
        # Medido por mutação em 2026-09-02: era exatamente o que acontecia.
        if "display_screen" in d:
            mud.append("display_screen residual removido (era %s; `token_display` "
                       "já valia %s)" % (d["display_screen"], d["token_display"]))
    elif "display_screen" in d:
        novo["token_display"] = d["display_screen"]
        mud.append("display_screen→token_display=%s" % d["display_screen"])

    if "display_voice" in d:
        mud.append("display_voice removido (era %s)" % d["display_voice"])

    for campo in ("echo_to_customer", "echo_to_operator"):
        v = d.get(campo)
        if isinstance(v, str):
            novo[campo] = v                      # já migrado
        elif isinstance(v, bool):
            # COMPORTAMENTO, não nome: `true` ecoa `••••••`, logo `masked`.
            novo[campo] = "masked" if v else "none"
            mud.append("%s: %s→%s" % (campo, str(v).lower(), novo[campo]))
        elif v is not None:
            raise SystemExit("valor inesperado em %s: %r" % (campo, v))

    return novo, mud


def migrar_catalogo(doc: dict) -> tuple[dict, list[str]]:
    tipos = doc.get("types")
    if not isinstance(tipos, list):
        raise SystemExit("`types` não é lista — documento inesperado")
    saida, todas = [], []
    for t in tipos:
        t2 = dict(t)
        masc = t2.get("mascara")
        if isinstance(masc, dict) and isinstance(masc.get("display"), dict):
            novo, mud = migrar_display(masc["display"])
            if mud:
                todas.append("  %-18s %s" % (t2.get("id", "?"), " · ".join(mud)))
            t2["mascara"] = {**masc, "display": novo}
        saida.append(t2)
    return {**doc, "types": saida}, todas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="grava. Sem isto, só relata o que faria.")
    args = ap.parse_args()

    try:
        prov = _get(f"{CFG}/config/{NS}/_provenance?tenant_id={TENANT}")["keys"].get(KEY)
    except urllib.error.URLError as e:
        print("config-api inalcançável em %s: %s" % (CFG, e), file=sys.stderr)
        return 2
    if not prov:
        print("`%s.%s` não existe em escopo nenhum — nada a migrar." % (NS, KEY))
        return 0

    # Enumerar os escopos em vez de supor um: quem tem linha própria não é
    # alcançado por uma escrita no global.
    escopos: list[str | None] = []
    if prov["global_present"]:
        escopos.append(None)                 # None = `__global__` no contrato do PUT
    if prov["tenant_present"]:
        escopos.append(TENANT)
    print("escopos com linha própria de `%s.%s`: %s"
          % (NS, KEY, [e or "__global__" for e in escopos]))
    if prov["tenant_present"] and prov["diverges"] is False:
        print("  ⚠️  o override do tenant é IDÊNTICO ao global — sombra que não "
              "acrescenta nada. Migrado assim mesmo; removê-lo é outra decisão.")

    total_mud = 0
    for escopo in escopos:
        rotulo = escopo or "__global__"
        doc = _get(f"{CFG}/config/{NS}/{KEY}?tenant_id={rotulo}")["value"]
        novo, mudancas = migrar_catalogo(doc)
        print("\n── %s ── %d tipo(s), %d com mudança"
              % (rotulo, len(doc.get("types", [])), len(mudancas)))
        for linha in mudancas:
            print(linha)
        total_mud += len(mudancas)

        if not args.aplicar:
            continue
        if not mudancas:
            print("  (nada a gravar)")
            continue

        resp = _put(escopo, novo)
        sombra = resp.get("shadowed_by") or []
        if escopo is None and sombra:
            print("  escrita global NÃO alcança: %s (serão migrados à parte)" % sombra)

        # Verificação de EFEITO, no escopo, depois de gravar.
        depois = _get(f"{CFG}/config/{NS}/{KEY}?tenant_id={rotulo}")["value"]
        sobras = [
            t.get("id")
            for t in depois.get("types", [])
            if isinstance(t.get("mascara"), dict)
            and isinstance(t["mascara"].get("display"), dict)
            and any(c in t["mascara"]["display"] for c in ("display_screen", "display_voice"))
        ]
        if sobras:
            print("  ✗ ainda na forma velha: %s" % sobras, file=sys.stderr)
            return 1
        booleanos = [
            t.get("id")
            for t in depois.get("types", [])
            if isinstance(t.get("mascara"), dict)
            and isinstance(t["mascara"].get("display"), dict)
            and any(isinstance(t["mascara"]["display"].get(c), bool)
                    for c in ("echo_to_customer", "echo_to_operator"))
        ]
        if booleanos:
            print("  ✗ eco ainda booleano: %s" % booleanos, file=sys.stderr)
            return 1
        print("  ✓ gravado e conferido no escopo %s" % rotulo)

    if not args.aplicar:
        print("\n%d mudança(s) previstas. Rode com --aplicar para gravar." % total_mud)
    return 0


if __name__ == "__main__":
    sys.exit(main())
