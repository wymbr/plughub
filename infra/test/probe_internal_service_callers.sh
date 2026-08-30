#!/usr/bin/env bash
# probe_internal_service_callers.sh — os chamadores INTERNOS da analytics-api falam?
#
# ── O que aconteceu ─────────────────────────────────────────────────────────
# O fechamento de credencial de 2026-08-29 gateou 18 rotas da analytics-api e
# **não migrou nenhum chamador interno**. Medido em 2026-08-30, quatro chamavam
# sem header algum — e três degradavam para um ZERO PLAUSÍVEL:
#
#   evaluation-api/router.py:2221  → /v1/transcript/…    → 502 na tela (visível)
#   evaluation-api/backfill.py:76  → /reports/segments   → scanned=0   (mudo)
#   agent-registry/skills.ts:609   → /reports/sessions   → active_sessions:0 (mudo)
#   mcp-server/evaluation.ts:1114  → /v1/audit/mcp-calls → toolTrace=[] (mudo)
#
# O terceiro é o pior: `handoff-status` existe para decidir se um deploy é seguro,
# e passou a responder "0 sessões ativas" sempre.
#
# ── O que este probe afirma, e o que ele NÃO afirma ─────────────────────────
# Ele mede a FRONTEIRA (a analytics aceita/recusa) e as TRAVESSIAS (cada chamador
# realmente atravessa). Não mede o quarto chamador: aquele lê a rota de auditoria
# LGPD, que tem portão próprio e **fonte vazia** (`session_timeline` = 0 linhas,
# medido). Afrouxar o portão mais sensível da casa para servir um leitor sem dado
# seria decidir política contra população zero. Ramo G afirma o oposto: o token de
# serviço **não** fura o portão de auditoria.
#
#   A  fronteira: token válido → 200 · token errado → 401 · sem header → 401
#   B  travessia agent-registry: handoff-status devolve contagem REAL (não 0)
#   C  travessia evaluation-api: /reports/segments (backfill) com e sem header
#   D  travessia evaluation-api: /v1/transcript (a que dava 502 na tela)
#   E  o token está fiado nos TRÊS serviços e os três valores CASAM
#   F  a recusa é NOMEADA (service_credential_invalid ≠ auth_required)
#   G  serviço NÃO fura o portão de auditoria LGPD
#
# Uso:  bash infra/test/probe_internal_service_callers.sh

set -uo pipefail

AN="${AN:-http://localhost:3500}"
AR="${AR:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
SVC="${SVC:-changeme_analytics_service_token_demo}"
AR_SVC="${AR_SVC:-changeme_agent_registry_service_token_demo}"
FAIL=0

ok()  { echo "  ✓ $1"; }
bad() { echo "  ✗ $1"; FAIL=1; }
assert(){ if [ "$2" = "$3" ]; then ok "$1 = $3"; else bad "$1: esperado [$2], veio [$3]"; fi; }
code(){ curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@"; }

SEG="${AN}/reports/segments?tenant_id=${TENANT}&from_dt=2026-01-01&to_dt=2030-01-01&page=1&page_size=1"

echo "══ probe_internal_service_callers ══"
echo
echo "── A — a fronteira ─────────────────────────────────────────────────────"
assert "A token valido" "200" "$(code -H "X-Service-Token: $SVC" -H 'X-Service-Name: probe' "$SEG")"
assert "A token errado" "401" "$(code -H 'X-Service-Token: nao-e-esse' "$SEG")"
assert "A sem header"   "401" "$(code "$SEG")"

echo
echo "── F — a recusa é NOMEADA ──────────────────────────────────────────────"
# Sem isto, o 401 do token errado seria indistinguível do 401 de credencial
# ausente, e um deploy com token dessincronizado pareceria "faltou login".
D1=$(curl -s --max-time 8 -H 'X-Service-Token: nao-e-esse' "$SEG")
case "$D1" in
  *service_credential_invalid*) ok "F token errado diz service_credential_invalid" ;;
  *) bad "F esperava service_credential_invalid, veio: $(echo "$D1" | head -c 80)" ;;
esac
D2=$(curl -s --max-time 8 "$SEG")
case "$D2" in
  *auth_required*) ok "F sem header segue dizendo auth_required (ramo intacto)" ;;
  *) bad "F sem header deveria dizer auth_required, veio: $(echo "$D2" | head -c 80)" ;;
esac

echo
echo "── B — travessia agent-registry (handoff-status) ────────────────────────"
# Testemunha POSITIVA obrigatória: o defeito era justamente devolver 0. Um probe
# que só conferisse HTTP 200 ficaria verde com o defeito no lugar.
HS=$(curl -s --max-time 10 -H "x-service-token: ${AR_SVC}" -H "x-tenant-id: ${TENANT}" \
     "${AR}/v1/skills/skill_atendimento_sac_v1/handoff-status")
N=$(echo "$HS" | sed -n 's/.*"active_sessions":\([0-9]*\).*/\1/p')
if [ -z "$N" ]; then
  bad "B handoff-status não devolveu active_sessions: $(echo "$HS" | head -c 90)"
elif [ "$N" -gt 0 ]; then
  ok "B active_sessions = $N (contagem real, não o zero da falha silenciosa)"
else
  bad "B active_sessions = 0 — ou a travessia falhou, ou não há sessão. INCONCLUSIVO como verde"
fi

echo
echo "── C/D — travessias da evaluation-api, de dentro do container ───────────"
# De dentro, porque é lá que as duas coisas que podem falhar vivem: a env do
# serviço e o valor do token. Testar do host provaria a fronteira outra vez, não
# a fiação do chamador.
docker exec plughub-demo-evaluation-api-1 python -c "
from plughub_evaluation_api.config import settings
import httpx, sys
u = settings.analytics_api_url.rstrip('/')
h = {'X-Service-Token': settings.analytics_service_token, 'X-Service-Name': 'evaluation-api'}
falhou = 0
if not settings.analytics_service_token:
    print('  X C/D token de servico VAZIO na evaluation-api'); sys.exit(1)
p = {'tenant_id': '${TENANT}', 'from_dt': '2026-01-01', 'to_dt': '2030-01-01', 'page': 1, 'page_size': 1}
r  = httpx.get(u + '/reports/segments', params=p, headers=h, timeout=10)
r0 = httpx.get(u + '/reports/segments', params=p, timeout=10)
print('  C /reports/segments  com=%s sem=%s' % (r.status_code, r0.status_code))
falhou |= (r.status_code != 200) or (r0.status_code != 401)
print('  (backfill enxerga %d linha(s))' % len((r.json() or {}).get('data') or []))
sys.exit(1 if falhou else 0)
" 2>&1 | sed 's/^/  /' && ok "C travessia do backfill (200 com header, 401 sem)" || bad "C travessia do backfill"

SID=$(docker exec -i plughub-demo-clickhouse-1 clickhouse-client -q \
  "SELECT session_id FROM plughub_demo.messages FINAL WHERE tenant_id='${TENANT}'
   GROUP BY session_id ORDER BY count() DESC LIMIT 1" 2>/dev/null | head -1)
if [ -z "$SID" ]; then
  echo "  — sem sessão com mensagem: ramo D não exercido (não é verde)"
else
  docker exec plughub-demo-evaluation-api-1 python -c "
from plughub_evaluation_api.config import settings
import httpx, sys
u = settings.analytics_api_url.rstrip('/')
h = {'X-Service-Token': settings.analytics_service_token, 'X-Service-Name': 'evaluation-api'}
p = {'tenant_id': '${TENANT}', 'scope': 'contact'}
r = httpx.get(u + '/v1/transcript/sessions/${SID}', params=p, headers=h, timeout=10)
n = len((r.json() or {}).get('messages') or []) if r.status_code == 200 else 0
print('  D transcript -> %s | mensagens: %d' % (r.status_code, n))
sys.exit(0 if (r.status_code == 200 and n > 0) else 1)
" 2>&1 | sed 's/^/  /' && ok "D a transcrição da tela de Qualidade volta com conteúdo" \
                        || bad "D transcrição — era o 502 da tela"
fi

echo
echo "── E — o segredo está fiado nos TRÊS e os valores CASAM ─────────────────"
V_AN=$(docker exec plughub-demo-analytics-api-1  sh -lc 'printf %s "$PLUGHUB_ANALYTICS_SERVICE_TOKEN"' 2>/dev/null)
V_EV=$(docker exec plughub-demo-evaluation-api-1 sh -lc 'printf %s "$PLUGHUB_EVALUATION_ANALYTICS_SERVICE_TOKEN"' 2>/dev/null)
V_AR=$(docker exec plughub-demo-agent-registry-1 sh -lc 'printf %s "$ANALYTICS_SERVICE_TOKEN"' 2>/dev/null)
for pair in "analytics-api:$V_AN" "evaluation-api:$V_EV" "agent-registry:$V_AR"; do
  nome="${pair%%:*}"; val="${pair#*:}"
  if [ -n "$val" ]; then ok "E $nome tem o segredo"; else bad "E $nome SEM o segredo"; fi
done
if [ -n "$V_AN" ] && [ "$V_AN" = "$V_EV" ] && [ "$V_AN" = "$V_AR" ]; then
  ok "E os três valores CASAM"
else
  bad "E os valores DIVERGEM — rotação pela metade é o modo de falha desta família"
fi

echo
echo "── G — serviço NÃO fura o portão de auditoria LGPD ──────────────────────"
# Decisão deliberada: o 4º chamador (mcp-server → /v1/audit/mcp-calls) NÃO foi
# atendido. Aquela rota tem portão próprio, com trilha gravada, e a fonte está
# vazia. Este ramo guarda a regressão mais tentadora — dar ao serviço uma chave
# mestra "para resolver de uma vez".
G=$(code -H "X-Service-Token: $SVC" -H 'X-Service-Name: probe' \
    "${AN}/v1/audit/mcp-calls?tenant_id=${TENANT}&session_id=x&limit=1")
case "$G" in
  401|403) ok "G auditoria recusa o serviço ($G) — portão preservado" ;;
  200)     bad "G o token de serviço FUROU o portão de auditoria LGPD" ;;
  *)       bad "G auditoria devolveu $G (inesperado)" ;;
esac

echo
if [ "$FAIL" -eq 0 ]; then echo "══ VERDE ══"; else echo "══ VERMELHO ══"; fi
exit "$FAIL"
