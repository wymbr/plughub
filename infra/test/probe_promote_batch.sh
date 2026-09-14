#!/usr/bin/env bash
# probe_promote_batch.sh — 2026-09-14  (PID-16)
#
# PERGUNTA: um skill que roda em várias portas é promovido em LOTE — um snapshot só, a
#           config de cada pool, todos ou nenhum — e o rollback continua por pool?
#
# O QUE FOI MEDIDO ANTES
#   `skill_intake_runner_v1` roda em `limite_ia` e `portabilidade_ia`: UM snapshot e DUAS
#   configs. O release era `set-next` + `promote` por pool. Vermelho ao vivo, nos fixtures
#   deste probe: `skill_nps_v1` pool a pool em `probe_batch_a` (webchat) e `probe_batch_b`
#   (webhook) — o primeiro foi PROMOVIDO, o segundo recusado pelo perfil, e o release ficou
#   pela metade sem nada que o desfizesse junto.
#
# TRÊS RAMOS
#   A  CENSO — os portões do candidato têm UMA casa (`lib/slot-candidate.ts`): nenhuma rota
#      chama os `judge*` diretamente. Mutação sobre cópia.
#   M  FALSEABILIDADE — a suíte `promote-batch.test.ts` roda sobre uma CÓPIA do `src/` com
#      seis mutações do produto (tudo-ou-nada, capacidade somada, pool inalterado, next
#      pendente, config adivinhada, portões pulados); cada uma tem de reprovar, e a cópia
#      sem mutação tem de passar.
#   B  AO VIVO — fixtures `probe_batch_{a,b,c}`: lote com um pool recusado não muda nenhum;
#      lote válido promove o mesmo snapshot com a config de cada pool e um SkillDeployment
#      por pool com o batch_id; repetir é `unchanged` e preserva o previous; rollback de um
#      pool não toca o outro.
#
# ⚠️ Os fixtures RODAM: o lote positivo precisa de `current`, e o bridge instancia 1 agente
# por pool (sem endpoint, nenhum contato chega). Custo declarado: 2 de capacidade declarada.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

TENANT="${TENANT:-tenant_demo}"
REG="${REG:-http://localhost:3300}"
SVC="${AGENT_REGISTRY_SERVICE_TOKEN:-changeme_agent_registry_service_token_demo}"
NODE_BIN="${NODE_BIN:-/home/a1/.nvm/versions/node/v24.14.1/bin}"
export PATH="$NODE_BIN:$PATH"
PKG=packages/agent-registry
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
jexpr() { printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(eval(sys.argv[1]))
except Exception as e:
    print("__ERRO__ %s" % e)' "$2"; }

echo "════════════════════════════════════════════════════════════════════"
echo " um release de skill em várias portas é um lote — todos ou nenhum?"
echo "════════════════════════════════════════════════════════════════════"

# ── A · CENSO ──────────────────────────────────────────────────────────────
echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
censo() {  # $1 raiz do src → JSON {chamadores: [...]}
  python3 - "$1" <<'PY'
import json, os, re, sys
src = sys.argv[1]
chamada = re.compile(r"(?<!function )\b(judgeMaskedDeploy|judgeProfileSteps|judgeRequiredConfig|judgeIdentityFloor)\s*\(")
fora = []
for d, subdirs, arquivos in os.walk(src):
    subdirs[:] = sorted(s for s in subdirs if s not in ("__tests__", "node_modules"))
    for a in sorted(arquivos):
        if not a.endswith(".ts") or a.endswith(".test.ts"):
            continue
        rel = os.path.relpath(os.path.join(d, a), src).replace(os.sep, "/")
        with open(os.path.join(d, a), encoding="utf-8") as f:
            if chamada.search(f.read()):
                fora.append(rel)
defs = {"lib/masked-deploy.ts", "lib/profile-steps.ts", "lib/required-config.ts", "lib/identity-floor.ts"}
print(json.dumps({"chamadores": sorted(set(fora) - defs)}))
PY
}
C=$(censo "$PKG/src")
echo "   $C"
[ "$(jexpr "$C" "d['chamadores'] == ['lib/slot-candidate.ts']")" = "True" ] \
  && ok "os portões do candidato têm uma casa: só lib/slot-candidate.ts os chama" \
  || falha "portão chamado fora da casa única: $C"
TMPA=$(mktemp -d)
cp -r "$PKG/src" "$TMPA/src"
python3 - "$TMPA/src/routes/pool-slots-batch.ts" <<'PY'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
a = 'import { judgeSlotCandidate } from "../lib/slot-candidate"\n'
assert s.count(a) == 1
s = s.replace(a, a + 'import { judgeProfileSteps } from "../lib/profile-steps"\nvoid judgeProfileSteps(null, [], { poolId: "x", skillId: "y" })\n')
open(p, "w", encoding="utf-8").write(s)
PY
CM=$(censo "$TMPA/src"); rm -rf "$TMPA"
[ "$(jexpr "$CM" "d['chamadores'] != ['lib/slot-candidate.ts']")" = "True" ] \
  && ok "mutação 'o lote julga um portão por conta própria' → censo sujo" \
  || falha "mutação não acusada: $CM"

# ── M · FALSEABILIDADE DA SUÍTE ────────────────────────────────────────────
echo ""
echo "── M · a suíte do lote reprova quando o produto quebra ─────────────────"
MUT="$PKG/.probe_pid16"
trap 'rm -rf "$MUT"' EXIT
roda_suite() {  # → exit do vitest sobre a cópia
  (cd "$MUT" && npx vitest run --root . src/__tests__/promote-batch.test.ts >/tmp/_probe_pid16_vitest.log 2>&1)
}
copia() { rm -rf "$MUT"; mkdir -p "$MUT"; cp -r "$PKG/src" "$MUT/src"; }
mutacao() {  # $1 rótulo · $2 arquivo relativo ao src · $3 âncora · $4 substituto
  copia
  if ! python3 - "$MUT/src/$2" "$3" "$4" <<'PY'
import sys
p, a, b = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
if s.count(a) != 1: raise SystemExit(1)
open(p, "w", encoding="utf-8", newline="").write(s.replace(a, b))
PY
  then incon "mutação '$1' não se aplicou (âncora sumiu)"; return; fi
  if roda_suite; then falha "mutação '$1' passou na suíte — os testes não a julgam"
  # vermelho por ASSERÇÃO, não por a cópia não compilar — senão a mutação mediria a sintaxe
  elif grep -qE "AssertionError|expected .* to " /tmp/_probe_pid16_vitest.log \
       && ! grep -qE "Transform failed|SyntaxError|Failed to load|Cannot find module" /tmp/_probe_pid16_vitest.log; then
    ok "mutação '$1' → suíte vermelha por asserção"
  else
    incon "mutação '$1' deixou a suíte vermelha sem asserção (erro de carga?): $(grep -m2 -E 'Error' /tmp/_probe_pid16_vitest.log)"
  fi
}
if ! command -v npx >/dev/null 2>&1 || [ ! -d "$PKG/node_modules" ]; then
  incon "npx ou $PKG/node_modules ausente — a falseabilidade não foi medida"
else
  copia
  if ! roda_suite; then
    incon "a CÓPIA sem mutação não passa — as mutações não provariam nada: $(grep -E 'FAIL|Error' /tmp/_probe_pid16_vitest.log | head -3)"
  else
    ok "controle: a suíte passa sobre a cópia sem mutação"
    B=routes/pool-slots-batch.ts
    mutacao "tudo-ou-nada vira pool a pool" "$B" "    if (problemas.length > 0) {" "    if (false) {"
    mutacao "capacidade julgada por pool" "$B" "mudam.map(p => ({ poolId: p.poolId, declared: slotDeclared(p.config) }))" "mudam.slice(0, 1).map(p => ({ poolId: p.poolId, declared: slotDeclared(p.config) }))"
    mutacao "pool idêntico volta a ser promovido" "$B" "const unchanged = !!current" "const unchanged = false && !!current"
    mutacao "next pendente atropelado" "$B" "if (pendente && !substituirNext && !unchanged) {" "if (false) {"
    mutacao "config de outro skill herdada" "$B" '} else if (current && current["skill_id"] === skillId) {' "} else if (current) {"
    mutacao "lote pula os portões" "$B" "      const veredito = judgeSlotCandidate({" "      const veredito: { kind: string; warnings: string[]; error?: string; message?: string } = { kind: 'ok', warnings: [] }; void ({"
  fi
fi

# ── B · AO VIVO ────────────────────────────────────────────────────────────
echo ""
echo "── B · AO VIVO ────────────────────────────────────────────────────────"
H=(-H "x-tenant-id: $TENANT" -H "x-service-token: $SVC" -H "x-user-id: probe_pid16" -H 'content-type: application/json')
PG="docker exec plughub-demo-postgres-1 psql -U plughub -d plughub_registry -Atc"
SA=skill_nps_v1
SB=skill_atendimento_reembolso_v1

slots() {  # $1 pool → JSON {current:{skill,cfg,snap}, previous:{...}}
  curl -s "$REG/v1/pools/$1/slots" -H "x-tenant-id: $TENANT" | python3 -c 'import json,sys
d = json.load(sys.stdin)["slots"]
def f(s): return {"skill": s.get("skill_id"), "cfg": s.get("config_json"), "snap": json.dumps(s.get("yaml_snapshot"), sort_keys=True), "set_at": s.get("set_at")}
print(json.dumps({"current": f(d["current"]), "previous": f(d["previous"])}))'
}
lote() {  # $1 corpo → "<http>\t<json>"
  local code
  code=$(curl -s -o /tmp/_probe_pid16_lote.json -w '%{http_code}' -X POST "$REG/v1/pool-slots/promote-batch" "${H[@]}" -d "$1")
  printf '%s\t%s' "$code" "$(cat /tmp/_probe_pid16_lote.json)"
}
garante_pool() {  # $1 pool $2 canal
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$REG/v1/pools/$1" -H "x-tenant-id: $TENANT")" = "200" ] && return 0
  curl -s -o /dev/null -w '%{http_code}' -X POST "$REG/v1/pools" "${H[@]}" -d "{\"pool_id\":\"$1\",\"agent_kind\":\"ai\",
    \"description\":\"fixture do probe_promote_batch (PID-16) — só o probe promove aqui\",\"channel_types\":[\"$2\"],
    \"sla_target_ms\":60000,\"max_concurrent_sessions\":1}" | grep -q '^20'
}
corpo() {  # $1 skill $2 pools(csv) → JSON com a config de cada pool marcada
  python3 -c 'import json,sys
skill, pools = sys.argv[1], sys.argv[2].split(",")
print(json.dumps({"skill_id": skill, "pools": pools,
  "configs": {p: {"max_concurrent_sessions": 1, "probe_pool": p} for p in pools}}))' "$1" "$2"
}

if ! curl -sf "$REG/v1/health" >/dev/null 2>&1; then
  incon "agent-registry inalcançável em $REG"
elif ! garante_pool probe_batch_a webchat || ! garante_pool probe_batch_b webhook || ! garante_pool probe_batch_c webchat; then
  incon "não consegui garantir os fixtures probe_batch_{a,b,c}"
else
  # Preparação: a e c com o MESMO skill (S0), para o lote seguinte mudar os dois e
  # deixar um previous conhecido. S0 é o que NÃO está em `a`.
  A0=$(slots probe_batch_a)
  if [ "$(jexpr "$A0" "d['current']['skill']")" = "$SA" ]; then S0=$SB; T=$SA; else S0=$SA; T=$SB; fi
  R=$(lote "$(corpo "$S0" probe_batch_a,probe_batch_c)")
  if [ "${R%%$'\t'*}" != "200" ]; then
    incon "preparação (lote $S0 em a,c) recusada: ${R:0:400}"
  else
    ANTES_A=$(slots probe_batch_a); ANTES_C=$(slots probe_batch_c); ANTES_B=$(slots probe_batch_b)

    # B1 — tudo ou nada
    R=$(lote "$(corpo "$T" probe_batch_a,probe_batch_b)"); CODE=${R%%$'\t'*}; BODY=${R#*$'\t'}
    if [ "$CODE" = "422" ] && [ "$(jexpr "$BODY" "d['error'] == 'lote_recusado' and [x['pool_id'] for x in d['pools']] == ['probe_batch_b']")" = "True" ]; then
      ok "lote com o pool webhook: 422 nomeando SÓ probe_batch_b ($(jexpr "$BODY" "d['pools'][0]['error']"))"
    else
      falha "lote com pool inválido: HTTP $CODE ${BODY:0:300}"
    fi
    [ "$(slots probe_batch_a)" = "$ANTES_A" ] && [ "$(slots probe_batch_b)" = "$ANTES_B" ] \
      && ok "tudo ou nada: os slots de a e b ficaram byte a byte como estavam" \
      || falha "o lote recusado mexeu em slot: a=$(slots probe_batch_a)"

    # B2 — lote válido
    R=$(lote "$(corpo "$T" probe_batch_a,probe_batch_c)"); CODE=${R%%$'\t'*}; BODY=${R#*$'\t'}
    BATCH=$(jexpr "$BODY" "d.get('batch_id','')")
    if [ "$CODE" != "200" ] || [ "$(jexpr "$BODY" "d['promoted']")" != "2" ]; then
      falha "lote válido: HTTP $CODE ${BODY:0:300}"
    else
      DA=$(slots probe_batch_a); DC=$(slots probe_batch_c)
      FLOW=$(curl -s "$REG/v1/skills/$T" -H "x-tenant-id: $TENANT" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("flow"), sort_keys=True))')
      V=$(python3 -c 'import json,sys
a, c, flow, s0, t = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
print(json.dumps({
  "skill":     a["current"]["skill"] == t and c["current"]["skill"] == t,
  "snapshot":  a["current"]["snap"] == c["current"]["snap"] == flow,
  "config":    a["current"]["cfg"].get("probe_pool") == "probe_batch_a" and c["current"]["cfg"].get("probe_pool") == "probe_batch_c",
  "previous":  a["previous"]["skill"] == s0 and c["previous"]["skill"] == s0,
}))' "$DA" "$DC" "$FLOW" "$S0" "$T")
      [ "$(jexpr "$V" "all(d.values())")" = "True" ] \
        && ok "lote válido: $T em a e c com o MESMO snapshot (= flow do skill), config de cada pool, previous = $S0" \
        || falha "lote válido promoveu errado: $V"
      N=$($PG "select count(*), string_agg(pool_ids[1], ',' order by pool_ids[1]) from skill_deployments where tenant_id='$TENANT' and notes like 'promote-batch:$BATCH%'" 2>/dev/null)
      [ "$N" = "2|probe_batch_a,probe_batch_c" ] \
        && ok "um SkillDeployment por pool com o batch_id ($BATCH)" \
        || falha "SkillDeployment do lote: [$N]"

      # B3 — repetir não apaga o previous
      R=$(lote "$(corpo "$T" probe_batch_a,probe_batch_c)"); CODE=${R%%$'\t'*}; BODY=${R#*$'\t'}
      if [ "$CODE" = "200" ] && [ "$(jexpr "$BODY" "d['unchanged'] == 2 and d['promoted'] == 0")" = "True" ] \
         && [ "$(slots probe_batch_a)" = "$DA" ] && [ "$(slots probe_batch_c)" = "$DC" ]; then
        ok "repetir o lote: unchanged nos dois, e o previous ($S0) sobreviveu"
      else
        falha "repetição do lote: HTTP $CODE ${BODY:0:200} · a=$(slots probe_batch_a)"
      fi

      # B4 — rollback por pool
      RB=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$REG/v1/pools/probe_batch_a/rollback" "${H[@]}")
      if [ "$RB" = "200" ] && [ "$(jexpr "$(slots probe_batch_a)" "d['current']['skill']")" = "$S0" ] \
         && [ "$(slots probe_batch_c)" = "$DC" ]; then
        ok "rollback de probe_batch_a voltou a $S0 e não tocou probe_batch_c"
      else
        falha "rollback por pool: HTTP $RB · a=$(slots probe_batch_a)"
      fi
    fi
  fi
fi

echo ""
if [ "$FALHA" -gt 0 ]; then echo "VEREDICTO: FALHA ($FALHA)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "VEREDICTO: INCONCLUSIVO ($INCONCL)"; exit 2; fi
echo "VEREDICTO: OK — o release em várias portas é um lote, e o rollback segue por pool"
exit 0
