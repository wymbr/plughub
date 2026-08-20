#!/usr/bin/env bash
# gate_sentiment_engine_half.sh — a metade ENGINE da medição de sentimento.
#
# ── O que este gate julga, e o que ele NÃO julga ─────────────────────────────
# `probe_sentiment_producer.sh` prova a metade GATEWAY: dado um `customer_utterance`
# no corpo, o ai-gateway mede e os três emissores gravam. Ele chama `/v1/reason`
# DIRETO, então não diz nada sobre quem deveria preencher esse campo.
#
# Este gate julga a outra metade, e ela tem quatro elos que só existem em runtime:
#   1. o slot `current` do pool de fila carrega o flow que DECLARA o campo
#   2. o engine resolve a referência `$.pipeline_state.ultima_mensagem`
#      (`skill-flow-engine/src/steps/reason.ts:214`) — literal é recusado
#   3. o skill-flow-service repassa `customer_utterance` E `tenant_id`
#   4. o pool medido vem de `session:{id}:meta`, e num contato REAL ele não é
#      `unknown` — que é justamente o valor que a chamada sintética produz
#
# O elo 4 é o que dá a este gate um discriminador que o probe não tem: se o pool
# sair `unknown`, a medição veio de chamada sintética e não de contato.
#
# ── REPRODUÇÃO (manual, como no gate da fila) ────────────────────────────────
# O contato precisa ENFILEIRAR, e para isso o pool humano tem de estar sem
# capacidade livre. Com alguém logado e pronto o contato é roteado direto, o agente
# de fila nunca ativa, e "0 medições" seria ausência de amostra, não defeito.
#
#   1. bash infra/scripts/deploy_skill_to_slot.sh \
#        packages/skill-flow-engine/skills/agente_fila_v1.yaml fila_humano customer_utterance
#   2. pause (ou logout) do humano em `retencao_humano` no Console
#   3. T0=$(date -u +%FT%TZ); echo "$T0"        ← instante ABSOLUTO, nunca --since 300s
#   4. webchat → escalar para especialista → esperar a mensagem de fila →
#      mandar uma fala IRRITADA → esperar a resposta do agente de fila
#   5. bash infra/test/gate_sentiment_engine_half.sh "$T0"
#
# Sai: 0 = VERDE · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail

DC="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
T0="${1:-}"
TENANT="${TENANT:-tenant_demo}"
AR="${AGENT_REGISTRY_URL:-http://localhost:3300}"
AIGW="${AIGW:-http://localhost:3200}"
POOL_FILA="${POOL_FILA:-fila_humano}"

[ -n "$T0" ] || {
  echo "uso: bash infra/test/gate_sentiment_engine_half.sh <T0-ISO-UTC>"
  echo "     T0=\$(date -u +%FT%TZ)   ← pegue ANTES de abrir o contato"; exit 3; }

R() { $DC exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }
die() { echo "   ⛔ INCONCLUSIVO: $1"; exit 3; }

echo "══ sentimento · metade ENGINE — janela desde $T0 ══"
echo

# ── Preflight 1 · o flow que RODA declara o campo? ───────────────────────────
# Não basta o YAML no repo: o bridge executa o `yaml_snapshot` do slot `current`.
# Editar o arquivo e reiniciar é NO-OP (skills são seed-if-absent).
echo "── preflight 1 · slot current de $POOL_FILA ──────────────────────────────"
SLOTS="$(curl -s --max-time 10 -H "x-tenant-id: $TENANT" "$AR/v1/pools/$POOL_FILA/slots" 2>/dev/null)"
if [ -z "$SLOTS" ]; then
  die "agent-registry inalcançável em $AR"
fi
if ! printf '%s' "$SLOTS" | grep -q 'customer_utterance'; then
  echo "      (resposta crua, primeiros 300 chars:)"
  printf '%s' "$SLOTS" | head -c 300; echo
  die "o snapshot promovido NÃO contém \`customer_utterance\`. O engine não tem o que
        resolver, e um vermelho abaixo seria sobre o flow ANTIGO. Publicar:
          bash infra/scripts/deploy_skill_to_slot.sh \\
            packages/skill-flow-engine/skills/agente_fila_v1.yaml $POOL_FILA customer_utterance"
fi
echo "      ✅ o snapshot em execução declara customer_utterance"

# ── Preflight 2 · há provedor de LLM? ────────────────────────────────────────
HCODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$AIGW/v1/health" 2>/dev/null)"
echo "── preflight 2 · ai-gateway /v1/health : $HCODE ──────────────────────────"
[ "$HCODE" = "200" ] || die "ai-gateway não saudável ($HCODE) — sem provedor nada é medido,
        e a ausência de medição não julgaria o engine."

# ── Testemunha · o agente de fila chegou a raciocinar? ───────────────────────
# Contador de AUSÊNCIA precisa de contador de PRESENÇA ao lado: "0 medições" com
# "0 reasons" é teste não reproduzido; com "N reasons" é achado.
echo
echo "── testemunha · o caminho foi percorrido? ────────────────────────────────"
LOG="$($DC logs --no-log-prefix -t --since "$T0" ai-gateway 2>/dev/null | tr -d '\r')"
N_REASON="$(printf '%s\n' "$LOG" | grep -c 'POST /v1/reason')"
N_MEDIDO="$(printf '%s\n' "$LOG" | grep -c 'sentiment: medido')"
N_NAOMED="$(printf '%s\n' "$LOG" | grep -c 'sentimento NÃO medido')"
echo "      POST /v1/reason na janela : $N_REASON"
echo "      'sentiment: medido'       : $N_MEDIDO"
echo "      'sentimento NÃO medido'   : $N_NAOMED"

if [ "$N_REASON" -eq 0 ]; then
  die "nenhum step \`reason\` rodou na janela. O contato não chegou ao agente de fila.
        Causa ordenada, da mais provável:
          (a) havia humano PRONTO em retencao_humano e o contato foi roteado direto
              (confira — a chave é STRING JSON, HGETALL nela dá WRONGTYPE:
               $DC exec -T redis redis-cli GET $TENANT:pool:retencao_humano:snapshot)
          (b) o contato não foi escalado para o pool humano
          (c) o agente de fila não ativou (rodar gate_queue_segment_not_born_without_flow.sh)"
fi

# ── Medida ───────────────────────────────────────────────────────────────────
echo
echo "── medida ────────────────────────────────────────────────────────────────"
MEDIDAS="$(printf '%s\n' "$LOG" | grep 'sentiment: medido' || true)"
[ -n "$MEDIDAS" ] && printf '%s\n' "$MEDIDAS" | sed 's/^/      /'

if [ "$N_MEDIDO" -eq 0 ]; then
  echo
  echo "   ❌ O ENGINE NÃO ENVIOU A FALA — $N_REASON reason(s) rodaram e nenhuma medição."
  echo "      O gateway está provado (probe_sentiment_producer.sh); logo o campo não"
  echo "      chegou nele. Elos a conferir, nesta ordem:"
  echo "      · a referência resolveu? \`ultima_mensagem\` vazio devolve undefined e o"
  echo "        engine OMITE o campo — desfecho honesto, mas indistinguível daqui;"
  echo "      · o skill-flow-service repassou o payload inteiro?"
  echo "      · a fala foi um SENTINELA (__agent_available__ / __queue_timeout__)? Esses"
  echo "        desviam antes do step, então não deveriam chegar."
  [ "$N_NAOMED" -gt 0 ] && echo "      · há $N_NAOMED 'sentimento NÃO medido' na janela — leia o motivo no log."
  exit 1
fi

# ── Discriminador · veio de contato REAL, ou de chamada sintética? ───────────
#
# ⚠️ A v1 deste gate classificava por `pool=unknown`, e estava ERRADA: em 2026-08-24
# um contato REAL mediu -0.50 e saiu com `pool=unknown`, e o gate o anunciou como
# "chamada sintética do probe". Dois fatos independentes tinham sido colapsados num
# só sinal — DE ONDE veio a medição, e se o pool pôde ser resolvido.
#
# Origem se decide pela FORMA DO ID (o probe usa o prefixo `probe-sent-`; contato
# real é UUID). Pool irresolvível é achado PRÓPRIO, com mensagem própria — e foi
# assim que se descobriu que o ai-gateway lia `session:{id}:meta` com HGET numa
# chave que é String (JSON).
echo
echo "── discriminador · contato real × chamada sintética ──────────────────────"
SINTETICAS="$(printf '%s\n' "$MEDIDAS" | grep -E 'session=probe-' || true)"
REAIS="$(printf '%s\n' "$MEDIDAS" | grep -Ev 'session=probe-' | grep . || true)"
N_SINT="$(printf '%s\n' "$SINTETICAS" | grep -c . )"
N_REAL="$(printf '%s\n' "$REAIS" | grep -c . )"
echo "      medições sintéticas (session=probe-*) : $N_SINT"
echo "      medições de contato real              : $N_REAL"

if [ "$N_REAL" -eq 0 ]; then
  echo
  echo "   ❌ Só houve medição SINTÉTICA na janela — todas as sessões têm o prefixo do"
  echo "      probe. O contato não produziu \`reason\`, ou a janela pegou apenas o"
  echo "      tráfego do \`probe_sentiment_producer.sh\`. A metade engine não foi"
  echo "      exercitada; este é o caso em que o verde compraria cobertura falsa."
  exit 1
fi

# ── Pool resolvido? (fato SEPARADO da origem) ────────────────────────────────
N_REAL_UNK="$(printf '%s\n' "$REAIS" | grep -c 'pool=unknown' || true)"
echo "      destas, com pool=unknown              : $N_REAL_UNK"
if [ "$N_REAL_UNK" -gt 0 ]; then
  echo
  echo "   ❌ $N_REAL_UNK medição(ões) de CONTATO REAL agregada(s) sob 'unknown'."
  echo "      A medição funcionou; o que falhou foi resolver o pool a partir de"
  echo "      \`session:{id}:meta\`. Essa chave é String (JSON) — ler com HGET levanta"
  echo "      WRONGTYPE e o except devolve 'unknown', com o dado presente na chave o"
  echo "      tempo todo. Conferir o motivo NOMEADO no log (são quatro ramos):"
  echo "         $DC logs ai-gateway | grep -i 'sentiment:.*unknown\\|falha ao LER'"
  echo "      E o estado da chave:"
  echo "         $DC exec -T redis redis-cli GET session:<sid>:meta"
  exit 1
fi

# ── O dado está legível onde alguém o lê? ────────────────────────────────────
SID="$(printf '%s\n' "$REAIS" | tail -1 | grep -oE 'session=[^ ]+' | cut -d= -f2)"
POOL="$(printf '%s\n' "$REAIS" | tail -1 | grep -oE 'pool=[^ ]+' | cut -d= -f2)"
echo
echo "── leitura · o score está no ctx da sessão e no agregado do pool? ────────"
echo "      sessão : $SID"
echo "      pool   : $POOL"
CTX="$(R HGET "$TENANT:ctx:$SID" 'session.sentimento.current')"
LIVE="$(R HGETALL "$TENANT:pool:$POOL:sentiment_live")"
echo "      ctx    : ${CTX:-<ausente>}"
echo "      live   : $(printf '%s' "$LIVE" | tr '\n' ' ')"

SCORE="$(printf '%s' "$CTX" | sed -n 's/.*"value"[[:space:]]*:[[:space:]]*\(-\{0,1\}[0-9][0-9.]*\).*/\1/p')"
if [ -z "$CTX" ]; then
  echo
  echo "   ❌ o log diz MEDIDO e o ctx da sessão está vazio — o score foi calculado e"
  echo "      não chegou a quem o lê. Conferir a ordem das escritas em"
  echo "      sentiment_analyzer.py (Redis antes do Kafka, desde 2026-08-24)."
  exit 1
fi
if [ -z "$SCORE" ]; then
  die "ctx presente mas o campo \`value\` não foi legível — a forma do ContextEntry
        mudou? Ajustar o extrator antes de julgar. Valor cru: $CTX"
fi

echo
echo "   ✅ A METADE ENGINE ESTÁ PROVADA — $N_REAL medição(ões) de contato real."
echo "      A fala do cliente saiu do pipeline_state, atravessou engine →"
echo "      skill-flow-service → ai-gateway, e virou score $SCORE no ctx da sessão,"
echo "      agregado sob o pool '$POOL' (não 'unknown')."
echo
echo "   ⚠️  LIMITE: este gate NÃO julga se o score está CERTO para a fala — para isso"
echo "      é preciso saber o que o cliente digitou, e o gate não tem essa entrada."
echo "      O sinal é julgado no probe, onde a fala é controlada."
exit 0
