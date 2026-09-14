"""_resume_requirement_census.py — PID-06. Censo estático da cadeia da exigência de retomada.

Uso: python3 _resume_requirement_census.py <raiz do repo>
Imprime UMA linha JSON com os fatos; o probe julga. A raiz é argumento para as mutações
rodarem sobre CÓPIA.

A cadeia tem seis elos, e cada um perde o campo calado se esquecer dele:
  schema (step) → deploy (piso) → engine (resolve e repassa) → skill-flow-service (HTTP)
  → gateway (PendingEntry / chave legada / leitura) → mcp-server (julga ao liberar).
"""
import json
import os
import re
import sys

root = sys.argv[1]


def rd(rel):
    with open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


f = {}

sk = rd("packages/schemas/src/skill.ts")
f["schema_campo_nos_steps"] = sk.count("resume_requires:       ResumeRequiresFieldSchema.optional()")

ps = rd("packages/agent-registry/src/routes/pool-slots.ts")
# PID-16: o piso é julgado na casa única dos portões do candidato (`lib/slot-candidate.ts`),
# e set-next e promote a chamam. Duas metades: a casa julga o piso, e cada momento chama a casa.
casa = rd("packages/agent-registry/src/lib/slot-candidate.ts")
casa_julga_piso = "judgeIdentityFloor(snapshot, configJson" in casa


def _bloco(ini, fim):
    a = ps.find(ini)
    b = ps.find(fim, a + 1) if a >= 0 else -1
    return ps[a:b] if a >= 0 and b > a else ""


f["deploy_piso_set_next"] = casa_julga_piso and "judgeSlotCandidate(" in _bloco(
    'poolSlotsRouter.put("/slots/:slot"', 'poolSlotsRouter.post("/promote"')
f["deploy_piso_promote"] = casa_julga_piso and "judgeSlotCandidate(" in _bloco(
    'poolSlotsRouter.post("/promote"', 'poolSlotsRouter.post("/rollback"')

eng_d = rd("packages/skill-flow-engine/src/steps/delegate.ts")
eng_c = rd("packages/skill-flow-engine/src/steps/collect.ts")
f["engine_repassa"] = [n for n, s in (("delegate", eng_d), ("collect", eng_c))
                       if "resolveResumeRequirement(step, ctx)" in s and "resume_requires: exigencia.value" in s]

sfs = rd("packages/e2e-tests/services/skill-flow-service/src/index.ts")
f["sfs_repassa"] = sfs.count("resume_requires: params.resume_requires")

wh = rd("packages/channel-gateway/src/plughub_channel_gateway/adapters/webhook.py")
f["gw_pending_entry"] = wh.count("resume_requires=resume_requires,   # PID-06")
f["gw_chave_legada"] = len(re.findall(r'"resume_requires":\s+resume_requires', wh))
f["gw_leituras"] = int("p.resume_requires" in wh) + int("first.resume_requires" in wh) + int('data.get("resume_requires")' in wh)
# PID-13 — a retomada: o portão mora no handle_resume e todas as portas passam por ele
f["gw_portao_no_resume"] = wh.count("await self._enforce_resume_requirement(")
f["gw_scanner_isento"] = wh.count('identity_clearance="timeout",   # PID-13')
f["gw_registro_carrega"] = wh.count("resume_requires=resume_requires,   # PID-13")
gm = rd("packages/channel-gateway/src/plughub_channel_gateway/main.py")
# o atestado é lido UMA vez na rota interna e é ele que chega ao handle_resume
_rota = gm.split("async def webhook_resume(", 1)
_rota = _rota[1].split("\n@app.", 1)[0] if len(_rota) == 2 else ""
f["gw_atestado_rota_interna"] = int(
    _rota.count("clearance = _resume_identity_clearance(request)") == 1
    and "identity_clearance = clearance," in _rota
)
# a rota externa NUNCA aceita atestado: nenhum identity_clearance no corpo dela
ext = gm.split('async def external_webhook_resume(', 1)
f["gw_externa_sem_atestado"] = len(ext) == 2 and "identity_clearance" not in ext[1].split("\n@app.", 1)[0]
idx = rd("packages/channel-gateway/src/plughub_channel_gateway/identity/index.py")
f["gw_campo_no_registro"] = "resume_requires: list[str] | None = None" in idx

wf = rd("packages/mcp-server-plughub/src/tools/workflow.ts")
m = re.search(r'withGuard\("pending_workflow_get".*?\n  \)\n', wf, re.S)
bloco = m.group(0) if m else ""
f["mcp_bloco_encontrado"] = bool(bloco)
f["mcp_saidas_julgadas"] = bloco.count("withholdUnprovenResume(")
# toda resposta que carrega token tem de sair do julgamento: sobra de retorno cru = porta em volta
f["mcp_retornos_crus"] = len(re.findall(r"JSON\.stringify\(\s*(data|\{\s*customer_id: ref\.customer_id, \.\.\.pdata\s*\})\s*\)", bloco))

m2 = re.search(r'withGuard\("workflow_resume".*?\n  \)\n', wf, re.S)
bres = m2.group(0) if m2 else ""
f["mcp_resume_julga"] = "resumeIdentityClearance(deps.redis" in bres and "resume_requires_unproven" in bres
f["mcp_atesta_so_satisfeito"] = "clearance?.requires && clearance.satisfied" in bres and "...atestado" in bres

# população: steps de pendência de cliente nos skills do repo, e quem declara exigência
skills_dir = os.path.join(root, "packages/skill-flow-engine/skills")
resumiveis, declaram, com_piso = [], [], []
for nome in sorted(os.listdir(skills_dir)):
    if not nome.endswith(".yaml"):
        continue
    txt = open(os.path.join(skills_dir, nome), encoding="utf-8").read()
    for bloco_step in re.split(r"\n  - id: ", txt)[1:]:
        sid = bloco_step.split("\n", 1)[0].strip()
        corpo = bloco_step.split("\n  - ", 1)[0]
        if re.search(r"\n\s+customer_resumable: true", corpo):
            resumiveis.append(f"{nome[:-5]}.{sid}")
            if "resume_requires:" in corpo:
                declaram.append(f"{nome[:-5]}.{sid}")
            if "resume_requires_floor:" in corpo:
                com_piso.append(f"{nome[:-5]}.{sid}")
f["resumiveis"] = resumiveis
f["declaram"] = declaram
f["com_piso"] = com_piso

print(json.dumps(f, ensure_ascii=False))
