# -*- coding: utf-8 -*-
"""Falseia o ramo G: retipa AO VIVO uma tag que chega ao modelo, e restaura.

O ramo G afirma que nenhuma interpolacao de prompt carrega tipo que mascara. Provar
isso exige fazer uma carregar — e no caminho REAL (o censo le o catalogo vivo), nao
mexendo no proprio censo, que mediria o instrumento.

Restaura em `finally`. Se o restore falhar, o script GRITA: um catalogo deixado
mutado seria pior que o teste nao existir.
"""
import json
import subprocess
import sys
import urllib.request

CFG = "http://localhost:3600"
T = "tenant_demo"
ADMIN = "demo_config_admin_token"
ALVO = "core.sentiment.category"   # `texto` hoje, e chega ao `reason`
MUT = "credit_card"


def get():
    with urllib.request.urlopen(
            "%s/config/masking/context_map?tenant_id=%s" % (CFG, T), timeout=15) as r:
        return json.load(r)["value"]


def put(v):
    corpo = json.dumps({"tenant_id": None, "value": v}).encode()
    req = urllib.request.Request(
        "%s/config/masking/context_map" % CFG, data=corpo, method="PUT",
        headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def mexe(mapa, novo):
    """Troca o tipo do ALVO; devolve o tipo anterior."""
    anterior = [None]

    def anda(no, pref):
        if not isinstance(no, dict):
            return
        if isinstance(no.get("tipo"), str):
            if pref.endswith("." + ALVO) or pref == ALVO:
                anterior[0] = no["tipo"]
                no["tipo"] = novo
            return
        for k, v in no.items():
            anda(v, (pref + "." + k) if pref else k)

    anda(mapa, "")
    return anterior[0]


original = get()
antes = mexe(json.loads(json.dumps(original)), MUT)   # so para descobrir o tipo
if antes is None:
    sys.exit("ALVO %s nao esta no mapa — a mutacao nao descreve o parque" % ALVO)
print("alvo: %s  tipo atual=%s  ->  %s" % (ALVO, antes, MUT))

mutado = json.loads(json.dumps(original))
mexe(mutado, MUT)
put(mutado)

# ASSERT de aplicacao: reler, nunca confiar na copia local.
conf = get()
achou = [None]


def confere(no, pref):
    if not isinstance(no, dict):
        return
    if isinstance(no.get("tipo"), str):
        if pref.endswith("." + ALVO) or pref == ALVO:
            achou[0] = no["tipo"]
        return
    for k, v in no.items():
        confere(v, (pref + "." + k) if pref else k)


confere(conf, "")
if achou[0] != MUT:
    sys.exit("MUTACAO NAO APLICADA (leu %r)" % achou[0])
print("mutacao aplicada e conferida por releitura")

try:
    r = subprocess.run(["bash", "infra/test/probe_ctx_read_audience.sh"],
                       capture_output=True, text=True)
    linhas = [l for l in r.stdout.splitlines() if " G. " in l or l.startswith("REPROVADO")]
    for l in linhas:
        print("  " + l.strip())
    verde = (r.returncode == 0)
finally:
    put(original)
    conf2 = get()
    achou[0] = None
    confere(conf2, "")
    if achou[0] != antes:
        sys.exit("RESTORE FALHOU: %s esta %r, deveria estar %r — CONSERTE A MAO" % (ALVO, achou[0], antes))
    print("restaurado: %s de volta a %s" % (ALVO, antes))

print()
if verde:
    print("BATERIA REPROVADA — o ramo G ficou VERDE com tipo que mascara indo ao prompt")
    sys.exit(1)
print("BATERIA OK — o ramo G reprova quando a populacao da D5 deixa de ser zero")
