#!/usr/bin/env python3
"""_portabilidade_door_census.py — PID-17 (2026-09-14)

Censo do fonte para `probe_portabilidade_door.sh`. Recebe a RAIZ a medir (o repo, ou a
cópia mutada do probe) e imprime UM JSON de fatos. Os fatos da POPULAÇÃO de portas
(config obrigatória, nós do roteiro, intakes antigos) moram no censo do runner
(`_intake_runner_census.py`); aqui ficam os do DOMÍNIO da portabilidade.

  porta_runner             o seed do `portabilidade_ia` roda `skill_intake_runner_v1`
  ancora_linha             a config da porta identifica e prova pela LINHA (phone/phone)
  form_sem_numero          o formulário do pedido NÃO tem campo de número (`numero_atual`,
                           `phone`): o número é a âncora, nunca um campo
  numero_da_ancora         o processo grava `journey.numero_atual` a partir de
                           `@ctx.session.phone` (a âncora que o gatilho escreve por último)
  ancora_da_pendencia      o `delegate` do processo passa `phone: @ctx.session.phone` e NÃO
                           passa `contact_identifier` — as duas chaves são lidas como âncora
                           da pendência, e a segunda veio do formulário
  confirmacao_le_journey   o agente mostra número e operadora lidos da journey
  confirmacao_le_sessao    o agente ainda lê `@ctx.session.numero_atual|operadora_destino`
  cancelar_rejected        a resposta `cancelar` retoma o processo com `decision: rejected`
  ocioso_preserva          timeout e desconexão, com o cliente presente, devolvem à porta
                           pelo token do CHAMADOR e não retomam o processo
  alias_plataforma         o mapa padrão (TS e gêmeo Python) declara `session.phone` e
                           `session.email` como aliases — sem isso o cadastro recusa o
                           `delegate` que passa a âncora
"""
import io
import json
import os
import re
import sys

import yaml

raiz = sys.argv[1] if len(sys.argv) > 1 else "."
SK = "packages/skill-flow-engine/skills"


def ler(rel):
    try:
        with io.open(os.path.join(raiz, rel), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def yml(rel):
    t = ler(rel)
    return yaml.safe_load(t) if t else None


seed = yml("infra/registry/tenant_demo.yaml") or {}
pool = next((p for p in seed.get("pools") or [] if p.get("pool_id") == "portabilidade_ia"), {})
dep = pool.get("deploy") or {}
cfg = dep.get("config") or {}

form = json.loads(ler("infra/dialog/%s.json" % cfg.get("new_form_id", "")) or "{}")
campos = [f.get("id") for n in form.get("nodes", []) for f in (n.get("fields") or [])]

n3 = yml(SK + "/skill_portabilidade_demo_v1.yaml") or {}
passos = {s.get("id"): s for s in n3.get("steps") or []}
numero = next((s for s in passos.values()
               if (s.get("input") or {}).get("tag") in ("journey.numero_atual", "journey.portabilidade.numero_atual")), {})
deleg = next((s for s in passos.values() if s.get("type") == "delegate"), {})
dctx = deleg.get("context") or {}

ag_txt = ler(SK + "/agente_confirmacao_portabilidade_v1.yaml") or ""
ag = yaml.safe_load(ag_txt) if ag_txt else {}
ap = {s.get("id"): s for s in ag.get("steps") or []}


def destino_de(escolha_id, valor):
    for c in (ap.get(escolha_id) or {}).get("conditions") or []:
        if c.get("value") == valor:
            return c.get("next")
    return None


resp = ap.get("coletar_confirmacao") or {}
cancel = ap.get(destino_de(resp.get("on_success"), "cancelar")) or {}
cancelar_rejected = (cancel.get("tool") == "workflow_resume"
                     and (cancel.get("input") or {}).get("decision") == "rejected"
                     and (cancel.get("input") or {}).get("resume_token") == "@ctx.core.workflow.resume_token")


def preserva(ramo):
    esc = ap.get(resp.get(ramo)) or {}
    alvo = ap.get(destino_de(esc.get("id"), "true") or "") or {}
    return (esc.get("type") == "choice"
            and any(c.get("field") == "@ctx.session.customer_present" for c in esc.get("conditions") or [])
            and alvo.get("tool") == "workflow_resume"
            and (alvo.get("input") or {}).get("resume_token") == "@ctx.core.workflow.delegate_resume_token")


ts = ler("packages/schemas/src/context-map.ts") or ""
py = ler("packages/py-contextstore/src/plughub_contextstore/default_map.py") or ""
alias = all(re.search(r'telefone.{0,80}"session\.phone"', t) and re.search(r'email.{0,80}"session\.email"', t)
            for t in (ts, py))

print(json.dumps({
    "porta_runner":           dep.get("skill_id") == "skill_intake_runner_v1",
    "ancora_linha":           cfg.get("anchor_kind") == "phone" and cfg.get("proof_anchor_kind") == "phone",
    "form_sem_numero":        bool(campos) and not ({"numero_atual", "phone"} & set(campos)),
    "numero_da_ancora":       (numero.get("input") or {}).get("value") == "@ctx.session.phone",
    "ancora_da_pendencia":    dctx.get("phone") == "@ctx.session.phone" and "contact_identifier" not in dctx,
    "confirmacao_le_journey": sorted(set(re.findall(r"@ctx\.journey\.(numero_atual|operadora_destino)\b", ag_txt))),
    "confirmacao_le_sessao":  sorted(set(re.findall(r"@ctx\.session\.(numero_atual|operadora_destino)\b", ag_txt))),
    "cancelar_rejected":      cancelar_rejected,
    "ocioso_preserva":        preserva("on_timeout") and preserva("on_disconnect"),
    "alias_plataforma":       bool(alias),
}, ensure_ascii=False))
