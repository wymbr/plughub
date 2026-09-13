#!/usr/bin/env bash
# probe_evidence_transport.sh — 2026-09-13  (PID-03)
#
# PERGUNTA: a evidência de quem ACABOU de provar chega ao processo retomado — pelo merge e
#           pelo `workflow_resume`?
#
# O DEFEITO QUE O ORIGINOU (medido ao vivo antes do fix)
#   Na jornada do limite, duas consultas fizeram OTP `verified` e as duas se uniram ao
#   processo por `journey_merge`. O hash do processo ficou com a prova da PRIMEIRA: o merge
#   fazia "canônica vence" campo a campo e apagava a origem. A D5 ("só a evidência da
#   sessão que retoma") recusaria justamente quem acabou de provar.
#
# TRÊS RAMOS
#   A  ESTÁTICO — o merge tira a evidência do "canônica vence" e a adota como registro; o
#      `workflow_resume` transporta ANTES de chamar o gateway.
#   B  RESUME VIVO — `workflow_resume` real pelo /sse sobre sessões sintéticas com token
#      vencido (o gateway recusa, nada acorda): a prova mais nova viaja; controle: a mais
#      velha não sobrescreve.
#   C  MERGE PONTA A PONTA — roda a jornada (duas consultas com OTP) e exige que o processo
#      guarde a prova da ÚLTIMA consulta que verificou.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

MCP="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
TENANT="${TENANT:-tenant_demo}"
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
echo " a prova de quem acabou de provar chega ao processo retomado?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · ESTÁTICO ───────────────────────────────────────────────────────"
A=$(python3 - <<'EOF'
import json, re
j = open("packages/mcp-server-plughub/src/tools/journey.ts", encoding="utf-8").read()
w = open("packages/mcp-server-plughub/src/tools/workflow.ts", encoding="utf-8").read()
m = re.search(r"async function migrateJourneyContext\(.*?\n}\n", j, re.S).group(0)
r = re.search(r'withGuard\("workflow_resume".*?\n    \}\),', w, re.S).group(0)
out = {
  "merge_pula_evidencia": "if (mecanismoDe(tag) !== null) continue" in m,
  "merge_adota": "adoptNewerEvidence(redis, src, dst)" in m and m.index("adoptNewerEvidence(redis, src, dst)") < m.index("redis.del(src)"),
  "resume_transporta_antes": "transportEvidenceOnResume(" in r and r.index("transportEvidenceOnResume(") < r.index("/v1/channels/webhook/resume/"),
}
print(json.dumps(out))
EOF
)
echo "   $A"
for k in merge_pula_evidencia merge_adota resume_transporta_antes; do
  [ "$(jexpr "$A" "d['$k']")" = "True" ] && ok "$k" || falha "$k"
done

echo ""
echo "── B · RESUME VIVO ────────────────────────────────────────────────────"
J=$(docker exec -i "$MCP" sh -c "cd /app/packages/mcp-server-plughub && node - $TENANT" < infra/test/_evidence_transport_exercise.cjs 2>/dev/null | tail -1)
echo "   $J" | cut -c1-400
if [ "$(jexpr "$J" "'casos' in d")" != "True" ]; then incon "exercício sem JSON: $J"; else
  for k in leva_mais_nova controle_mais_velha gateway_404; do
    [ "$(jexpr "$J" "d['casos'].get('$k')")" = "True" ] && ok "$k" || falha "$k"
  done
fi

echo ""
echo "── C · MERGE PONTA A PONTA (duas consultas na jornada) ────────────────"
if [ "${PULA_JORNADA:-}" = "1" ]; then
  incon "PULA_JORNADA=1 — merge não medido"
else
  bash infra/test/probe_journey_merge_status_access.sh > /tmp/pid03_jornada.log 2>&1
  EJ=$?
  [ "$EJ" = "0" ] && ok "jornada verde" || falha "jornada EXIT=$EJ (ver /tmp/pid03_jornada.log)"
  SESS=$(grep -oE "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}" /tmp/pid03_jornada.log | sort -u | tr '\n' ' ')
  J=$(docker exec -i "$GW" python - "$TENANT" $SESS 2>/dev/null <<'EOF' | tail -1
import json, sys, redis
r = redis.Redis.from_url("redis://redis:6379", decode_responses=True)
t, sess = sys.argv[1], sys.argv[2:]
consultas = []
processo = None
for s in sess:
    meta = json.loads(r.get("session:%s:meta" % s) or "{}")
    res = json.loads(r.get("%s:pipeline:%s" % (t, s)) or "{}").get("results", {})
    if meta.get("pool_id") == "limite_processo":
        processo = s
    if (res.get("otp_res") or {}).get("verified") is True and res.get("unificar_journey:__invoked__") == "completed":
        ini = r.xrange("session:%s:stream" % s, count=1)
        consultas.append((ini[0][0] if ini else "", s))
consultas.sort()
out = {"processo": processo, "consultas": [c[1] for c in consultas]}
if processo:
    v = r.hget("%s:ctx:journey:%s" % (t, processo), "core.journey.identity.otp.proven_in_session")
    out["prova_no_processo"] = json.loads(v)["value"] if v else None
out["casos"] = {
    "duas_consultas_provaram": len(consultas) >= 2,
    "processo_tem_a_ultima": bool(consultas) and out.get("prova_no_processo") == consultas[-1][1],
}
print(json.dumps(out))
EOF
)
  echo "   $J"
  if [ "$(jexpr "$J" "'casos' in d")" != "True" ]; then incon "leitura da jornada sem JSON"; else
    [ "$(jexpr "$J" "d['casos']['duas_consultas_provaram']")" = "True" ] && ok "duas consultas provaram posse e se uniram ao processo (testemunha)" \
      || incon "a jornada não produziu duas consultas verificadas — não há o que medir"
    [ "$(jexpr "$J" "d['casos']['processo_tem_a_ultima']")" = "True" ] && ok "o processo guarda a prova da ÚLTIMA consulta" \
      || falha "o processo guarda a prova de $(jexpr "$J" "d.get('prova_no_processo')"), não da última consulta"
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"
exit 0
