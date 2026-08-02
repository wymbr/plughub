#!/usr/bin/env bash
# smoke_shared_capacity_fanout.sh — prova AO VIVO do defeito A corrigido (fatia 2).
#
# O que prova: um recurso (humano, max_concurrent 3) logado em DOIS pools, com UMA
# vaga consumida por um deles, faz as DUAS linhas de snapshot descontarem a vaga —
# e a linha do pool que NÃO serviu explica o desconto via `busy_elsewhere`. Antes da
# fatia 2 o pool irmão anunciava capacidade cheia.
#
# O que é semeado e o que é REAL:
#   · SEMEADO — o login. A instância é escrita no Redis exatamente como o
#     `registerHumanAgent` (mcp-server) a escreve; mesmo padrão do
#     smoke_human_instance_identity.sh. Login não é o objeto do teste.
#   · REAL — a OCUPAÇÃO. Vai pelo `claim_instance` (semáforo atômico, com a tag de
#     pool) + `mark_busy`, e volta pelo `remove_conversation`. São as mesmas funções
#     que o router chama. Nada de ocupação é escrito à mão: seria seedar a resposta.
#
# Pools DEDICADOS (`cap_smoke_*`), nunca os operacionais: pôr uma instância fantasma
# no ready_set de `retencao_humano` criaria capacidade que o roteamento acredita.
#
#   ./smoke_shared_capacity_fanout.sh [tenant] [run|keep|cleanup]
#     run     (default) ciclo completo + limpeza
#     keep    para com a vaga OCUPADA e deixa o estado de pé — para rodar o
#             measure_capacity_licensing_baseline.sh em cima e ver as linhas
#     cleanup só apaga o que o `keep` deixou
set -uo pipefail

TENANT="${1:-tenant_demo}"
MODE="${2:-run}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"

$COMPOSE exec -T -e SMOKE_TENANT="$TENANT" -e SMOKE_MODE="$MODE" routing-engine python - <<'PY'
import asyncio, json, os, sys
import redis.asyncio as aioredis
from plughub_routing.registry import (
    InstanceRegistry, _pool_snapshot_key, _pool_instances_key,
    _pool_busy_instances_key, _instance_key, _instance_sessions_key,
)

TENANT = os.environ["SMOKE_TENANT"]
MODE   = os.environ.get("SMOKE_MODE", "run")
URL    = (os.environ.get("PLUGHUB_REDIS_URL") or os.environ.get("REDIS_URL")
          or "redis://redis:6379")

POOL_A, POOL_B = "cap_smoke_a", "cap_smoke_b"
INST, SID, CAP = "human-cap_smoke", "ses-cap-smoke", 3

G, R, Y, Z = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
fails: list[str] = []
def ok(m):   print(f"  {G}OK{Z}      {m}")
def bad(m):  fails.append(m); print(f"  {R}FALHOU{Z}  {m}")
def note(m): print(f"  {Y}»{Z} {m}")


async def cleanup(c):
    keys = [_instance_key(TENANT, INST), _instance_sessions_key(TENANT, INST),
            f"{TENANT}:routing:instance:{INST}:meta",
            f"{TENANT}:routing:instance:{INST}:conversations",
            f"{TENANT}:session:pool:{SID}", f"{TENANT}:instance:{INST}:reap_cooldown"]
    for p in (POOL_A, POOL_B):
        keys += [_pool_snapshot_key(TENANT, p), _pool_instances_key(TENANT, p),
                 _pool_busy_instances_key(TENANT, p)]
    await c.delete(*keys)


async def snap(c, pool):
    raw = await c.get(_pool_snapshot_key(TENANT, pool))
    return json.loads(raw) if raw else None


async def show(c, label):
    print(f"\n  ── {label} ──")
    out = {}
    for p in (POOL_A, POOL_B):
        s = await snap(c, p)
        out[p] = s
        if s is None:
            print(f"    {p:<14} (sem snapshot)")
            continue
        print(f"    {p:<14} avail={s['available']} busy={s['busy']} "
              f"elsew={s['busy_elsewhere']} untag={s['untagged']} "
              f"total={s['total_instances']} model={s.get('model')}")
    return out


def check_line(s, pool):
    """A aritmética da própria linha: available = total − busy − busy_elsewhere."""
    if s is None:
        bad(f"{pool}: snapshot ausente — nada a julgar")
        return
    calc = s["total_instances"] - s["busy"] - s["busy_elsewhere"]
    (ok if s["available"] == calc else bad)(
        f"{pool}: a linha fecha ({s['available']} = {s['total_instances']} − "
        f"{s['busy']} − {s['busy_elsewhere']})" if s["available"] == calc else
        f"{pool}: a linha NÃO fecha — {s}")


async def main():
    c   = aioredis.from_url(URL, decode_responses=True)
    await c.ping()
    reg = InstanceRegistry(c)

    if MODE == "cleanup":
        await cleanup(c); print("  limpeza feita."); await c.aclose(); return 0

    await cleanup(c)

    # ── setup: login humano (semeado como o registerHumanAgent escreve) ────────
    await c.set(_instance_key(TENANT, INST), json.dumps({
        "instance_id": INST, "agent_type_id": f"human_agent_{POOL_A}",
        "user_id": "cap_smoke", "user_login": "cap_smoke@demo.local",
        "tenant_id": TENANT, "pools": [POOL_A, POOL_B],
        "execution_model": "stateful", "max_concurrent": CAP,
        "current_sessions": 0, "status": "ready", "source": "human_login",
    }))
    for p in (POOL_A, POOL_B):
        await c.sadd(_pool_instances_key(TENANT, p), INST)
        # Primeira linha do pool — é o que o `route()` faz no 1º contato (e o que o
        # bootstrap faria se estes pools estivessem no registry). O fan-out só
        # REESCREVE pool que já tem snapshot; sem isto o teste mediria a lacuna
        # registrada no TODO, não o fan-out.
        await reg.write_pool_snapshot(tenant_id=TENANT, pool_id=p,
                                      sla_target_ms=480_000, channel_types=["webchat"])

    base = await show(c, "estado inicial — 1 humano de 3 vagas em 2 pools, ocioso")
    for p in (POOL_A, POOL_B):
        s = base[p]
        (ok if s and s["available"] == CAP and s["total_instances"] == CAP else bad)(
            f"{p}: parte de {CAP} vagas livres" if s and s["available"] == CAP
            else f"{p}: setup não partiu de {CAP} livres — {s}")

    # ── ocupação pelo caminho REAL ────────────────────────────────────────────
    claimed = await reg.claim_instance(TENANT, INST, SID, None, CAP, pool_id=POOL_A)
    (ok if claimed == 1 else bad)(
        f"claim atômico em {POOL_A} devolveu ocupação {claimed}"
        if claimed == 1 else f"claim não ocupou a vaga (retorno {claimed}) — NADA A MEDIR")
    if claimed != 1:
        await cleanup(c); await c.aclose(); return 1
    await reg.mark_busy(TENANT, POOL_A, INST, SID)

    occ = await show(c, "UMA vaga consumida por cap_smoke_a (via claim + mark_busy)")
    sa, sb = occ[POOL_A], occ[POOL_B]

    (ok if sa and sa["busy"] == 1 and sa["busy_elsewhere"] == 0 else bad)(
        f"{POOL_A}: serviu o contato (busy 1, elsewhere 0)" if sa and sa["busy"] == 1
        and sa["busy_elsewhere"] == 0 else f"{POOL_A}: esperado busy 1/elsew 0 — {sa}")

    # ── O NÓ DO DEFEITO A ─────────────────────────────────────────────────────
    (ok if sb and sb["available"] == CAP - 1 else bad)(
        f"{POOL_B}: DESCONTOU a vaga que o irmão consumiu (available {CAP-1})"
        if sb and sb["available"] == CAP - 1 else
        f"{POOL_B}: available={sb and sb['available']} ignora o consumo do irmão — "
        f"é o defeito A. A capacidade é do RECURSO, não do pool")
    (ok if sb and sb["busy"] == 0 and sb["busy_elsewhere"] == 1 else bad)(
        f"{POOL_B}: o desconto está EXPLICADO (busy 0, busy_elsewhere 1)"
        if sb and sb["busy"] == 0 and sb["busy_elsewhere"] == 1 else
        f"{POOL_B}: esperado busy 0/elsew 1 — {sb}")

    for p in (POOL_A, POOL_B):
        check_line(occ[p], p)
        s = occ[p]
        (ok if s and s["untagged"] == 0 else bad)(
            f"{p}: nenhum ocupante untagged" if s and s["untagged"] == 0
            else f"{p}: untagged={s and s['untagged']} — escritor fora do claim_instance")
        (ok if s and s.get("model") == "resource_semaphore" else bad)(
            f"{p}: linha do routing-engine (model resource_semaphore)"
            if s and s.get("model") == "resource_semaphore"
            else f"{p}: model={s and s.get('model')} — não é a linha derivada do semáforo")

    if MODE == "keep":
        note("estado MANTIDO com a vaga ocupada. Rode agora:")
        note(f"  bash infra/test/measure_capacity_licensing_baseline.sh {TENANT}")
        note("  (procure cap_smoke_a e cap_smoke_b na tabela)")
        note(f"Depois: bash infra/test/smoke_shared_capacity_fanout.sh {TENANT} cleanup")
        await c.aclose()
        return 1 if fails else 0

    # ── devolução: as DUAS linhas voltam ──────────────────────────────────────
    await reg.remove_conversation(TENANT, INST, SID, fallback_pools=[POOL_A])
    rel = await show(c, "contato encerrado — a vaga volta ao RECURSO")
    for p in (POOL_A, POOL_B):
        s = rel[p]
        good = s and s["available"] == CAP and s["busy"] == 0 and s["busy_elsewhere"] == 0
        (ok if good else bad)(
            f"{p}: voltou a {CAP} vagas livres" if good
            else f"{p}: a vaga não voltou — {s}")
    left = await c.exists(_instance_sessions_key(TENANT, INST))
    (ok if not left else bad)(
        "semáforo do recurso vazio (release apagou o SET)" if not left
        else "SET de ocupantes sobreviveu ao release — vaga vazada")

    await cleanup(c)
    await c.aclose()
    return 1 if fails else 0


rc = asyncio.run(main())
print()
if fails:
    print(f"  {R}{len(fails)} asserção(ões) reprovaram:{Z}")
    for f in fails:
        print(f"    · {f}")
else:
    print(f"  {G}todas as asserções passaram{Z}")
sys.exit(rc)
PY
