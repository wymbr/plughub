#!/usr/bin/env python3
"""_intake_runner_census.py — PID-04 (2026-09-14)

Censo do fonte para `probe_intake_runner.sh`. Recebe a RAIZ a medir (o repo, ou a cópia
mutada do probe) e imprime UM JSON de fatos:

  runner_presente          o YAML do runner existe com o id certo
  literais_de_dominio      pools/forms LITERAIS no runner fora da allowlist de plataforma
                           (`dialog_runner`, `dialog_otp_possession`). Tudo o que é do
                           processo tem de vir de `$.config.*` ou da pendência.
  context_json_no_runner   o runner dispara o processo por template de texto (a injeção)
  nos_ausentes             `render.by_node.<nó>` referenciado pelo runner ou pela
                           continuidade que NÃO existe no roteiro configurado para o
                           `limite_ia` — referência a nó ausente resolve para texto VAZIO
  config_faltando          `config_params` obrigatórios do runner que o seed do `limite_ia`
                           não declara
  intake_antigo            sobra do `skill_limite_entrada_v1` (arquivo ou id)
  retorno_le_sessao        o `limite_retorno` ainda lê o resultado de `@ctx.session.*`
  choice_le_config         o `choice` do engine enxerga `$.config.*`
  choice_le_journey        a condição `@ctx.journey.*` do `choice` lê o hash da journey
  continuidade_le_journey  a continuidade lê cartão e valor da journey (controle: o
                           censo acha as leituras quando elas existem)
"""
import io
import json
import os
import re
import sys

import yaml

raiz = sys.argv[1] if len(sys.argv) > 1 else "."
SK = os.path.join(raiz, "packages/skill-flow-engine/skills")


def ler(rel):
    try:
        with io.open(os.path.join(raiz, rel), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def yml(rel):
    t = ler(rel)
    return yaml.safe_load(t) if t else None


runner_rel = "packages/skill-flow-engine/skills/skill_intake_runner_v1.yaml"
runner = yml(runner_rel) or {}
runner_txt = ler(runner_rel) or ""
cont_txt = ler("packages/skill-flow-engine/skills/skill_limite_continuidade_v1.yaml") or ""

PLATAFORMA = {"dialog_runner", "dialog_otp_possession"}
literais = []
for st in runner.get("steps") or []:
    candidatos = []
    if st.get("type") == "delegate":
        candidatos.append(("pool", st.get("pool")))
        for k, v in (st.get("context") or {}).items():
            if k.endswith("form_id"):
                candidatos.append((k, v))
    if st.get("type") == "escalate":
        candidatos.append(("target.pool", (st.get("target") or {}).get("pool")))
    for k, v in (st.get("input") or {}).items():
        if k in ("pool_id", "form_id", "skill_id"):
            candidatos.append((k, v))
    for k, v in candidatos:
        if isinstance(v, str) and not v.startswith(("$.", "@ctx.")) and v not in PLATAFORMA:
            literais.append("%s.%s=%s" % (st.get("id"), k, v))

context_json = any("context_json" in (st.get("input") or {}) for st in runner.get("steps") or [])

# o roteiro que o limite_ia usa, lido do SEED do pool
seed = yml("infra/registry/tenant_demo.yaml") or {}
pool = next((p for p in seed.get("pools") or [] if p.get("pool_id") == "limite_ia"), {})
cfg = ((pool.get("deploy") or {}).get("config") or {})
form_id = cfg.get("dialog_form_id") or ""
nos = set()
forma = ler("infra/dialog/%s.json" % form_id) if form_id else None
if forma:
    nos = {n.get("id") for n in json.loads(forma).get("nodes", [])}
refs = set(re.findall(r"render\.by_node\.([a-z0-9_]+)", runner_txt + "\n" + cont_txt))
nos_ausentes = sorted(refs - nos) if forma else ["<roteiro do limite_ia ilegível: %s>" % form_id]

obrig = [p["key"] for p in runner.get("config_params") or [] if p.get("required")]
config_faltando = [k for k in obrig if cfg.get(k) in (None, "", [], {})]

intake_antigo = []
if os.path.exists(os.path.join(SK, "skill_limite_entrada_v1.yaml")):
    intake_antigo.append("arquivo")
if pool and (pool.get("deploy") or {}).get("skill_id") == "skill_limite_entrada_v1":
    intake_antigo.append("seed do limite_ia")

retorno = ler("packages/skill-flow-engine/skills/skill_limite_retorno_v1.yaml") or ""
retorno_le_sessao = sorted(set(re.findall(
    r"@ctx\.session\.(resultado|numero_cartao|limite_aprovado|parecer)\b", retorno)))

choice = ler("packages/skill-flow-engine/src/steps/choice.ts") or ""
m = re.search(r"const evalContext = \{(.*?)\}", choice, re.S)
choice_le_config = bool(m and re.search(r"\bconfig\s*:\s*ctx\.config", m.group(1)))
# a condição `@ctx.journey.*` vai ao hash da journey (sem isto a entrega narrou recusa
# de pedido aprovado)
choice_le_journey = bool(re.search(r"journey:\$\{ctx\.journeyId\}", choice)) and "contextStore.get(naJourney" in choice

continuidade_le_journey = sorted(set(re.findall(r"@ctx\.journey\.(numero_cartao|limite_solicitado)\b", cont_txt)))

print(json.dumps({
    "runner_presente":         runner.get("id") == "skill_intake_runner_v1",
    "literais_de_dominio":     literais,
    "context_json_no_runner":  context_json,
    "roteiro":                 form_id,
    "refs_by_node":            len(refs),
    "nos_ausentes":            nos_ausentes,
    "config_faltando":         config_faltando,
    "intake_antigo":           intake_antigo,
    "retorno_le_sessao":       retorno_le_sessao,
    "choice_le_config":        choice_le_config,
    "choice_le_journey":       choice_le_journey,
    "continuidade_le_journey": continuidade_le_journey,
}, ensure_ascii=False))
