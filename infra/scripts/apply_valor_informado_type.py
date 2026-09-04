# -*- coding: utf-8 -*-
"""Desfaz o override de tenant que eu criei e aplica tudo no escopo GLOBAL.

── Por que o escopo importa aqui, e nao e detalhe de chamada ────────────────────

`masking.context_map` tem guard que RECUSA override de tenant (`router.py:311`), com
a razao escrita: a resolucao e tenant-vence-global POR INTEIRO, entao um override
substitui as 94 folhas da plataforma pelo que o tenant mandou — medido em 2026-09-01,
um PUT com uma folha deixou o tenant com 1 no lugar de 94.

`masking.types` e a chave IRMA e **nao tem o guard**. Escrevi nela com
`tenant_id=tenant_demo` e criei exatamente o override que o guard vizinho impede:
global com 14 tipos, tenant congelado em 15 e desligado da plataforma. Este script
desfaz.

Registrado como divida: o guard cobre uma chave de um par que tem o mesmo modo de
falha (CNS-17).
"""
import json
import os
import sys
import urllib.request

CFG = os.environ.get("CONFIG_API_URL", "http://localhost:3600")
ADMIN = os.environ.get("CONFIG_ADMIN_TOKEN", "demo_config_admin_token")
GLOBAL = "__global__"
TENANT = "tenant_demo"

RETIPAR = {
    "session.cartao.limite_aprovado":   "valor_informado_ao_cliente",
    "journey.cartao.limite_aprovado":   "valor_informado_ao_cliente",
    "session.cartao.limite_solicitado": "valor_declarado_pelo_cliente",
}


def get(key, escopo):
    with urllib.request.urlopen(
            "%s/config/masking/%s?tenant_id=%s" % (CFG, key, escopo), timeout=15) as r:
        return json.load(r)


def get_prov():
    """Proveniencia por chave: diz QUAL escopo respondeu, que o GET nao diz."""
    with urllib.request.urlopen(
            "%s/config/masking/_provenance?tenant_id=%s" % (CFG, TENANT), timeout=15) as r:
        return json.load(r)


def put(key, valor, escopo):
    corpo = json.dumps({"tenant_id": None if escopo == GLOBAL else escopo,
                        "value": valor}).encode()
    req = urllib.request.Request(
        "%s/config/masking/%s" % (CFG, key), data=corpo, method="PUT",
        headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit("PUT %s@%s recusado (%s): %s" % (key, escopo, e.code, e.read().decode()[:600]))


def delete(key, escopo):
    req = urllib.request.Request(
        "%s/config/masking/%s?tenant_id=%s" % (CFG, key, escopo), method="DELETE",
        headers={"X-Admin-Token": ADMIN})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        sys.exit("DELETE %s@%s recusado (%s): %s" % (key, escopo, e.code, e.read().decode()[:400]))


# ── 1. o tipo novo vai para o GLOBAL, que e onde os outros 14 moram ──────────
g = get("types", GLOBAL)["value"]
ids = [t.get("id") for t in g.get("types", [])]
if "valor_informado_ao_cliente" in ids:
    print("types@global: ja tem o tipo (%d)" % len(ids))
else:
    g["types"].append({
        "id": "valor_informado_ao_cliente",
        "label": "Valor que a empresa informa ao cliente (limite, saldo, extrato)",
        "icon": "\U0001f4b0",
        "formato": {},
        "mascara": {"by_role": {}},
        "lgpd": "financeiro",
        "declared_only": True,
    })
    put("types", g, GLOBAL)
    print("types@global: %d -> %d" % (len(ids), len(ids) + 1))

# ── 2. remove o override de tenant que eu criei ──────────────────────────────
# Mesma armadilha do  ecoado: quem sabe se HA override e a proveniencia.
if get_prov()["keys"]["types"].get("tenant_present"):
    delete("types", TENANT)
    print("types@tenant: override REMOVIDO")
else:
    print("types@tenant: nao ha override — nada a desfazer")

# ── 3. o mapa, no escopo que o guard exige ───────────────────────────────────
mapa = get("context_map", GLOBAL)["value"]
mudou = []


def anda(no, pref):
    if not isinstance(no, dict):
        return
    if isinstance(no.get("tipo"), str):
        alvo = next((v for k, v in RETIPAR.items()
                     if pref == k or pref.endswith("." + k)), None)
        if alvo and no["tipo"] != alvo:
            mudou.append("%s: %s -> %s" % (pref, no["tipo"], alvo))
            no["tipo"] = alvo
        return
    for k, v in no.items():
        anda(v, (pref + "." + k) if pref else k)


anda(mapa, "")
if mudou:
    put("context_map", mapa, GLOBAL)
    for m in mudou:
        print("mapa@global: " + m)
else:
    print("mapa@global: nada a retipar")

# ── 4. conferencia por RELEITURA, na resolucao que o runtime usa ─────────────
# ⚠️ O `tenant_id` da resposta do GET ecoa o que foi PEDIDO, nao o escopo que
# respondeu — a primeira versao desta conferencia o leu como escopo e acusou um
# override que ja tinha sido removido. Quem responde a pergunta certa e
# `_provenance`, que separa `global_present` de `tenant_present`.
prov = get_prov()["keys"]["types"]
if prov.get("effective_scope") != "global" or prov.get("tenant_present"):
    sys.exit("CONFERENCIA: types resolve por %r (tenant_present=%r), esperado global"
             % (prov.get("effective_scope"), prov.get("tenant_present")))

res = get("types", TENANT)
ids_r = [t.get("id") for t in (res.get("value") or {}).get("types", [])]
if "valor_informado_ao_cliente" not in ids_r:
    sys.exit("CONFERENCIA: tipo ausente na resolucao do tenant")

achado = {}


def confere(no, pref):
    if not isinstance(no, dict):
        return
    if isinstance(no.get("tipo"), str):
        for k in RETIPAR:
            if pref == k or pref.endswith("." + k):
                achado[k] = no["tipo"]
        return
    for k, v in no.items():
        confere(v, (pref + "." + k) if pref else k)


confere(get("context_map", TENANT)["value"], "")
for tag, esp in RETIPAR.items():
    if achado.get(tag) != esp:
        sys.exit("CONFERENCIA: %s = %r, esperado %r" % (tag, achado.get(tag), esp))

print("conferencia OK: types resolve do __global__ com %d tipos; as %d tags no alvo"
      % (len(ids_r), len(RETIPAR)))
