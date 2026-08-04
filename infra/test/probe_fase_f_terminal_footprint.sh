#!/usr/bin/env bash
# PROBE (não é gate) — Fase F / D7: qual é a PEGADA do encerramento hoje?
#
# Mede, antes de propor qualquer mecanismo:
#   1. quantos itens saem por SUBMIT × por SUPERVISOR × por PRAZO;
#   2. se já existe DUPLO-TERMINAL gravado no demo;
#   3. em que estado terminal estão os itens parados na fila do `formfill_demo`
#      — inclusive se ainda EXISTE caminho terminal para eles.
#
# ── PREVISÃO ESCRITA ANTES DE RODAR (2026-08-04) ──────────────────────────────
#
#   M1  task_submitted        ≥ 9, todos agent_type='human'
#         (a Fase E mediu 9/9 na fila interna; é o único caminho exercitado)
#       acw_supervisor_closed = 0
#         (o `smoke_acw_expire.sh` cobre o encerramento, mas o ramo REIVINDICADO
#          — o único que fecha segmento humano — exige INSTANCE=human-<id> e é
#          anunciado como NÃO EXERCITADO sem ele; nada na série A–E o rodou assim)
#       acw_expired           = 0
#         (exige o `expires_at` do TOKEN vencer com o item reivindicado; os itens
#          do demo vivem 24 h e são recriados antes disso)
#
#   M2a segmentos com DUAS razões terminais distintas entre versões  = 0
#   M2b sessões com ≥2 segmentos terminais                           = 0
#         → se a previsão bater, a corrida da D7 NUNCA foi observada, e a Fase F
#           terá de CONSTRUIR a corrida para se validar. Ausência de caso não é
#           ausência de defeito: as duas janelas abaixo são de leitura de código.
#
#   M3  linhas `work_task_expire:` na janela de log ≥ 1, quase todas reason=task_done;
#       assinatura de no-op silencioso (was_queued=False was_claimed=False) ≥ 0
#         (o segundo a chegar aplica em silêncio HOJE — esta linha é a medida disso)
#
#   M5  4 itens na fila do formfill_demo; ledger presente nos 4; ≥2 com
#       work_item_deadline JÁ VENCIDO e ainda no ZSET.
#         → se o token de resume já não existir para um item ainda enfileirado,
#           o achado não é "terminal duas vezes", é **TERMINAL NENHUMA VEZ**:
#           submit devolve 404 (token), expire devolve 404 (ledger/token), e o
#           item fica imortal na fila até o TTL. Isso é a MESMA lacuna da D7 pelo
#           outro lado, e mudaria o desenho da Fase F.
#
# ── VEREDICTO ────────────────────────────────────────────────────────────────
# Cada bloco ramifica sobre o valor medido, em três estados. AUSENTE é sempre
# INCONCLUSIVO, nunca verde: "0 duplo-terminal" só significa alguma coisa se
# houver terminal nenhum dos dois lados para contar.
#
# Uso (raiz do repo, demo no ar):
#   bash infra/test/probe_fase_f_terminal_footprint.sh
#   SINCE_MIN=1440 POOL=formfill_demo bash infra/test/probe_fase_f_terminal_footprint.sh
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CH_DB="plughub_demo"
POOL="${POOL:-formfill_demo}"
SINCE_MIN="${SINCE_MIN:-1440}"
VOCAB="'task_submitted','acw_expired','acw_supervisor_closed'"

CH() { $COMPOSE exec -T clickhouse clickhouse-client -q "$1" < /dev/null 2>/dev/null | tr -d '\r'; }
R()  { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null | tr -d '\r'; }

echo "══ Fase F (D7) — pegada do encerramento · tenant=$TENANT pool=$POOL ══"
echo "   janela de LOG: ${SINCE_MIN} min (log é janela, não acumulado)"
echo

# ── PREFLIGHT — provar que cada leitor LÊ, contra valor conhecido ─────────────
# Duas leituras quebradas são iguais entre si: sem isto, um erro de query vira
# "0 duplo-terminal" e passa por medição.
[ "$(CH "SELECT 41 + 1")" = "42" ] || {
  echo "   ⛔ INSTRUMENTO QUEBRADO — clickhouse-client não respondeu 42 a 41+1"; exit 3; }
_SEGS_TOTAL=$(CH "SELECT count() FROM ${CH_DB}.segments")
case "$_SEGS_TOTAL" in
  ''|*[!0-9]*) echo "   ⛔ INSTRUMENTO QUEBRADO — count() em segments não é inteiro: '$_SEGS_TOTAL'"; exit 3 ;;
esac
[ "$_SEGS_TOTAL" -gt 0 ] || {
  echo "   ⛔ INCONCLUSIVO — a tabela segments está VAZIA. Nada a medir."; exit 3; }
[ "$(R PING)" = "PONG" ] || {
  echo "   ⛔ INSTRUMENTO QUEBRADO — redis não respondeu PONG"; exit 3; }
# o parser de log tem de extrair os campos de uma linha SINTÉTICA conhecida
_FAKE="work_task_expire: session=sess_X pool=p reason=task_done was_queued=True was_claimed=False via=- instance=- remaining=-1"
[ "$(printf '%s' "$_FAKE" | grep -oE 'reason=[a-z_]+' | sed 's/reason=//')" = "task_done" ] || {
  echo "   ⛔ INSTRUMENTO QUEBRADO — parser não extrai reason da linha sintética"; exit 3; }
echo "   preflight ok — CH responde ($_SEGS_TOTAL segmentos), redis responde, parser extrai"
echo

# ── M1 — por onde os itens SAEM hoje ─────────────────────────────────────────
echo "── M1 · vocabulário terminal em segments (FINAL, todo o histórico) ──"
M1=$(CH "SELECT close_reason, agent_type, count()
         FROM ${CH_DB}.segments FINAL
         WHERE close_reason IN (${VOCAB})
         GROUP BY close_reason, agent_type
         ORDER BY close_reason, agent_type")
if [ -z "$M1" ]; then
  echo "   ⚠️  NENHUM segmento com razão terminal de item de trabalho."
  echo "      INCONCLUSIVO para 'quantos saem por onde' — e um achado por si:"
  echo "      significa que nenhum item de trabalho reivindicado fechou segmento"
  echo "      neste banco. Conferir se a fila interna já foi exercitada."
else
  printf '%s\n' "$M1" | while IFS=$'\t' read -r cr at n; do
    printf '   %-24s agent_type=%-8s %s\n' "$cr" "${at:-∅}" "$n"
  done
fi
N_SUB=$(CH "SELECT count() FROM ${CH_DB}.segments FINAL WHERE close_reason='task_submitted'")
N_SUP=$(CH "SELECT count() FROM ${CH_DB}.segments FINAL WHERE close_reason='acw_supervisor_closed'")
N_EXP=$(CH "SELECT count() FROM ${CH_DB}.segments FINAL WHERE close_reason='acw_expired'")
echo
echo "   submit=${N_SUB:-?}  supervisor=${N_SUP:-?}  prazo=${N_EXP:-?}"
if [ "${N_SUP:-0}" = "0" ] && [ "${N_EXP:-0}" = "0" ]; then
  echo "   → PREVISÃO BATE: só o SUBMIT já fechou segmento. Os outros dois braços"
  echo "     da corrida da D7 nunca rodaram aqui — a Fase F não vai ACHAR a"
  echo "     corrida no dado, vai ter de CONSTRUÍ-LA."
else
  echo "   → PREVISÃO NÃO BATE: existe encerramento por supervisor e/ou prazo no"
  echo "     histórico. Olhar as sessões desses segmentos ANTES de projetar:"
  echo "     elas são a amostra real do caminho que a F precisa tornar único."
fi
echo

# ── M2a — duplo-terminal no MESMO segmento (assinatura da sobrescrita) ───────
# SEM FINAL de propósito: o ReplacingMergeTree colapsa por (tenant, session,
# segment) e o ÚLTIMO a escrever vence. Se dois terminais correram, as duas
# versões estão gravadas e só a última aparece com FINAL — a evidência da
# corrida só existe nas versões.
echo "── M2a · mesmo segmento com DUAS razões terminais distintas (sem FINAL) ──"
M2A=$(CH "SELECT session_id, segment_id,
                 arrayStringConcat(arraySort(groupUniqArray(close_reason)),' | ') AS reasons,
                 count() AS versions
          FROM ${CH_DB}.segments
          WHERE close_reason IN (${VOCAB})
          GROUP BY session_id, segment_id
          HAVING length(groupUniqArray(close_reason)) > 1
          ORDER BY session_id")
if [ -z "$M2A" ]; then
  echo "   0 segmentos com razão terminal ambígua."
  echo "   → NÃO é 'a corrida não existe': é 'a corrida não foi gravada aqui'."
  echo "     Com supervisor=${N_SUP:-0} e prazo=${N_EXP:-0}, não havia como gravar."
else
  printf '%s\n' "$M2A" | while IFS=$'\t' read -r sid seg reasons v; do
    echo "   ⚠️  DUPLO-TERMINAL  session=$sid seg=$seg  [$reasons]  versões=$v"
  done
  echo "   → ACHADO DURO: dois terminais escreveram o MESMO segmento. Com FINAL,"
  echo "     só o último aparece — a sobrescrita é silenciosa. Estas sessões são"
  echo "     a amostra que a Fase F tem de reproduzir."
fi
echo

# ── M2b — duplo-terminal na mesma SESSÃO (dois itens ou dois fechamentos) ────
echo "── M2b · sessões com ≥2 segmentos terminais (FINAL) ──"
M2B=$(CH "SELECT session_id,
                 arrayStringConcat(arraySort(groupUniqArray(close_reason)),' | ') AS reasons,
                 count() AS segs
          FROM ${CH_DB}.segments FINAL
          WHERE close_reason IN (${VOCAB})
          GROUP BY session_id
          HAVING count() > 1
          ORDER BY session_id")
if [ -z "$M2B" ]; then
  echo "   0 sessões com mais de um segmento terminal."
else
  printf '%s\n' "$M2B" | while IFS=$'\t' read -r sid reasons n; do
    echo "   session=$sid  segs=$n  [$reasons]"
  done
  echo "   → Nem toda linha aqui é defeito (re-delegate no on_timeout cria item"
  echo "     NOVO, legitimamente). Separar pelo step_id antes de concluir."
fi
echo

# ── M3 — o segundo a chegar aplica em SILÊNCIO? (log do routing) ────────────
echo "── M3 · work_task_expire no log do routing-engine (janela ${SINCE_MIN} min) ──"
RLOG=$($COMPOSE logs --since "${SINCE_MIN}m" --no-color routing-engine 2>/dev/null)
WTE=$(printf '%s\n' "$RLOG" | grep -F "work_task_expire: session=" || true)
N_WTE=$(printf '%s\n' "$WTE" | grep -c . || true)
if [ "${N_WTE:-0}" -eq 0 ]; then
  echo "   ⚠️  INCONCLUSIVO — nenhuma chamada de work_task_expire na janela."
  echo "      Não é '0 encerramentos': é ausência de amostra no LOG (que é janela,"
  echo "      não acumulado). Aumentar SINCE_MIN ou exercitar um item."
else
  echo "   $N_WTE chamada(s). Por reason:"
  printf '%s\n' "$WTE" | grep -oE 'reason=[a-z_]+' | sed 's/reason=//' \
    | sort | uniq -c | sed 's/^/     /'
  # A assinatura do no-op: nada estava na fila, nada estava reivindicado. É o
  # que acontece HOJE quando um segundo terminal chega — o método é idempotente
  # por construção e NÃO recusa; devolve 200 tendo feito nada.
  NOOP=$(printf '%s\n' "$WTE" | grep -cF "was_queued=False was_claimed=False" || true)
  echo "   no-op silencioso (was_queued=False was_claimed=False): ${NOOP:-0}"
  if [ "${NOOP:-0}" -gt 0 ]; then
    echo "   → Cada uma dessas é um encerramento que NÃO recusou e NÃO fez nada."
    echo "     Hoje isso é indistinguível de 'encerrou com sucesso' para quem chama."
    printf '%s\n' "$WTE" | grep -F "was_queued=False was_claimed=False" | tail -5 | sed 's/^/     /'
  fi
fi
echo

# ── M4 — dois resumes para a mesma sessão+step (candidato a duplo-terminal) ──
echo "── M4 · resumes repetidos no log do channel-gateway (janela ${SINCE_MIN} min) ──"
GLOG=$($COMPOSE logs --since "${SINCE_MIN}m" --no-color channel-gateway 2>/dev/null)
RES=$(printf '%s\n' "$GLOG" | grep -oE 'webhook resume: session=[A-Za-z0-9_.:-]+ step=[A-Za-z0-9_.-]+' || true)
N_RES=$(printf '%s\n' "$RES" | grep -c . || true)
if [ "${N_RES:-0}" -eq 0 ]; then
  echo "   ⚠️  INCONCLUSIVO — nenhum resume na janela."
else
  echo "   $N_RES resume(s) concluído(s). Repetidos por (session, step):"
  DUP=$(printf '%s\n' "$RES" | sort | uniq -c | awk '$1 > 1' || true)
  if [ -z "$DUP" ]; then
    echo "     nenhum — cada (sessão, step) resumiu uma vez só na janela."
  else
    printf '%s\n' "$DUP" | sed 's/^/     ⚠️  /'
    echo "   → CANDIDATO, não prova: o mesmo step pode resumir de novo por"
    echo "     re-delegate legítimo. Cruzar com M2a antes de chamar de corrida."
  fi
  # O scanner de prazo é o terceiro gatilho, e roda no MESMO processo do
  # endpoint HTTP — a corrida da D7 não precisa de duas réplicas.
  N_SCAN=$(printf '%s\n' "$GLOG" | grep -cF "timeout scanner: expiring token" || true)
  N_404=$(printf '%s\n' "$GLOG" | grep -cF "unknown or expired token" || true)
  echo "   scanner de prazo disparou: ${N_SCAN:-0}   ·   token desconhecido/vencido (404): ${N_404:-0}"
fi
echo

# ── M5 — os itens parados: ainda EXISTE caminho terminal para eles? ─────────
echo "── M5 · itens na fila de $POOL — estado terminal de cada um ──"
MEMBERS=$(R ZRANGE "${TENANT}:pool:${POOL}:queue" 0 -1)
if [ -z "$MEMBERS" ]; then
  echo "   ⚠️  INCONCLUSIVO — fila VAZIA. Crie um item real:"
  echo "        bash infra/test/smoke_formfill_renderer.sh"
else
  NOW=$(date -u +%s)
  N_ITEMS=0; N_SEM_SAIDA=0; N_VENCIDO=0
  while IFS= read -r SID; do
    [ -n "$SID" ] || continue
    N_ITEMS=$((N_ITEMS+1))
    JSON=$(R GET "${TENANT}:queue_contact:${SID}")
    DL=$(printf '%s' "$JSON" | grep -oE '"work_item_deadline":[[:space:]]*"[^"]*"' \
         | sed 's/.*"work_item_deadline":[[:space:]]*"//;s/"$//')
    ASG=$(printf '%s' "$JSON" | grep -oE '"assigned_to":[[:space:]]*"[^"]*"' \
         | sed 's/.*"assigned_to":[[:space:]]*"//;s/"$//')
    LEDGER=$(R GET "${TENANT}:work_task:${SID}")
    TOK=$(printf '%s' "$LEDGER" | grep -oE '"resume_token":[[:space:]]*"[^"]*"' \
         | sed 's/.*"resume_token":[[:space:]]*"//;s/"$//')
    TOKVAL=""
    [ -n "$TOK" ] && TOKVAL=$(R HGET "${TENANT}:resume_tokens" "$TOK")
    CLAIM=$(R GET "${TENANT}:pool:${POOL}:claim_record:${SID}")

    # prazo do ITEM × prazo do TOKEN: são dois relógios, e só o do TOKEN move o
    # scanner. Se divergirem, "sai por prazo" não quer dizer uma coisa só.
    TOKEXP=""
    [ -n "$TOKVAL" ] && TOKEXP=$(printf '%s' "$TOKVAL" | cut -d: -f3-)
    DLS=0; [ -n "$DL" ] && DLS=$(date -u -d "$DL" +%s 2>/dev/null || echo 0)
    VENC="—"
    if [ "$DLS" -gt 0 ]; then
      if [ "$DLS" -lt "$NOW" ]; then VENC="VENCIDO há $(( (NOW-DLS)/60 ))min"; N_VENCIDO=$((N_VENCIDO+1))
      else VENC="vence em $(( (DLS-NOW)/60 ))min"; fi
    fi

    echo "   • $SID"
    echo "       work_item_deadline=${DL:-∅}  ($VENC)"
    echo "       assigned_to=${ASG:-∅}   claim_record=$([ -n "$CLAIM" ] && echo PRESENTE || echo ausente)"
    echo "       ledger work_task=$([ -n "$LEDGER" ] && echo PRESENTE || echo AUSENTE)  resume_token=${TOK:-∅}"
    echo "       token vivo no hash=$([ -n "$TOKVAL" ] && echo SIM || echo NÃO)  token_expires_at=${TOKEXP:-∅}"

    # Três ramos sobre a EXISTÊNCIA de caminho terminal — o que a D7 pressupõe.
    if [ -z "$LEDGER" ]; then
      echo "       → SEM SAÍDA: ledger ausente ⇒ o expire do supervisor devolve 404"
      echo "         no_work_task. O item segue na fila e ninguém pode encerrá-lo."
      N_SEM_SAIDA=$((N_SEM_SAIDA+1))
    elif [ -z "$TOKVAL" ]; then
      echo "       → SEM SAÍDA: ledger presente mas o TOKEN já foi consumido ⇒ o"
      echo "         expire chega ao gateway e leva 404 'token not found'. Nem"
      echo "         submit nem supervisor nem prazo tiram este item de cena."
      N_SEM_SAIDA=$((N_SEM_SAIDA+1))
    else
      echo "       → com saída: submit e expire ainda alcançam este item."
    fi
  done <<< "$MEMBERS"

  echo
  echo "   itens=$N_ITEMS  com prazo VENCIDO=$N_VENCIDO  SEM caminho terminal=$N_SEM_SAIDA"
  if [ "$N_SEM_SAIDA" -gt 0 ]; then
    echo "   → ACHADO que muda a Fase F: a D7 diz que o expire é 'a única forma de"
    echo "     tirar o item de cena antes do prazo'. Para $N_SEM_SAIDA item(ns) não há"
    echo "     forma NENHUMA. Terminal-uma-vez pressupõe terminal-ao-menos-uma-vez;"
    echo "     projetar só a recusa do segundo deixaria estes itens imortais."
  else
    echo "   → todos os itens ainda têm caminho terminal. A F pode se ocupar só da"
    echo "     unicidade, sem abrir a frente do item órfão."
  fi
fi
echo
echo "══ fim da medição — nada foi alterado ══"
