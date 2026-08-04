#!/usr/bin/env bash
# PROBE (não é gate) — qual é a PEGADA que a queda de transporte já deixou?
#
# Fase E (D8) do ADR `adr-work-item-requeue-and-agent-affinity.md`. Antes de
# propor qualquer mudança, medir as duas coisas que a D8 afirma estarem
# contaminadas:
#
#   (1) quantos SEGMENTOS o demo já acumulou sem `close_reason` — a lacuna 6;
#   (2) quantos `agent_done` a queda já publicou, e quantos deles são falsos.
#
# Este probe NÃO semeia nada. Ele lê o que existe.
#
# ─────────────────────────────────────────────────────────────────────────────
# PREVISÃO ESCRITA ANTES DE RODAR (2026-08-04). Cada uma é falsificável, e o
# que cada desfecho significa está escrito junto — para o veredicto não ser
# costurado depois, em cima do número que aparecer.
#
#   P1  Segmento de IA (`agent_type='native'`) tem close_reason NULL em ~100%
#       dos casos. Base: NENHUM dos 4 call sites de participant_left para
#       `native` passa o campo (main.py:4336, :5245, :5452, :7756) — só os
#       humanos passam. CONSEQUÊNCIA: o número bruto "segmentos sem
#       close_reason" é dominado por IA e NÃO é a lacuna 6. Medir sem separar
#       por agent_type produziria um número grande, plausível e sobre outra
#       coisa.
#       → refutada se aparecer QUALQUER segmento native com close_reason: então
#         minha leitura dos call sites está errada e há um produtor que não li.
#
#   P2  Entre os segmentos HUMANOS sem close_reason, o transporte responsável é
#       `agent_disconnect` na maioria. Base: o mapa `_TRANSPORT_TO_CLOSE_REASON`
#       (main.py:3028) tem 6 entradas e nenhuma delas é `agent_disconnect`;
#       `agent_transfer` também está fora, mas é exercitado muito menos no demo.
#       → refutada se o transporte dominante nos WARNINGs for outro. Aí o
#         produtor concreto da lacuna 6 não é o F5, e a Fase E muda de alvo.
#
#   P3  Existe pelo menos UM par (session_id, participant_id) com 2+ segmentos
#       humanos — a "pilha por um único wrap-up" que a D8 descreve. Base: as
#       sessões de F5 de 2026-08-04 (probe_fase_b_release_on_reload).
#       → se 0, a pilha é hipótese não observada e a D8 perde seu dano medido.
#         Isso NÃO invalida a separação dos mapas (que tem produtor próprio).
#
#   P4  #(WARNING de transporte 'agent_disconnect' não mapeado)
#         ==
#       #(linhas "agent_done published to lifecycle (human agent)") originadas
#         de queda, na MESMA janela de log.
#       Base: os dois saem do MESMO handler e do mesmo passe — o WARNING em
#       main.py:6548 e o publish em :6676. Um por queda, cada.
#       → divergência ⇒ existe um segundo caminho publicando agent_done (ou
#         suprimindo o WARNING) que eu não li. Nesse caso a Fase E não pode ser
#         escrita só sobre `handle_agent_close`.
#
#   P5  (a correção que a leitura de código sugere, e que a medição confirma ou
#       derruba) — o `agent_done` de `agent.lifecycle` tem HOJE um único
#       consumidor que AGE sobre ele: o routing-engine (`remove_conversation`,
#       kafka_listener.py:308). A analytics-api mapeia agent_done → None
#       (models.py:448; a tabela `agent_events` saiu em 2026-07-28) e o
#       rules-engine só olha `agent_login`.
#       CONSEQUÊNCIA, se confirmado: a frase da D8 — *"pilha de agent_done
#       falsos … contaminando contagem, AHT e a bancada de agentes"* — está
#       certa no DANO e errada no VEÍCULO. Nada analítico lê o agent_done; quem
#       contamina contagem/AHT/bancada é a pilha de SEGMENTOS
#       (`conversations.participants` → `analytics.segments`). E deixar de
#       publicar o agent_done, isolado, não limpa relatório nenhum: só remove
#       uma liberação de capacidade.
#       → este probe não decide P5 sozinho (é fato de código, não de dado). Ele
#         imprime o que precisa ser conferido e onde. A confirmação vale como
#         EMENDA ao ADR, não como implementação.
#
# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENTO — por que há preflight
#
# Em 2026-08-04 um probe desta mesma família deu FALSO VERDE: o parser rodava
# num container sem o interpretador, a mensagem de erro saiu como valor, e o
# veredicto comparou duas leituras quebradas — iguais entre si. Aqui o leitor é
# provado ANTES de medir, contra valor conhecido, e aborta com código 3 se
# falhar. Nenhum bloco compara antes×depois: todos ramificam sobre a FORMA do
# valor final.
#
# Códigos de saída:  0 medido · 2 INCONCLUSIVO (sem amostra) · 3 instrumento quebrado
#
# Uso (da raiz do repo, demo no ar):
#   bash infra/test/probe_fase_e_drop_footprint.sh
#   SINCE_H=72 LOG_SINCE=48h bash infra/test/probe_fase_e_drop_footprint.sh
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CH_DB="plughub_demo"
SINCE_H="${SINCE_H:-48}"        # janela ClickHouse, em horas
LOG_SINCE="${LOG_SINCE:-48h}"   # janela de log do bridge

CH() { $COMPOSE exec -T clickhouse clickhouse-client -q "$1" < /dev/null 2>/dev/null | tr -d '\r'; }

echo "════════════════════════════════════════════════════════════════"
echo " PROBE Fase E — pegada da queda de transporte"
echo " tenant=$TENANT  janela CH=${SINCE_H}h  janela log=${LOG_SINCE}"
echo "════════════════════════════════════════════════════════════════"
echo

# ── PREFLIGHT 0 — o leitor de ClickHouse lê? ─────────────────────────────────
# Contra valor CONHECIDO. Sem isto, uma query que falha devolve string vazia e
# "0 segmentos sem close_reason" seria indistinguível de "✅ nenhum".
PF=$(CH "SELECT 41 + 1")
if [ "$PF" != "42" ]; then
  echo "  ⛔ INSTRUMENTO QUEBRADO — clickhouse-client devolveu '$PF' para 41+1."
  echo "     Nada abaixo seria medição. Conferir: $COMPOSE ps clickhouse"
  exit 3
fi
PF2=$(CH "SELECT count() FROM ${CH_DB}.segments")
case "$PF2" in
  ''|*[!0-9]*)
    echo "  ⛔ INSTRUMENTO QUEBRADO — count() em ${CH_DB}.segments devolveu '$PF2'"
    echo "     (esperado: um inteiro). Banco/tabela errados?"
    exit 3 ;;
esac
echo "  preflight CH ok — ${CH_DB}.segments tem $PF2 linha(s) no total (todos os tenants)"

# ── PREFLIGHT 1 — o parser de log extrai o transporte? ───────────────────────
# Testado contra uma linha SINTÉTICA, com o formato exato que o bridge emite
# (main.py:3054). Se o pipeline não extrair 'agent_disconnect' daqui, ele
# também não extrairia do log real — e o "0 ocorrências" seria mentira.
FAKE="close_reason: transporte 'agent_disconnect' não mapeado — segmento sai SEM close_reason (session=sess_ABC123). Acrescentar em _TRANSPORT_TO_CLOSE_REASON."
PF3=$(echo "$FAKE" | grep -oE "transporte '[^']+'" | sed "s/transporte '//; s/'//")
PF4=$(echo "$FAKE" | grep -oE "session=[A-Za-z0-9_.:-]+" | sed 's/session=//')
if [ "$PF3" != "agent_disconnect" ] || [ "$PF4" != "sess_ABC123" ]; then
  echo "  ⛔ INSTRUMENTO QUEBRADO — parser de log não extrai da linha sintética."
  echo "     transporte='$PF3' (esperado agent_disconnect)  session='$PF4' (esperado sess_ABC123)"
  exit 3
fi
echo "  preflight parser ok — extrai transporte e session da linha sintética"

# ── PREFLIGHT 2 — a janela de log é REAL? ────────────────────────────────────
# `--since 48h` não cria histórico: se o container subiu há 20 min, a janela é
# de 20 min. Dizer o contrário transformaria "não aconteceu" em "não coube".
CID=$($COMPOSE ps -q orchestrator-bridge 2>/dev/null | head -1)
if [ -n "$CID" ]; then
  STARTED=$(docker inspect -f '{{.State.StartedAt}}' "$CID" 2>/dev/null | tr -d '\r')
  echo "  orchestrator-bridge de pé desde: ${STARTED:-?}  (a janela de log NÃO passa disto)"
else
  echo "  ⚠️  não achei o container do orchestrator-bridge — a seção de LOG sairá inconclusiva"
fi
echo

WIN="tenant_id='$TENANT' AND started_at >= now() - INTERVAL $SINCE_H HOUR"

# ── BLOCO 1 — a base: o número bruto NÃO é a lacuna 6 ────────────────────────
echo "══ 1) segmentos sem close_reason — bruto × separado por tipo de agente ══"
TOT=$(CH "SELECT count() FROM ${CH_DB}.segments FINAL WHERE $WIN")
if [ -z "$TOT" ] || [ "$TOT" = "0" ]; then
  echo "  ⚠️  INCONCLUSIVO — 0 segmentos na janela de ${SINCE_H}h."
  echo "     Não é 'não há defeito': é ausência de amostra. Aumente com SINCE_H=."
  exit 2
fi
CH "SELECT agent_type,
           count()                                             AS segmentos,
           countIf(close_reason IS NULL OR close_reason = '')  AS sem_close_reason,
           round(100 * countIf(close_reason IS NULL OR close_reason = '') / count(), 1) AS pct
    FROM ${CH_DB}.segments FINAL
    WHERE $WIN
    GROUP BY agent_type ORDER BY segmentos DESC FORMAT PrettyCompact"
echo
echo "  → P1 diz: a linha 'native' tem pct ≈ 100. Se tiver, o número bruto está"
echo "    contando IA e não fala sobre a lacuna 6. Se NÃO tiver, minha leitura"
echo "    dos call sites está errada — parar e reler antes de propor."

# ── BLOCO 2 — só o que a lacuna 6 cobre: segmento HUMANO ─────────────────────
echo
echo "══ 2) segmentos HUMANOS: por pool, e o vocabulário de close_reason em uso ══"
HUM=$(CH "SELECT count() FROM ${CH_DB}.segments FINAL WHERE $WIN AND agent_type='human'")
HUM_NULL=$(CH "SELECT count() FROM ${CH_DB}.segments FINAL WHERE $WIN AND agent_type='human'
               AND (close_reason IS NULL OR close_reason = '')")
echo "  humanos=${HUM:-?}   sem close_reason=${HUM_NULL:-?}"
if [ "${HUM:-0}" = "0" ]; then
  echo "  ⚠️  INCONCLUSIVO para a lacuna 6 — nenhum segmento humano na janela."
  echo "     Atenda um contato no Console (ou rode smoke_formfill_renderer.sh e"
  echo "     reivindique) e rode de novo."
  exit 2
fi
CH "SELECT pool_id,
           if(endsWith(pool_id,'-int'),'fila interna','contato')  AS familia,
           count()                                                AS segmentos,
           countIf(close_reason IS NULL OR close_reason = '')     AS sem_cr
    FROM ${CH_DB}.segments FINAL
    WHERE $WIN AND agent_type='human'
    GROUP BY pool_id ORDER BY sem_cr DESC, segmentos DESC FORMAT PrettyCompact"
echo
echo "  vocabulário de close_reason efetivamente gravado (humanos):"
CH "SELECT ifNull(nullIf(close_reason,''),'∅ AUSENTE') AS close_reason,
           count() AS n
    FROM ${CH_DB}.segments FINAL
    WHERE $WIN AND agent_type='human'
    GROUP BY close_reason ORDER BY n DESC FORMAT PrettyCompact"
echo
echo "  → os dois domínios têm de aparecer separados aqui: contato"
echo "    (agent_hangup, customer_disconnect, session_timeout…) e segmento"
echo "    (task_submitted, acw_expired, acw_supervisor_closed). Se um valor de"
echo "    um domínio aparecer em pool do outro, o mapa compartilhado JÁ escolheu"
echo "    um domínio em silêncio — e isso é a Fase E, não hipótese."

# ── BLOCO 3 — a PILHA (P3): mesmo participante, vários segmentos ─────────────
echo
echo "══ 3) pilha de segmentos — mesmo (sessão, participante) fechado N vezes ══"
PILHA=$(CH "SELECT count() FROM (
              SELECT session_id, participant_id, count() AS n
              FROM ${CH_DB}.segments FINAL
              WHERE $WIN AND agent_type='human'
              GROUP BY session_id, participant_id HAVING n > 1)")
echo "  pares (sessão, participante) com 2+ segmentos: ${PILHA:-?}"
if [ "${PILHA:-0}" = "0" ]; then
  echo "  → P3 REFUTADA nesta janela: a pilha não foi observada."
  echo "    Isso NÃO derruba a separação dos mapas (produtor próprio, bloco 2),"
  echo "    mas derruba o dano MEDIDO que a D8 usa como justificativa. Registrar"
  echo "    como tal — e não reescrever a previsão para caber no resultado."
else
  echo "  → P3 confirmada. Detalhe (sem_cr = quantos daqueles segmentos saíram sem motivo):"
  CH "SELECT substring(session_id,1,12) AS sess,
             participant_id,
             any(pool_id)                                        AS pool_ref,
             count()                                             AS segmentos,
             countIf(close_reason IS NULL OR close_reason = '')   AS sem_cr,
             countIf(outcome IS NULL OR outcome = '')             AS sem_outcome
      FROM ${CH_DB}.segments FINAL
      WHERE $WIN AND agent_type='human'
      GROUP BY session_id, participant_id
      HAVING segmentos > 1
      ORDER BY segmentos DESC LIMIT 20 FORMAT PrettyCompact"
  echo
  echo "  (alias com sufixo _ref de propósito: em ClickHouse um alias de agregado"
  echo "   com o nome de coluna real sombreia a coluna e derruba a query inteira.)"
fi

# ── BLOCO 4 — o LOG: atribuição ao transporte, e o par WARNING × agent_done ───
echo
echo "══ 4) log do bridge — quem produziu a ausência, e quantos agent_done ══"
if [ -z "$CID" ]; then
  echo "  ⚠️  INCONCLUSIVO — container do bridge não localizado."
else
  LOGS=$($COMPOSE logs --since "$LOG_SINCE" --no-color orchestrator-bridge 2>/dev/null)
  NLINES=$(printf '%s\n' "$LOGS" | wc -l | tr -d ' ')
  echo "  linhas de log na janela: $NLINES"

  echo
  echo "  a) WARNINGs de transporte não mapeado, POR transporte:"
  UNMAPPED=$(printf '%s\n' "$LOGS" | grep -oE "transporte '[^']+'" | sed "s/transporte '//; s/'//" \
             | sort | uniq -c | sort -rn)
  if [ -z "$UNMAPPED" ]; then
    echo "     (nenhum)  — INCONCLUSIVO para P2: sem WARNING na janela, o log não"
    echo "     diz quem produziu os NULL do bloco 2. Eles podem ser anteriores ao"
    echo "     boot do container. Ausência de log ≠ ausência de defeito."
  else
    printf '%s\n' "$UNMAPPED" | sed 's/^/     /'
    DISC=$(printf '%s\n' "$UNMAPPED" | awk '$2=="agent_disconnect"{print $1}')
    echo "     → agent_disconnect: ${DISC:-0}"
  fi

  echo
  echo "  b) agent_done publicados para agent.lifecycle (humano):"
  DONE_LINES=$(printf '%s\n' "$LOGS" | grep -F "agent_done published to lifecycle (human agent)")
  NDONE=$(printf '%s\n' "$DONE_LINES" | grep -c "session=" )
  echo "     total: ${NDONE:-0}"
  echo "     por sessão (2+ = a pilha, do lado do lifecycle):"
  printf '%s\n' "$DONE_LINES" | grep -oE "session=[A-Za-z0-9_.:-]+" | sed 's/session=//' \
    | sort | uniq -c | sort -rn | head -20 | sed 's/^/       /'

  echo
  echo "  c) P4 — o par tem de bater (mesmo handler, mesmo passe):"
  echo "     WARNING agent_disconnect = ${DISC:-0}   ·   agent_done (humano) = ${NDONE:-0}"
  if [ -z "${DISC:-}" ] && [ "${NDONE:-0}" = "0" ]; then
    echo "     ⚠️  INCONCLUSIVO — nenhum dos dois na janela. Nada a comparar."
  elif [ "${DISC:-0}" = "${NDONE:-0}" ]; then
    echo "     ✔ iguais. Compatível com P4, MAS só prova o par se TODO agent_done"
    echo "       da janela veio de queda — confira em (b) se há sessão com"
    echo "       encerramento normal no meio. Igualdade aqui não é prova sozinha."
  else
    echo "     ✘ DIFERENTES. Um dos dois tem produtor a mais. Isto é resultado, não"
    echo "       ruído: a Fase E não pode ser escrita só sobre handle_agent_close"
    echo "       até saber qual. Próximo passo é achar a linha extra, não estimar."
  fi

  echo
  echo "  d) devolução pela Fase B (item de trabalho, não contato):"
  printf '%s\n' "$LOGS" | grep -cE "work_task_release|Fase B" | sed 's/^/     ocorrências: /'
fi

# ── FECHO — o que este probe NÃO decide ──────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════════════"
echo " NÃO DECIDIDO AQUI (não tratar como fato medido):"
echo "  · P5 — quem CONSOME o agent_done. É fato de código, não de dado."
echo "    Leitura atual, para conferência linha a linha:"
echo "      routing-engine/kafka_listener.py:308  → age (remove_conversation)"
echo "      analytics-api/models.py:448           → agent_done devolve None"
echo "                                              (tabela agent_events saiu 2026-07-28)"
echo "      rules-engine/main.py:12               → só agent_login"
echo "    Se isto se confirmar, a D8 acerta o DANO e erra o VEÍCULO: quem suja"
echo "    contagem/AHT/bancada é a pilha de SEGMENTOS, não o agent_done — e"
echo "    parar de publicá-lo, sozinho, só remove uma liberação de capacidade."
echo "  · se a vaga fica presa ao NÃO publicar agent_done no ramo de CONTATO"
echo "    de cliente (o ramo de item de trabalho libera pelo work_task_release)."
echo "════════════════════════════════════════════════════════════════"
