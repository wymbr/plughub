#!/usr/bin/env bash
# probe_sentiment_producer.sh — a plataforma MEDE sentimento, ou só parece medir?
#
# ── O que este probe julga (contrato de 2026-08-23) ──────────────────────────
# Sentimento é medido por uma chamada DEDICADA ao modelo, fora do turno, disparada
# por `/v1/reason` **somente quando o step NOMEOU a fala do cliente** em
# `ReasonRequest.customer_utterance` (`main.py:498` → `analyze_and_emit_sentiment`).
# O gateway não adivinha: o `input` do reason é opaco por contrato.
#
# Saídas, os três emissores que já existiam:
#   · Kafka  `sentiment.updated`
#   · Redis  {tenant}:pool:{pool}:sentiment_live        (TTL 300 s)
#   · ctx    {tenant}:ctx:{session} → core.sentiment.current   (TTL 4 h)
#
# ⚠️ Contrato ANTERIOR, deliberadamente aposentado — não voltar a testá-lo:
# `/v1/reason` lia `sentiment_score` do `output_schema` que o SKILL declarava. Como
# nenhum skill o declarava, o valor era sempre 0.0 — neutro, indistinguível de
# não-medido. E sentimento auto-reportado pelo modelo que está atendendo é o
# avaliado dando a própria nota. A v1 deste probe testava esse caminho.
#
# ── DESENHO DO TESTE ────────────────────────────────────────────────────────
# Duas chamadas que diferem em UMA coisa só: a presença de `customer_utterance`.
# É essa diferença que torna o resultado falseável — "existe chave de sentimento
# no Redis" não julga nada sozinho, porque a chave pode ter vindo de outro tráfego.
#
#   A · SEM customer_utterance  → TESTEMUNHA NEGATIVA: nada pode ser escrito.
#                                 Se A escrever, o campo não está gateando e o
#                                 gateway está medindo texto que ninguém nomeou.
#   B · COM customer_utterance  → tem de escrever, e com score NEGATIVO: a fala
#                                 é uma reclamação irritada. Score ≥ 0 significa
#                                 que mediu OUTRA COISA (pipeline_state, prompt),
#                                 que é o modo de falha caro — mede e mente.
#
# PREVISÕES:
#   P-A  ctx de A ausente · P-B ctx de B presente e < 0 · sentiment_live de B presente
#
# Veredicto de TRÊS estados: 0 = mede · 1 = não mede / mede errado · 3 = INCONCLUSIVO
set -uo pipefail

COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
TENANT="${TENANT:-tenant_demo}"
AIGW="${AIGW:-http://localhost:3200}"

R() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }

echo "══ medição de sentimento — a plataforma mede? ══"
[ "$(R PING)" = "PONG" ] || { echo "   ⛔ INCONCLUSIVO — redis inalcançável"; exit 3; }

# ── Pré-flight: a credencial de LLM está de pé? ──────────────────────────────
# Sem provedor não há medição, e a falha seria lida como "a trilha não escreve".
# O /v1/health já responde isso na hora, com a causa nomeada.
echo
echo "── pré-flight · credencial de LLM ────────────────────────────────────────"
HCODE="$(curl -s -o /tmp/_sent_health.json -w '%{http_code}' --max-time 10 "$AIGW/v1/health" 2>/dev/null)"
echo "      GET /v1/health : $HCODE"
if [ "$HCODE" != "200" ]; then
  echo "   ⛔ INCONCLUSIVO — ai-gateway não está saudável (corpo abaixo)."
  head -c 400 /tmp/_sent_health.json 2>/dev/null; echo
  echo "      Sem provedor a medição não roda, e a ausência de escrita NÃO julga o código."
  exit 3
fi

# ── Contexto passivo (nunca aborta) ──────────────────────────────────────────
# Estes números situam, não decidem. Na v1 o P1 dava `exit 3` quando o log estava
# vazio — e o log fica vazio a cada recriação do container, matando justamente a
# parte ATIVA, que é a única que julga. Contexto não pode vetar o experimento.
echo
echo "── contexto · tráfego já ocorrido nesta janela de log ────────────────────"
LOG="$($COMPOSE logs ai-gateway 2>/dev/null | tr -d '\r')"
echo "      linhas de log                    : $(printf '%s\n' "$LOG" | grep -c .)"
echo "      POST /v1/reason                  : $(printf '%s\n' "$LOG" | grep -c 'POST /v1/reason')"
echo "      'sentiment: medido'              : $(printf '%s\n' "$LOG" | grep -c 'sentiment: medido')"
echo "      'sentimento NÃO medido'          : $(printf '%s\n' "$LOG" | grep -c 'sentimento NÃO medido')"
echo "      (log recriado junto com o container — 0 aqui não é ausência de comportamento)"

# ── O experimento ────────────────────────────────────────────────────────────
echo
echo "── experimento · duas chamadas, uma diferença ────────────────────────────"
SA="probe-sent-sem-$$"; SB="probe-sent-com-$$"
MSG='Já é a terceira vez que ligo e ninguém resolve. Estou muito irritado.'

# A forma do `output_schema` é {campo: {type: ...}}, copiada de um skill real
# (agente_fila_v1.yaml) — a v1 mandou {"resposta":"string"} e levou 422, e teria
# concluído "não escreveu" sobre requisição que nem chegou ao código sob teste.
call_reason() {   # $1 = session_id · $2 = arquivo de saída · $3 = bloco extra (pode ser vazio)
  curl -s --max-time 90 -X POST "$AIGW/v1/reason" -H 'content-type: application/json' \
    -d "{\"session_id\":\"$1\",\"tenant_id\":\"$TENANT\",\"prompt_id\":\"probe\",
         \"input\":{\"mensagem_cliente\":\"$MSG\"},
         \"output_schema\":{\"resposta\":{\"type\":\"string\"}}$3}" > "$2"
}

call_reason "$SA" /tmp/_sent_a.json ""
echo "      A · SEM customer_utterance : $(head -c 160 /tmp/_sent_a.json)"

call_reason "$SB" /tmp/_sent_b.json ",\"customer_utterance\":\"$MSG\""
echo "      B · COM customer_utterance : $(head -c 160 /tmp/_sent_b.json)"

# Dois modos de "não rodou", com mensagens diferentes. Casar por "detail" — campo
# que as DUAS formas têm — fazia um 401 do provedor ser anunciado como 422.
if grep -q 'upstream_model_error' /tmp/_sent_b.json 2>/dev/null; then
  echo
  echo "   ⛔ INCONCLUSIVO — o PROVEDOR recusou a chamada do turno."
  echo "      Sem LLM nada pós-turno roda; a ausência de escrita não julga o código."
  exit 3
fi
if grep -q '"loc"' /tmp/_sent_b.json 2>/dev/null; then
  echo "   ⛔ INCONCLUSIVO — /v1/reason recusou o CORPO (422). O caminho sob teste"
  echo "      não foi exercitado; conserte o corpo antes de ler o veredicto."
  exit 3
fi

# A medição é fire-and-forget: uma SEGUNDA chamada ao modelo, disparada depois da
# resposta. Ler o Redis imediatamente mediria a latência do haiku, não a escrita.
echo
echo "      aguardando a task de background (chamada dedicada ao modelo)..."
CTX_B=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 2
  CTX_B="$(R HGET "$TENANT:ctx:$SB" 'core.sentiment.current')"
  [ -n "$CTX_B" ] && break
done

CTX_A="$(R HGET "$TENANT:ctx:$SA" 'core.sentiment.current')"
LIVE="$(R --scan --pattern "$TENANT:pool:*:sentiment_live")"
N_LIVE="$(printf '%s\n' "$LIVE" | grep -c .)"

# O ctx NÃO guarda um número: guarda um ContextEntry
# {value, confidence, source, visibility, updated_at}. A v2 deste probe comparou a
# BLOB inteira com awk — que avalia string não-numérica como 0, logo "não < 0" — e
# acusou "mediu o texto errado" sobre uma medição CORRETA de -0.5. Acusação
# confiante e falsa é pior que reprovar: manda consertar o que está certo.
extract_score() {  # ContextEntry JSON → o campo `value`, ou vazio se ilegível
  printf '%s' "$1" | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9.]*\).*/\1/p'
}
SCORE_B="$(extract_score "$CTX_B")"

echo
echo "── medidas ───────────────────────────────────────────────────────────────"
echo "      ctx de A (SEM utterance) : ${CTX_A:-<ausente>}   ← previsto: ausente"
echo "      ctx de B (COM utterance) : ${CTX_B:-<ausente>}"
echo "      score extraído de B      : ${SCORE_B:-<ilegível>}   ← previsto: < 0"
echo "      chaves *:sentiment_live  : $N_LIVE"
[ "$N_LIVE" -gt 0 ] && printf '%s\n' "$LIVE" | sed 's/^/         /'

# ── Veredicto ────────────────────────────────────────────────────────────────
echo
if [ -n "$CTX_A" ]; then
  echo "   ❌ A TESTEMUNHA NEGATIVA ESCREVEU — a chamada SEM customer_utterance"
  echo "      produziu sentimento ($CTX_A). O campo não está gateando a medição, e o"
  echo "      gateway está pontuando texto que ninguém nomeou como fala de cliente."
  exit 1
fi

if [ -z "$CTX_B" ]; then
  echo "   ❌ NÃO MEDIU — a chamada COM customer_utterance não escreveu nada em 20 s."
  echo "      Próximo passo é o LOG, não o código: cada saída do analisador diz por"
  echo "      que saiu (sem provider · tenant vazio · modelo falhou · resposta ilegível):"
  echo "         $COMPOSE logs --tail 80 ai-gateway | grep -i sentiment"
  exit 1
fi

# Ilegível ≠ errado. Sem conseguir LER o score não há veredicto — o probe tem de
# se declarar inconclusivo em vez de escolher um ramo por default.
if [ -z "$SCORE_B" ]; then
  echo "   ⛔ INCONCLUSIVO — o ctx foi escrito, mas o campo \`value\` não foi legível:"
  echo "      $CTX_B"
  echo "      A forma do ContextEntry mudou? Ajuste o extrator antes de julgar o score."
  exit 3
fi

# Sinal, não magnitude: a fala é uma reclamação irritada. Score ≥ 0 não é "modelo
# generoso" — é forte indício de que mediu OUTRO texto, que é a falha cara.
if ! awk -v s="$SCORE_B" 'BEGIN{exit !(s+0 < 0)}'; then
  echo "   ❌ MEDIU, MAS O SINAL ESTÁ ERRADO — score $SCORE_B para uma reclamação"
  echo "      irritada. Suspeita ordenada: mediu o texto errado (pipeline_state,"
  echo "      prompt do sistema) e não a fala do cliente. Conferir o que chegou em"
  echo "      ReasonRequest.customer_utterance antes de culpar o modelo."
  exit 1
fi

echo "   ✅ A PLATAFORMA MEDE — score $SCORE_B para a fala do cliente, e a chamada"
echo "      sem \`customer_utterance\` não escreveu nada (testemunha negativa limpa)."
echo
echo "      Escopo do que este gate PROVA e do que NÃO prova:"
echo "      · prova a metade GATEWAY (contrato → analisador → três emissores);"
echo "      · NÃO prova a metade ENGINE — que um skill real resolva a referência"
echo "        \$./@ctx. e a envie. Isso exige contato de verdade sobre um skill que"
echo "        declare o campo (hoje: agente_fila_v1.responder_cliente)."
exit 0
