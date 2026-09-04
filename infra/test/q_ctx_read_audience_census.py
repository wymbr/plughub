# -*- coding: utf-8 -*-
# Censo manual da §1 do adr-context-read-audience-policy (2026-09-04). Vira gate na CTX-01.
"""Quantas interpolacoes existem, e quantas resolvem para um tipo que ja PROIBE eco ao cliente."""
import glob
import io
import json
import re

mapa = json.load(io.open("/tmp/cm.json", encoding="utf-8")).get("value") or {}
tipos = {t["id"]: t for t in json.load(io.open("/tmp/tipos.json", encoding="utf-8"))["value"]["types"]}

# tag (canonica e legado) -> tipo
tag2tipo = {}
def anda(no, cam):
    if not isinstance(no, dict):
        return
    if "tipo" in no and isinstance(no.get("tipo"), str):
        canon = ".".join(cam[1:]) if cam and cam[0] == "contexto" else ".".join(cam)
        tag2tipo[canon] = no["tipo"]
        for a in no.get("legado") or []:
            tag2tipo[a] = no["tipo"]
        return
    for k, x in no.items():
        anda(x, cam + [k])
anda(mapa, [])

CTX = re.compile(r"@ctx\.([a-zA-Z0-9_.]+)")
PS  = re.compile(r"\$\.pipeline_state\.([a-zA-Z0-9_.]+)")

ctx_total = ps_total = 0
sensiveis = []
for f in sorted(glob.glob("packages/skill-flow-engine/skills/*.yaml")):
    t = io.open(f, encoding="utf-8").read()
    for m in CTX.finditer(t):
        ctx_total += 1
        tag = m.group(1).rstrip(".")
        tp = tag2tipo.get(tag)
        if tp and tipos.get(tp, {}).get("mascara", {}).get("display", {}).get("echo_to_customer") == "none":
            sensiveis.append((f.split("/")[-1], "@ctx." + tag, tp))
    ps_total += len(PS.findall(t))

print("interpolacoes @ctx.*            :", ctx_total)
print("interpolacoes $.pipeline_state.*:", ps_total)
print("tags tipadas no mapa (c/ legado):", len(tag2tipo))
print()
print("@ctx que resolvem para tipo com echo_to_customer=none:", len(sensiveis))
for f, tag, tp in sensiveis:
    print("   %-32s %-34s %s" % (f, tag, tp))
