#!/usr/bin/env bash
# probe_masked_field_sinks.sh — o valor de um campo `masked` chega a algum store?
#
# ── Por que este probe existe ────────────────────────────────────────────────
# Em 2026-08-29 mediu-se que `skill_auth_form_v1` (pool `auth_form_ia`, campos
# `senha` e `codigo_2fa` com `masked: true` NO CAMPO, sem `masked` no step)
# deixou 13 respostas CRUAS em `plughub_demo.messages`. Expurgadas.
#
# A investigação que se seguiu produziu CINCO falsos positivos seguidos, todos
# pela mesma causa: sonda por `LIKE` sobre nome de campo casa a DEFINIÇÃO do
# formulário (`interaction_request`), a PROSA do skill ("Formulário de
# autenticação iniciado…") e a chave do JSON — nunca só o valor. Nenhuma delas
# respondia a pergunta feita.
#
# Um canário resolve por construção: string improvável, presente APENAS se o
# valor digitado atravessou. Não há falso positivo possível.
#
# ── O que ele julga ──────────────────────────────────────────────────────────
# Cinco destinos da resposta do cliente (ver `redact_customer_reply` em
# orchestrator-bridge/main.py):
#   1. stream canônico / Agent Assist   → PG session_stream_events (payload)
#   2. Kafka conversations.events       → ClickHouse messages          ← o que vazou
#   3. cofre do masking de mensagem     → PG session_stream_events (original_content)
#   4. log do bridge                    → docker logs
#   5. step `receive`                   → transiente, sem store: FORA de escopo
#
# ── A testemunha negativa é obrigatória ──────────────────────────────────────
# Um redator que suprimisse TUDO passaria em todos os testes de vazamento e
# quebraria o produto. Por isso o canário do campo NÃO mascarado (`email`) tem
# de ser ENCONTRADO. Sem essa metade o probe não pode reprovar o modo de falha
# oposto, e vira decoração.
#
# ── Uso ──────────────────────────────────────────────────────────────────────
#   bash infra/test/probe_masked_field_sinks.sh
#
# Reprodução MANUAL (mesmo molde de gate_sentiment_engine_half.sh): o script
# prepara, pede o contato, e depois julga. Não dirige a UI.
#
# Saída: VERDE (nenhum valor mascarado em store algum, e o não-mascarado está lá)
#      | VERMELHO (vazou, com o destino nomeado)
#      | INCONCLUSIVO (o contato não chegou — nada a julgar)

set -uo pipefail   # sem -e de propósito: medição que falha tem de IMPRIMIR

CH_CONTAINER="${CH_CONTAINER:-plughub-demo-clickhouse-1}"
PG_CONTAINER="${PG_CONTAINER:-plughub-demo-postgres-1}"
BRIDGE_CONTAINER="${BRIDGE_CONTAINER:-plughub-demo-orchestrator-bridge-1}"
CH_DB="${CH_DB:-plughub_demo}"
PG_DB="${PG_DB:-plughub_demo}"
PG_USER="${PG_USER:-plughub}"

STAMP="$(date +%s)"
CANARIO_SENHA="CANARIOSENHA${STAMP}"
CANARIO_2FA="CANARIO2FA${STAMP}"
CANARIO_EMAIL="canario${STAMP}@probe.local"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; OFF=$'\033[0m'

echo "═══════════════════════════════════════════════════════════════════"
echo " probe_masked_field_sinks — canário de campo mascarado"
echo "═══════════════════════════════════════════════════════════════════"

# ── Fase 0 — PREFLIGHT ───────────────────────────────────────────────────────
# Sem isto o probe mede a imagem ANTIGA e devolve o número pré-conserto como se
# fosse resultado. `restart` não basta: o bridge não monta o fonte.
echo
echo "── Fase 0 — preflight ──"
if ! docker ps --format '{{.Names}}' | grep -q "^${BRIDGE_CONTAINER}$"; then
    echo "${YEL}INCONCLUSIVO${OFF}: container ${BRIDGE_CONTAINER} não está de pé."
    echo "  (ajuste BRIDGE_CONTAINER=… ou suba a stack)"
    exit 2
fi

if docker exec "$BRIDGE_CONTAINER" \
        grep -q "def redact_customer_reply" \
        /app/packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py 2>/dev/null; then
    echo "  redator único presente na IMAGEM  ✔"
else
    echo "${YEL}INCONCLUSIVO${OFF}: a imagem em execução não tem \`redact_customer_reply\`."
    echo "  O conserto está no fonte, não no container. Rode:"
    echo "    docker compose -f docker-compose.demo.yml build orchestrator-bridge"
    echo "    docker compose -f docker-compose.demo.yml up -d orchestrator-bridge"
    echo "  (\`restart\` NÃO serve — nenhum serviço monta o fonte.)"
    exit 2
fi

LOG_SINCE="$(date -u +%Y-%m-%dT%H:%M:%S)"   # instante ABSOLUTO: --since por duração soma execuções anteriores

# ── Fase 1 — o contato ───────────────────────────────────────────────────────
cat <<EOF

── Fase 1 — reprodução manual ──

  1. Abra o webchat do demo e inicie um contato no pool que use
     \`skill_auth_form_v1\` (no tenant_demo: mencione @auth_form).
  2. No formulário, preencha EXATAMENTE:

         email       ${CANARIO_EMAIL}
         senha       ${CANARIO_SENHA}
         codigo_2fa  ${CANARIO_2FA}

  3. Submeta e volte aqui.

  (\`email\` não é mascarado — é a testemunha negativa. Ele DEVE aparecer.)

EOF
read -r -p "Pressione ENTER quando tiver submetido o formulário… " _

echo
echo "  aguardando propagação (Kafka → ClickHouse)…"
sleep 8

# ── Fase 2 — varredura dos destinos ──────────────────────────────────────────
echo
echo "── Fase 2 — varredura ──"

ch() { docker exec -i "$CH_CONTAINER" clickhouse-client -q "$1" 2>/dev/null || echo "ERRO"; }
pg() { docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc "$1" 2>/dev/null || echo "ERRO"; }

VAZOU=0
INCONCLUSIVO=0

check_sink() {   # nome, contagem, tipo(masked|clean)
    local nome="$1" n="$2" tipo="$3"
    if [ "$n" = "ERRO" ]; then
        echo "  ${YEL}?${OFF} ${nome}: consulta falhou"
        INCONCLUSIVO=1
        return
    fi
    if [ "$tipo" = "masked" ]; then
        if [ "$n" -gt 0 ] 2>/dev/null; then
            echo "  ${RED}✗${OFF} ${nome}: ${n} ocorrência(s) do canário MASCARADO"
            VAZOU=1
        else
            echo "  ${GRN}✔${OFF} ${nome}: limpo"
        fi
    fi
}

# Destino 2 — ClickHouse messages (o que vazou em 2026-08-10..19)
N_CH=$(ch "SELECT count() FROM ${CH_DB}.messages
           WHERE content LIKE '%${CANARIO_SENHA}%' OR content LIKE '%${CANARIO_2FA}%'")
check_sink "ClickHouse messages" "$N_CH" masked

# Destino 1 — stream canônico persistido
N_PG=$(pg "SELECT count(*) FROM session_stream_events
           WHERE payload::text LIKE '%${CANARIO_SENHA}%'
              OR payload::text LIKE '%${CANARIO_2FA}%'")
check_sink "PG session_stream_events.payload" "$N_PG" masked

# Destino 3 — cofre do masking de MENSAGEM (mecanismo distinto; deve ser 0 sempre)
N_VAULT=$(pg "SELECT count(*) FROM session_stream_events
              WHERE original_content::text LIKE '%${CANARIO_SENHA}%'
                 OR original_content::text LIKE '%${CANARIO_2FA}%'")
check_sink "PG session_stream_events.original_content" "$N_VAULT" masked

# Destino 4 — log do bridge (masked-input.md: "nunca em logs")
N_LOG=$(docker logs --since "$LOG_SINCE" "$BRIDGE_CONTAINER" 2>&1 \
        | grep -c -e "$CANARIO_SENHA" -e "$CANARIO_2FA")
check_sink "log do orchestrator-bridge" "${N_LOG:-0}" masked

# ── Fase 3 — testemunha negativa ─────────────────────────────────────────────
# Sem esta metade, um redator que suprimisse TUDO passaria acima.
echo
echo "── Fase 3 — testemunha negativa (o não-mascarado tem de sobreviver) ──"

N_EMAIL_CH=$(ch "SELECT count() FROM ${CH_DB}.messages WHERE content LIKE '%${CANARIO_EMAIL}%'")
N_EMAIL_PG=$(pg "SELECT count(*) FROM session_stream_events WHERE payload::text LIKE '%${CANARIO_EMAIL}%'")

if [ "$N_EMAIL_CH" = "ERRO" ] || [ "$N_EMAIL_PG" = "ERRO" ]; then
    echo "  ${YEL}?${OFF} consulta falhou"
    INCONCLUSIVO=1
elif [ "$(( ${N_EMAIL_CH:-0} + ${N_EMAIL_PG:-0} ))" -eq 0 ]; then
    echo "  ${YEL}?${OFF} o campo NÃO mascarado também não apareceu em store algum."
    echo "     Duas leituras possíveis, e o probe não as separa:"
    echo "       (a) o contato não chegou  ⇒ nada foi exercido, o verde acima é vazio;"
    echo "       (b) o redator suprime tudo ⇒ defeito oposto, igualmente grave."
    INCONCLUSIVO=1
else
    echo "  ${GRN}✔${OFF} \`email\` presente (CH=${N_EMAIL_CH} PG=${N_EMAIL_PG}) — o caminho foi exercido"
fi

# ── Veredicto ────────────────────────────────────────────────────────────────
echo
echo "═══════════════════════════════════════════════════════════════════"
if [ "$VAZOU" -eq 1 ]; then
    echo "${RED}VERMELHO${OFF} — valor de campo mascarado alcançou store durável."
    echo "  Os destinos marcados ✗ acima gravaram o canário."
    exit 1
elif [ "$INCONCLUSIVO" -eq 1 ]; then
    echo "${YEL}INCONCLUSIVO${OFF} — nada foi julgado."
    echo "  Um verde aqui seria por AUSÊNCIA de amostra, não por ausência de vazamento."
    exit 2
else
    echo "${GRN}VERDE${OFF} — nenhum valor mascarado em store; o não-mascarado sobreviveu."
    exit 0
fi
