"""_deploy_write_principal_census.py — PID-07. Censo estático do agent-registry.

Uso: python3 _deploy_write_principal_census.py <raiz src do agent-registry>
Imprime UMA linha JSON com os fatos; o probe julga. A raiz é argumento para que as
mutações rodem sobre uma CÓPIA — mutar a árvore compartilhada seria mutar o trabalho
de outra sessão.
"""
import json
import os
import re
import sys

src = sys.argv[1]


def rd(*p):
    with open(os.path.join(src, *p), encoding="utf-8") as f:
        return f.read()


fatos = {}

# 1 · ordem de montagem: o router de deploy ANTES do de pools (a ordem é o mecanismo)
app = rd("app.ts")
i_slots = app.find('app.use("/v1/pools/:pool_id"')
i_pools = app.find('app.use("/v1/pools",')
fatos["montagem_encontrada"] = i_slots >= 0 and i_pools >= 0
fatos["deploy_antes_de_pools"] = fatos["montagem_encontrada"] and i_slots < i_pools

# 2 · cada rota de ESCRITA do router de deploy declara o portão do deploy
sl = rd("routes", "pool-slots.ts")
m = re.search(r'const (\w+)\s*=\s*requireAbacWrite\("skill_flows",\s*"operacao"\)', sl)
gate = m.group(1) if m else None
escritas = re.findall(r'poolSlotsRouter\.(put|post|patch|delete)\(\s*"([^"]+)"\s*,\s*([^\n]*)', sl)
fatos["rotas_escrita"] = sorted(f"{v.upper()} {p}" for v, p, _ in escritas)
fatos["rotas_sem_portao"] = sorted(
    f"{v.upper()} {p}" for v, p, resto in escritas if not gate or not resto.lstrip().startswith(gate)
)

# 3 · nenhum router lê o autor do header (população: todos os arquivos de rota)
leitores = []
for nome in sorted(os.listdir(os.path.join(src, "routes"))):
    if nome.endswith(".ts") and re.search(r'headers\[\s*"x-user-id"\s*\]', rd("routes", nome)):
        leitores.append(nome)
fatos["routers_lendo_x_user_id"] = leitores
fatos["populacao_routers"] = len([n for n in os.listdir(os.path.join(src, "routes")) if n.endswith(".ts")])

# 4 · o middleware amarra o tenant do Bearer
mw = rd("middleware", "require-resource-write.ts")
fatos["middleware_recusa_tenant_divergente"] = '"tenant_mismatch"' in mw
fatos["middleware_preenche_tenant_do_token"] = 'req.headers["x-tenant-id"] = claimTenant' in mw

print(json.dumps(fatos, ensure_ascii=False))
