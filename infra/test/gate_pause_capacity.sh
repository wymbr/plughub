#!/usr/bin/env bash
# gate_pause_capacity.sh — 2026-08-21
#   TODO § "A capacidade de pool não conhece PAUSA"
#
# PERGUNTA: pausar um agente retira as vagas dele de circulação, e retomá-lo as
# devolve?
#
# TRÊS PONTOS DE MEDIÇÃO, e o primeiro é o que dá ao gate o poder de REPROVAR:
#
#   P0  agente ATIVO      → available > 0            ← CONTROLE
#   P1  agente PAUSADO    → available == 0
#   P2  agente RETOMADO   → available == P0
#
# Sem o P0 e o P2 este gate ficaria verde para um recompute que devolvesse zero
# sempre — o modo de falha que a sessão de 08-20 batizou: um caso permitido que
# pula o portão não serve de controle. Aqui o portão é o MESMO em todos os três
# pontos (`write_pool_snapshot` sobre o mesmo Redis); o que muda é UM valor, o
# `status` da instância, e ele muda pelo caminho de produção (endpoints
# `/api/agent-pause` e `/api/agent-resume` do mcp-server), nunca por escrita
# direta em Redis.
#
# O QUE ESTE GATE **NÃO** EXERCITA, e diz na saída:
#   · a metade "pausado COM sessão viva" só é exercida se o agente já estiver
#     atendendo quando o gate roda. Sem sessão, `paused_capacity` é a capacidade
#     inteira e o caso interessante (a sessão continua, as vagas livres somem)
#     não aparece. O gate detecta e DECLARA.
#   · a criação da instância humana. Ela nasce só no login WS do Console
#     (`registerHumanAgent`), e forjá-la em Redis testaria a forja. Por isso o
#     agente logado é PRÉ-CONDIÇÃO, não passo.
#
# PRÉ-CONDIÇÃO: um agente humano logado no Console, no pool medido.
#
# USO:   bash infra/test/gate_pause_capacity.sh [pool_id]
#        AGENT_PASS=changeme_operator bash infra/test/gate_pause_capacity.sh
# SAÍDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO (pré-condição ausente)

set -u   # sem -e: cada ramo ausente vira INCONCLUSIVO explícito, não morte muda

TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
POOL="${1:-${POOL:-retencao_humano}}"
AUTH="${AUTH:-http://localhost:3202}"     # 3200 no host é o ai-gateway
MCP="${MCP:-http://localhost:3100}"
# Senha do agente DESCOBERTO. Sem override, é derivada do prefixo do e-mail do
# seed do demo (`infra/seed/seed_auth.py`) — o gate descobre quem está logado, e
# fixar uma senha só o faria reprovar por credencial sempre que o agente da vez
# fosse outro. `AGENT_PASS=…` continua vencendo, para base não-demo.
AGENT_PASS="${AGENT_PASS:-}"
SETTLE_MAX="${SETTLE_MAX:-15}"            # s de espera pelo snapshot recomputado

DC="docker compose -f $COMPOSE"
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
r() { $DC exec -T redis redis-cli "$@" < /dev/null; }

inconclusivo() { echo; echo "VEREDICTO: INCONCLUSIVO — $1"; exit 2; }
vermelho()     { echo; echo "VEREDICTO: VERMELHO — $1";     exit 1; }

command -v jq > /dev/null 2>&1 || inconclusivo "jq não está instalado"
[ "$(r PING)" = "PONG" ] || inconclusivo "redis-cli não respondeu PING"

echo "══ gate: a pausa retira as vagas de circulação? (pool=$POOL) ══"

# ── Pré-condição: DESCOBRIR o agente logado, não assumi-lo ───────────────────
READY="$(r SMEMBERS "${TENANT}:pool:${POOL}:instances"      | tr -d '\r')"
BUSY="$(r SMEMBERS  "${TENANT}:pool:${POOL}:busy_instances" | tr -d '\r')"
HUMANS="$(printf '%s\n%s\n' "$READY" "$BUSY" | grep '^human-' | sort -u)"
N_H="$(printf '%s\n' "$HUMANS" | grep -c '^human-')"

if [ "$N_H" -eq 0 ]; then
  inconclusivo "nenhum agente humano no pool $POOL. A instância humana nasce no
  login WS do Console (registerHumanAgent) — logue um agente e rode de novo.
  Forjar a chave em Redis testaria a forja, não o produto."
fi
if [ "$N_H" -gt 1 ]; then
  inconclusivo "$N_H agentes humanos no pool; este gate mede UM (o efeito de
  pausar um só não é isolável na linha agregada). Deixe um logado."
fi

INST="$(printf '%s\n' "$HUMANS" | grep '^human-' | head -1)"
IRAW="$(r GET "${TENANT}:instance:${INST}" | tr -d '\r')"
[ -n "$IRAW" ] || inconclusivo "instância $INST é membro do pool mas não tem chave"

EMAIL="$(printf '%s' "$IRAW" | jq -r '.user_login // empty')"
MC="$(printf    '%s' "$IRAW" | jq -r '.max_concurrent // 1')"
ST0="$(printf   '%s' "$IRAW" | jq -r '.status // .state // empty')"
USED="$(r SCARD "${TENANT}:instance:${INST}:sessions" | tr -d '\r')"

echo "   agente ....... $INST  ($EMAIL)"
echo "   max_concurrent $MC   ·  sessões em curso: $USED  ·  status: $ST0"

[ -n "$EMAIL" ] || inconclusivo "instância sem user_login — não dá para autenticar
  como o próprio agente, e o endpoint de pausa deriva a instância do 'sub' do JWT"
[ "$ST0" = "paused" ] && inconclusivo "o agente JÁ está pausado — sem o ponto P0
  (ativo) não existe controle, e o gate ficaria verde por construção. Retome-o."

# ── Login COMO O PRÓPRIO AGENTE ─────────────────────────────────────────────
# `/api/agent-pause` monta `human-${jwt.sub}`. Um token de admin pausaria a
# instância do admin (404) e o gate reprovaria pelo motivo errado.
if [ -z "$AGENT_PASS" ]; then
  case "${EMAIL%%@*}" in
    admin)      AGENT_PASS="changeme_admin"      ;;
    supervisor) AGENT_PASS="changeme_supervisor" ;;
    operator)   AGENT_PASS="changeme_operator"   ;;
    *) inconclusivo "não sei a senha de $EMAIL (fora do seed do demo).
  Passe AGENT_PASS=… — o gate precisa autenticar COMO o agente, porque o endpoint
  de pausa deriva a instância do 'sub' do JWT." ;;
  esac
fi

TOK="$($CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$AGENT_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')"
[ -n "$TOK" ] || inconclusivo "login falhou para $EMAIL em $AUTH.
  Passe AGENT_PASS=… (demo: changeme_operator / changeme_supervisor / changeme_admin)."

snap_field() { # campo → valor (vazio se ausente)
  r GET "${TENANT}:pool:${POOL}:snapshot" | tr -d '\r' | jq -r ".$1 // empty"
}
snap_line() {
  r GET "${TENANT}:pool:${POOL}:snapshot" | tr -d '\r' \
    | jq -c '{available,busy,busy_elsewhere,paused_capacity,total_instances,model}'
}

# Espera o snapshot ser REESCRITO (updated_at muda). O refresh é assíncrono
# (`asyncio.create_task`); medir na hora leria o valor PRÉ-mudança e a falha
# pareceria "não aplicou".
esperar_reescrita() { # updated_at anterior
  local antes="$1" i=0
  while [ "$i" -lt "$SETTLE_MAX" ]; do
    sleep 1; i=$((i+1))
    [ "$(snap_field updated_at)" != "$antes" ] && return 0
  done
  return 1
}

# ── P0 — CONTROLE: agente ativo ─────────────────────────────────────────────
echo
echo "── P0 (controle): agente ATIVO ──"
UPD0="$(snap_field updated_at)"
A0="$(snap_field available)"; B0="$(snap_field busy)"; T0="$(snap_field total_instances)"
echo "   $(snap_line)"
[ -n "$A0" ] || inconclusivo "sem snapshot publicado para $POOL (TTL 3600 s expirado?)"
if [ "$A0" -le 0 ]; then
  inconclusivo "P0 já tem available=$A0. O controle não existe: um recompute que
  devolvesse zero sempre passaria em P1 sem provar nada."
fi

# ── P1 — PAUSA pelo caminho de produção ─────────────────────────────────────
echo
echo "── P1: PUT /api/agent-pause ──"
RESP="$($CURL -X PUT "$MCP/api/agent-pause" $JSON -H "Authorization: Bearer $TOK" \
  -d "{\"pool_id\":\"$POOL\",\"reason_id\":\"gate\",\"reason_label\":\"gate_pause_capacity\"}")"
echo "   resposta: ${RESP:0:160}"
[ "$(printf '%s' "$RESP" | jq -r '.state // empty')" = "paused" ] \
  || vermelho "o endpoint de pausa não confirmou estado 'paused': $RESP"

if ! esperar_reescrita "$UPD0"; then
  vermelho "o snapshot NÃO foi reescrito em ${SETTLE_MAX}s após a pausa.
  É o defeito do gancho: o consumidor de agent.lifecycle não agiu, e a linha
  ficou CONGELADA no valor de antes (available=$A0) até o TTL de 1 h."
fi
UPD1="$(snap_field updated_at)"
A1="$(snap_field available)"; B1="$(snap_field busy)"
P1="$(snap_field paused_capacity)"; T1="$(snap_field total_instances)"
echo "   $(snap_line)"

FAIL=""
[ "$A1" = "0" ] || FAIL="$FAIL\n   · available=$A1 (esperado 0) — vaga oferecida de agente pausado"
[ "${B1:-0}" = "${USED:-0}" ] || FAIL="$FAIL\n   · busy=$B1 mas há $USED sessão(ões) em curso — a pausa não pode interromper"
if [ -n "$P1" ]; then
  ESP=$(( MC - USED )); [ "$ESP" -lt 0 ] && ESP=0
  [ "$P1" = "$ESP" ] || FAIL="$FAIL\n   · paused_capacity=$P1 (esperado $ESP = max_concurrent − sessões em curso)"
else
  FAIL="$FAIL\n   · paused_capacity AUSENTE na linha — sem ele available<total fica inexplicável"
fi

# ── P2 — RETOMADA: a vaga volta ─────────────────────────────────────────────
echo
echo "── P2: PUT /api/agent-resume ──"
RESP2="$($CURL -X PUT "$MCP/api/agent-resume" $JSON -H "Authorization: Bearer $TOK" \
  -d "{\"pool_id\":\"$POOL\"}")"
echo "   resposta: ${RESP2:0:160}"
if ! esperar_reescrita "$UPD1"; then
  FAIL="$FAIL\n   · o snapshot não foi reescrito em ${SETTLE_MAX}s após a retomada"
fi
A2="$(snap_field available)"
echo "   $(snap_line)"
[ "$A2" = "$A0" ] || FAIL="$FAIL\n   · available=$A2 após retomar, era $A0 antes de pausar —
     a capacidade não voltou. Regressão SIMÉTRICA, e pior que o defeito original:
     capacidade some em vez de sobrar."

# ── Veredicto ───────────────────────────────────────────────────────────────
echo
echo "── cobertura desta execução ──"
if [ "${USED:-0}" -gt 0 ]; then
  echo "   ✓ metade 'pausado COM sessão viva' EXERCITADA ($USED em curso):"
  echo "     a sessão continua e as vagas LIVRES ($(( MC - USED ))) saem de circulação."
else
  echo "   ⚠ metade 'pausado COM sessão viva' NÃO exercitada — o agente estava"
  echo "     ocioso. Neste caminho a capacidade inteira sai, e o caso em que a"
  echo "     aritmética precisa distinguir vaga ocupada de vaga livre não apareceu."
  echo "     Para exercitá-la: deixe o agente atendendo 1 contato e rode de novo."
fi

if [ -n "$FAIL" ]; then
  echo
  echo "FALHAS:"; printf "$FAIL\n"
  vermelho "a pausa não retira as vagas de circulação (ver acima)"
fi
echo
echo "VEREDICTO: VERDE — P0 available=$A0 · P1 available=0 (paused_capacity=$P1) · P2 available=$A2"
exit 0
