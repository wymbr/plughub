#!/usr/bin/env bash
# gate_family_b_resume_closes.sh — Pendência 3 do handoff de 2026-08-18.
#
# PERGUNTA. O conserto de 2026-08-18 (`key=session_id` no publish de
# `conversations.participants` + `ReplacingMergeTree(row_version)` em `segments` e
# `participation_intervals`) foi validado numa forma só: a FALHA RÁPIDA da fila
# (`role='queue'`, 3 ms). A família B — `primary`/`specialist` órfãos de
# suspend/resume — nunca foi reproduzida DEPOIS do conserto. O raciocínio de que
# "são o mesmo defeito" é INFERÊNCIA (mesmo publish, mesma ausência nas duas
# tabelas), não medição. Este gate produz a medição que falta.
#
# O QUE ELE MEDE, e o que NÃO mede:
#
#   MEDE   o ciclo `primary` nativo de webhook: trigger → delegate que SUSPENDE →
#          resume → complete. Exercita os quatro publishes do caminho:
#            main.py:4352 joined  (process_routed, role=primary, native)
#            main.py:4653 left    (mesmo, outcome='suspended')  ← o que sumia
#            main.py:8210 joined  (_handle_webhook_session_resumed)
#            main.py:8241 left    (mesmo)
#
#   NÃO MEDE o `specialist`. O único join com esse papel é `process_routed:4352`
#          com `conference_id` preenchido (delegate-as-conference), e o skill deste
#          gate delega a um pool PULL, não a uma conferência. Declarar que o
#          specialist está coberto por este verde seria repetir exatamente o erro
#          que a Pendência 3 aponta: tratar inferência como medição.
#
# POR QUE DIFERENCIAL, e não "conta 9 e compara". A baseline histórica (5·2·2 = 9,
# toda anterior a 2026-08-15) é PASSADO NÃO REPARADO e não vai mudar: o merge já
# apagou a linha perdedora. Um gate que olhasse só o total leria 9 para sempre e
# nunca poderia ficar vermelho. Por isso a janela é `started_at >= T0`, com T0 lido
# do PRÓPRIO ClickHouse (relógio do host não serve — skew entre container e host
# deslocaria a janela e o gate mediria zero amostra achando que mediu zero defeito).
#
# TESTEMUNHAS (um contador de ausência sozinho não julga nada):
#   · `total_janela`      — quantos segmentos a janela pegou. 0 ⇒ INCONCLUSIVO.
#   · `fechados_suspended`— quantos fecharam com `outcome='suspended'`. É a metade
#                           do ciclo que sumia; 0 aqui ⇒ o suspend não aconteceu e o
#                           verde do `abertos=0` seria "nenhuma amostra", não "nada
#                           aberto".
#   · `abertos_fora`      — abertos na janela em sessão AINDA NÃO fechada. Separa
#                           "não fechou" de "a sessão ainda está viva".
#
# Uso (raiz do repo, demo no ar):
#   bash infra/test/gate_family_b_resume_closes.sh [N_REPRODUCOES]   # default 3
#
# Sai: 0 = VERDE · 1 = REPROVOU (o defeito voltou / nunca foi) · 2 = INCONCLUSIVO
# Read-only quanto a config: só dispara workflows do demo. NÃO roda o e2e (que
# apagaria `tenant_demo:*` e levaria junto instâncias, claims e sessões vivas).

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
DB="${DB:-plughub_demo}"
CG="${CG:-http://localhost:8010}"
POOL_WH="${POOL_WH:-formfill_demo_ia}"     # pool webhook cujo skill SUSPENDE
POOL_PULL="${POOL_PULL:-formfill_demo}"    # destino do delegate (pull, sem claim)
N="${1:-3}"

chq()   { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }
redis() { $DC exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }

PASS=0; FAIL=0
ok()  { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad() { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
die() { echo "   ⛔ INCONCLUSIVO: $1"; exit 2; }

echo "══ família B — o segmento de suspend/resume FECHA? · tenant=$TENANT ══"
echo "   pool webhook=$POOL_WH · destino do delegate=$POOL_PULL · reproduções=$N"
echo

# ── PREFLIGHT — PROVAR QUE CADA INSTRUMENTO RESPONDE ────────────────────────────
command -v jq   >/dev/null || die "jq ausente"
command -v curl >/dev/null || die "curl ausente"
[ "$(redis PING)" = "PONG" ] || die "redis não respondeu PONG pelo compose"
curl -fsS "$CG/health" >/dev/null 2>&1 || die "channel-gateway fora do ar em $CG"
PING=$(chq 'SELECT 1' | tr -d '\r')
[ "$PING" = "1" ] || die "clickhouse não respondeu ('$PING')"

# O leitor tem de LER antes de qualquer comparação. Tabela vazia e leitor quebrado
# devolvem o mesmo 0, e 0 aqui seria lido como "nenhum órfão".
NSEG=$(chq "SELECT count() FROM $DB.segments FINAL WHERE tenant_id='$TENANT'" | tr -d '\r')
case "${NSEG:-x}" in
  ''|*[!0-9]*) die "contagem de segmentos não numérica ('$NSEG') — prefixo errado?" ;;
  0)           die "nenhum segmento no tenant — base vazia ou tenant errado" ;;
esac
echo "   preflight: redis=PONG · gateway=ok · clickhouse=ok · segments=$NSEG"

# ── BASELINE, contada (previsão de delta sem base não é falseável) ──────────────
BASE=$(chq "
  SELECT count()
    FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL" | tr -d '\r')
echo "   baseline de abertos em sessão fechada (todo o histórico): ${BASE:-?}"
echo

# ── T0 — do relógio do ClickHouse, nunca do host ────────────────────────────────
T0=$(chq "SELECT toString(now64(3,'UTC'))" | tr -d '\r')
case "$T0" in
  20*) : ;;
  *)   die "T0 não parece um timestamp ('$T0') — sem janela não há medição" ;;
esac
echo "── T0 (relógio do ClickHouse) = $T0 ───────────────────────────────────────"
echo

# ── AS REPRODUÇÕES ──────────────────────────────────────────────────────────────
SIDS=""
for i in $(seq 1 "$N"); do
  echo "── reprodução $i/$N ───────────────────────────────────────────────────────"

  TRIG=$(curl -fsS -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" \
           -H 'content-type: application/json' \
           -d "{\"tenant_id\":\"$TENANT\",\"context\":{\"session.briefing_session_id\":\"sess_gate_familia_b\"}}" \
           2>/dev/null)
  SID=$(printf '%s' "$TRIG" | jq -r '.session_id // empty' 2>/dev/null)
  [ -n "$SID" ] || die "o trigger não devolveu session_id (corpo: $(printf '%s' "$TRIG" | head -c 200)).
        Sem sessão não há suspend, e isto NÃO é 'o segmento fechou'.
        Suspeita 1: o pool '$POOL_WH' não tem slot \`current\` — o bridge executa o
        SNAPSHOT do slot, não o skill publicado. Conferir com
        infra/scripts/deploy_skill_to_slot.sh."
  echo "   sessão=$SID"

  # O token só existe DEPOIS que o delegate suspendeu. Esperar por ele é esperar
  # pelo suspend — e a ausência dele em 20 s é ausência de amostra, não verde.
  TOKEN=""
  for _ in $(seq 1 20); do
    TOKEN=$(redis HGETALL "$TENANT:resume_tokens" | paste - - | grep -F "$SID" | head -1 | cut -f1)
    [ -n "$TOKEN" ] && break
    sleep 1
  done
  [ -n "$TOKEN" ] || die "nenhum resume_token para $SID em 20 s — o delegate não suspendeu.
        O ciclo que este gate julga não chegou a existir."
  echo "   token=$TOKEN (o delegate suspendeu ⇒ o segmento 1 deveria ter fechado)"

  RCODE=$(curl -s -o /tmp/_fb_resume -w '%{http_code}' --max-time 25 \
            -X POST "$CG/v1/channels/webhook/resume/$TOKEN" \
            -H 'content-type: application/json' \
            -d "{\"tenant_id\":\"$TENANT\",\"payload\":{\"source\":\"operator\",\"answers\":{\"satisfacao\":\"sim\",\"observacao\":\"gate familia B\"}}}")
  case "$RCODE" in
    2*) echo "   resume aceito (HTTP $RCODE)" ;;
    *)  die "resume devolveu HTTP $RCODE — corpo: $(head -c 200 /tmp/_fb_resume).
        Pré-condição do gate falhou; não há conclusão sobre o segmento." ;;
  esac

  # Esperar o FECHAMENTO da sessão: o gate só olha sessão fechada, e medir antes
  # do close leria 'aberto' de um segmento que ainda vai fechar.
  CLOSED=""
  for _ in $(seq 1 30); do
    CLOSED=$(chq "SELECT count() FROM $DB.sessions FINAL
                   WHERE tenant_id='$TENANT' AND session_id='$SID'
                     AND closed_at IS NOT NULL" | tr -d '\r')
    [ "${CLOSED:-0}" -gt 0 ] 2>/dev/null && break
    sleep 2
  done
  [ "${CLOSED:-0}" -gt 0 ] 2>/dev/null \
    || die "a sessão $SID não fechou em 60 s — o gate mede sessão FECHADA, e um
        segmento aberto aqui seria indistinguível de 'ainda em curso'."
  echo "   sessão fechada"
  SIDS="$SIDS'$SID',"
  echo
done
SIDS="${SIDS%,}"

# Deixar o consumidor alcançar as últimas linhas. Não é 'esperar dar certo': a
# testemunha `total_janela` reprova se a espera não bastou.
echo "   aguardando ingestão do ClickHouse (10 s) ..."
sleep 10
echo

# ── 1. O QUE A JANELA PEGOU, nomeado ────────────────────────────────────────────
echo "── 1. segmentos das sessões reproduzidas (contar não é identificar) ───────"
chq "
  SELECT g.session_id            AS sessao,
         g.role                  AS papel,
         g.agent_type            AS tipo,
         g.pool_id               AS pool,
         g.started_at            AS abriu,
         g.ended_at              AS fechou,
         g.duration_ms           AS dur_ms,
         g.outcome               AS outcome,
         g.close_reason          AS close_reason
    FROM $DB.segments AS g FINAL
   WHERE g.tenant_id='$TENANT' AND g.session_id IN ($SIDS)
   ORDER BY g.session_id, g.started_at
   FORMAT PrettyCompactMonoBlock"
echo

# ── 2. abertos × total NA JANELA, por papel ─────────────────────────────────────
echo "── 2. abertos × total na janela (>= T0, sessão FECHADA) ──────────────────"
chq "
  SELECT g.role                        AS papel,
         g.agent_type                  AS tipo,
         countIf(g.ended_at IS NULL)   AS abertos,
         count()                       AS total
    FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT' AND g.started_at >= toDateTime64('$T0',3,'UTC')
   GROUP BY papel, tipo
   ORDER BY papel, tipo
   FORMAT PrettyCompactMonoBlock"
echo

# ── as três testemunhas + o contador que julga ──────────────────────────────────
Q_WINDOW="FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT' AND g.started_at >= toDateTime64('$T0',3,'UTC')"

TOTAL_J=$(chq "SELECT count() $Q_WINDOW" | tr -d '\r')
ABERTOS_J=$(chq "SELECT count() $Q_WINDOW AND g.ended_at IS NULL" | tr -d '\r')
SUSP_J=$(chq "SELECT count() $Q_WINDOW AND g.outcome='suspended'" | tr -d '\r')
ABERTOS_FORA=$(chq "
  SELECT count()
    FROM $DB.segments AS g FINAL
   WHERE g.tenant_id='$TENANT'
     AND g.started_at >= toDateTime64('$T0',3,'UTC')
     AND g.ended_at IS NULL
     AND g.session_id NOT IN (SELECT session_id FROM $DB.sessions FINAL
                               WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL)" | tr -d '\r')
FINAL_TOTAL=$(chq "
  SELECT count()
    FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL" | tr -d '\r')

echo "── 3. testemunhas ─────────────────────────────────────────────────────────"
echo "   total_janela        = ${TOTAL_J:-?}   (0 ⇒ INCONCLUSIVO, não verde)"
echo "   fechados_suspended  = ${SUSP_J:-?}    (0 ⇒ o suspend não fechou nenhuma vez)"
echo "   abertos_janela      = ${ABERTOS_J:-?}  ← o contador que JULGA"
echo "   abertos_fora        = ${ABERTOS_FORA:-?}  (sessão ainda viva — não é defeito)"
echo "   abertos_total_hoje  = ${FINAL_TOTAL:-?}  (baseline era ${BASE:-?})"
echo

# ── VEREDICTO DE TRÊS RAMOS ─────────────────────────────────────────────────────
echo "── veredicto ──────────────────────────────────────────────────────────────"
case "${TOTAL_J:-x}" in
  ''|*[!0-9]*) die "total_janela não numérico ('$TOTAL_J')" ;;
  0)           die "a janela não pegou NENHUM segmento. T0=$T0 pode estar à frente do
        relógio dos dados, ou o consumidor não alcançou. Isto NÃO é 'zero abertos'." ;;
esac
case "${SUSP_J:-x}" in
  ''|*[!0-9]*) die "fechados_suspended não numérico ('$SUSP_J')" ;;
  0)           die "nenhum segmento fechou com outcome='suspended'. A METADE do ciclo
        que este gate existe para julgar não apareceu — 'abertos=0' aqui significa
        'não medi', não 'fechou'." ;;
  *)           ok "o suspend fechou segmento $SUSP_J vez(es) — a amostra existe" ;;
esac

if [ "${ABERTOS_J:-1}" -eq 0 ] 2>/dev/null; then
  ok "0 aberto(s) na janela, em $TOTAL_J segmento(s) de $N reprodução(ões)"
  if [ "${FINAL_TOTAL:-0}" -eq "${BASE:-0}" ] 2>/dev/null; then
    ok "o total de abertos não mexeu (${BASE} → ${FINAL_TOTAL}): a baseline histórica
       segue intacta e nada novo entrou nela"
  else
    bad "o total saiu de ${BASE} para ${FINAL_TOTAL} com abertos_janela=0 — inconsistência
       entre os dois leitores; investigar antes de aceitar o verde"
  fi
else
  bad "$ABERTOS_J segmento(s) ABERTO(S) em sessão fechada, criados DEPOIS de T0.
       A família B NÃO está coberta pelo conserto de 2026-08-18: ou o publish do
       'left' deste caminho não sai, ou sai sem chegar. Próximo comando, e ele
       acha a fronteira em que o dado ainda existe:
          bash infra/test/probe_participant_event_in_kafka.sh <session_id>
       'left' no tópico ⇒ consumidor/ClickHouse · 'left' ausente ⇒ produtor."
fi

echo
echo "   ✅ $PASS · ❌ $FAIL"
echo
# NB: sem crases nestas duas linhas. Dentro de aspas duplas o bash as trata como
# substituição de comando (a mesma pegadinha registrada em gate_external_resume.sh:103).
echo '   ⚠️  LIMITE DECLARADO: este gate cobre `primary` nativo de webhook. O papel'
echo '      `specialist` (delegate-as-conference) segue SEM medição pós-conserto.'
[ "$FAIL" -eq 0 ] && { echo "   ✅ FAMÍLIA B (primary) FECHA"; exit 0; }
echo "   ❌ REPROVOU"; exit 1
