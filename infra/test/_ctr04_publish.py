# -*- coding: utf-8 -*-
"""Publica + deploya os 4 executores da CTR-04 (metade do RETORNO).

DUAS ETAPAS, e nenhuma basta sozinha:
  publish  PUT /v1/skills/:id com x-skill-publish:true -- editar o YAML de skill
           ja semeado e NO-OP (seed-if-absent), entao sem isto nada muda
  deploy   set-next -> promote -- o bridge executa o SNAPSHOT do slot `current`,
           entao publicar ainda nao faz rodar

⚠️ O set-next reenvia o `config_json` do slot CURRENT. Mandar so o `skill_id`
derrubaria a config do deploy (ja aconteceu nesta base: `max_concurrent_sessions`
de um pool foi perdido assim). O config e reenviado, nunca omitido.
"""
import io
import json
import sys
import urllib.error
import urllib.request

import yaml

REG = "http://localhost:3300"
H = {"x-tenant-id": "tenant_demo",
     "x-service-token": "changeme_agent_registry_service_token_demo",
     "content-type": "application/json"}

# (arquivo, skill_id, pool)
ALVOS = [
    ("packages/skill-flow-engine/skills/agente_reembolso_intake_v1.yaml",
     "skill_reembolso_intake_v1", "reembolso_ia"),
    ("packages/skill-flow-engine/skills/skill_atendimento_auth_v1.yaml",
     "skill_atendimento_auth_v1", "auth_sac_ia"),
    ("packages/skill-flow-engine/skills/agente_auth_form_v1.yaml",
     "skill_auth_form_v1", "auth_form_ia"),
    ("packages/skill-flow-engine/skills/skill_atendimento_sac_v1.yaml",
     "skill_atendimento_sac_v1", "sac_ia"),
]


def req(metodo, url, corpo=None, extra=None):
    cab = dict(H)
    if extra:
        cab.update(extra)
    dados = json.dumps(corpo).encode() if corpo is not None else None
    r = urllib.request.Request(url, data=dados, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(r) as resp:
            txt = resp.read().decode()
            return resp.status, (json.loads(txt) if txt else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


falhas = 0
for arq, skill_id, pool in ALVOS:
    raw = yaml.safe_load(io.open(arq, encoding="utf-8").read())

    # ── 1. publish ───────────────────────────────────────────────────────────
    # O envelope e o MESMO que o RegistrySyncer monta (registry_syncer.py §662):
    # `flow` carrega entry+steps (+required_context/mention_commands), e os campos
    # de metadado ficam no topo. Mandar o YAML cru da 422 `invalid_shape`.
    flow = {"entry": raw["entry"], "steps": raw["steps"]}
    if raw.get("required_context"):
        flow["required_context"] = raw["required_context"]
    if raw.get("mention_commands"):
        flow["mention_commands"] = raw["mention_commands"]
    doc = {
        "skill_id":       skill_id,
        "name":           raw.get("name", skill_id),
        "version":        raw.get("version", "1.0"),
        "description":    (raw.get("description") or raw.get("name") or skill_id).strip(),
        "classification": raw.get("classification", {"type": "orchestrator"}),
        "flow":           flow,
    }
    if raw.get("delegation_input"):
        doc["delegation_input"] = raw["delegation_input"]
    if raw.get("config_params"):
        doc["config_params"] = raw["config_params"]

    st, corpo = req("PUT", "%s/v1/skills/%s" % (REG, skill_id), doc,
                    {"x-skill-publish": "true"})
    if st not in (200, 201):
        print("%-28s PUBLISH FALHOU  [%s] %s" % (skill_id, st, corpo))
        falhas += 1
        continue

    # ── 2. set-next, REENVIANDO o config do current ──────────────────────────
    slots = (req("GET", "%s/v1/pools/%s/slots" % (REG, pool))[1].get("slots") or {})
    cur = slots.get("current") or {}
    cfg = cur.get("config_json") or {}

    st, corpo = req("PUT", "%s/v1/pools/%s/slots/next" % (REG, pool),
                    {"skill_id": skill_id, "config_json": cfg})
    if st not in (200, 201):
        print("%-28s SET-NEXT FALHOU [%s] %s" % (skill_id, st, corpo))
        falhas += 1
        continue

    # ── 3. promote ───────────────────────────────────────────────────────────
    st, corpo = req("POST", "%s/v1/pools/%s/promote" % (REG, pool))
    if st not in (200, 201):
        print("%-28s PROMOTE FALHOU  [%s] %s" % (skill_id, st, corpo))
        falhas += 1
        continue

    # ── 4. CONFERE o que ficou vivo (nunca confiar no 200) ───────────────────
    novo = ((req("GET", "%s/v1/pools/%s/slots" % (REG, pool))[1].get("slots") or {})
            .get("current") or {})
    snap = novo.get("yaml_snapshot") or {}
    passos = [x for x in (snap.get("steps") or []) if isinstance(x, dict)]
    devolve = sum(1 for x in passos if x.get("tool") == "workflow_resume")
    cfg_novo = novo.get("config_json") or {}
    perdeu = sorted(set(cfg.keys()) - set(cfg_novo.keys()))

    print("%-28s OK  pool=%-16s steps=%-3d devolucoes=%d%s" % (
        skill_id, pool, len(passos), devolve,
        ("  CONFIG PERDIDA: " + ",".join(perdeu)) if perdeu else ""))
    if devolve == 0:
        print("     ⚠️ o snapshot VIVO nao invoca workflow_resume — publish nao pegou")
        falhas += 1
    if perdeu:
        falhas += 1

print()
print("FALHAS: %d de %d" % (falhas, len(ALVOS)))
sys.exit(1 if falhas else 0)
