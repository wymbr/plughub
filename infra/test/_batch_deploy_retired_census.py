#!/usr/bin/env python3
"""_batch_deploy_retired_census.py — PID-08 (2026-09-14)

Censo do fonte para `probe_batch_deploy_retired.sh`. Recebe a RAIZ a medir (o repo, ou a
cópia mutada do probe) e imprime UM JSON com os fatos:

  escritores_deployment   arquivos de agent-registry/src (fora de testes) que chamam
                          skillDeployment.create/createMany/upsert/update/updateMany —
                          uma entrada por chamada. O esperado é EXATAMENTE
                          ["routes/pool-slots.ts"]: vazio também é sujo (sem escritor
                          nenhum, a lente fica sem marker e ninguém percebe).
  rota_lote_410           o handler de POST "/:skill_id/deploy" existe, responde 410 e
                          não toca o prisma.
  tool_skill_deploy       a tool `skill_deploy` está registrada no mcp-server.
  tool_pool_promote       a tool `pool_promote` está registrada (controle positivo: o
                          censo acha registro de tool quando ele existe).
  workflow_agendado       o YAML `skill_scheduled_deploy_v1` existe no catálogo.
  seed_usa_lote           o seed da lente chama `/v1/skills/.../deploy`.
  seed_usa_promote        o seed da lente chama `/promote` (controle positivo).
"""
import json
import os
import re
import sys

raiz = sys.argv[1] if len(sys.argv) > 1 else "."


def ler(rel):
    try:
        with open(os.path.join(raiz, rel), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


# ── escritores de SkillDeployment ─────────────────────────────────────────────
src = os.path.join(raiz, "packages/agent-registry/src")
escritores = []
padrao = re.compile(r"skillDeployment\s*\.\s*(create|createMany|upsert|update|updateMany)\s*\(")
for d, subdirs, arquivos in os.walk(src):
    subdirs[:] = sorted(s for s in subdirs if s not in ("__tests__", "node_modules"))
    for a in sorted(arquivos):
        if not a.endswith(".ts") or a.endswith(".test.ts"):
            continue
        caminho = os.path.join(d, a)
        with open(caminho, encoding="utf-8") as f:
            n = len(padrao.findall(f.read()))
        escritores += [os.path.relpath(caminho, src).replace(os.sep, "/")] * n

# ── a rota do lote ────────────────────────────────────────────────────────────
skills = ler("packages/agent-registry/src/routes/skills.ts") or ""
m = re.search(r'skillsRouter\.post\(\s*"/:skill_id/deploy"', skills)
rota_410 = None
if m:
    # corpo do handler: do registro até o próximo `skillsRouter.` de nível zero
    fim = skills.find("\nskillsRouter.", m.end())
    corpo = skills[m.start(): fim if fim != -1 else len(skills)]
    rota_410 = bool(re.search(r"status\(\s*410\s*\)", corpo)) and "prisma" not in corpo

# ── tools do mcp-server ───────────────────────────────────────────────────────
tools_dir = os.path.join(raiz, "packages/mcp-server-plughub/src")
registradas = set()
for d, subdirs, arquivos in os.walk(tools_dir):
    subdirs[:] = [s for s in subdirs if s not in ("__tests__", "node_modules")]
    for a in arquivos:
        if a.endswith(".ts") and not a.endswith(".test.ts"):
            with open(os.path.join(d, a), encoding="utf-8") as f:
                registradas |= set(re.findall(r'server\.(?:tool|registerTool)\(\s*"([a-z_]+)"', f.read()))

seed = ler("infra/test/seed_deploy_lens_demo.sh") or ""
seed_exec = "\n".join(l for l in seed.splitlines() if not l.lstrip().startswith("#"))

print(json.dumps({
    "escritores_deployment": escritores,
    "rota_lote_410": rota_410,
    "tool_skill_deploy": "skill_deploy" in registradas,
    "tool_pool_promote": "pool_promote" in registradas,
    "workflow_agendado": ler("packages/skill-flow-engine/skills/skill_scheduled_deploy_v1.yaml") is not None,
    "seed_usa_lote": bool(re.search(r"/v1/skills/[^\s\"]*/deploy", seed_exec)),
    "seed_usa_promote": "/promote" in seed_exec,
}, ensure_ascii=False))
