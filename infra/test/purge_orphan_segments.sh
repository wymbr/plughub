#!/usr/bin/env bash
# purge_orphan_segments.sh — expurga segmentos IRRECUPERÁVEIS anteriores ao conserto.
#
# DECISÃO (2026-08-18, handoff `docs/product/handoff-segmentos-abertos-2026-08-18.md` §Pendência 1):
# os segmentos que nunca fecharam por causa do publish sem chave em
# `conversations.participants` **não serão reparados** — serão expurgados, com data de corte.
#
# POR QUE EXPURGAR E NÃO REPARAR. Três opções, e duas foram recusadas:
#
#   reparar por replay do tópico  → re-emite eventos que o consumer trata como `live`. O
#                                   discriminador `origin` existe justamente para essa fronteira
#                                   (`adr-quality-substrate-isolation.md`). Risco caro por 9 linhas.
#   fechar sinteticamente         → PIOR das três: inventar `ended_at` transforma "não sei quando
#                                   terminou" num número plausível. É o modo de falha que a
#                                   § Postura de Engenharia manda caçar, cometido de propósito.
#   EXPURGAR                      → a linha é sabidamente quebrada e irrecuperável; removê-la é
#                                   honesto, e faz a baseline do gate cair a ZERO.
#
# O GANHO É DO INSTRUMENTO, não da tabela. Com baseline 9, o
# `probe_open_segments_closed_sessions.sh` lia 9 para sempre e **nunca poderia ficar vermelho** —
# um gate que não pode reprovar compra confiança sem dar nada. Com baseline 0, qualquer aberto novo
# é vermelho, sem aritmética e sem constante para alguém esquecer de atualizar.
#
# DELETA POR LISTA EXPLÍCITA DE `segment_id`, nunca por predicado. O dry-run imprime exatamente os
# ids; o apply deleta exatamente aqueles. Predicado numa mutação pode alcançar linha que ninguém
# mediu — e mutação não tem desfazer.
#
# NÃO TOCA `participation_intervals`, de propósito. Ela é
# `ORDER BY (tenant, session, participant)`, então dois segmentos do mesmo participante na mesma
# sessão colidem numa linha só (caso do resume). Ela **não** é testemunha por-segmento, logo não há
# como casar com segurança qual linha corresponde a qual órfão. Apagar por aproximação ali seria
# trocar um dado quebrado conhecido por um dado errado desconhecido.
#
# Uso (raiz do repo):
#   bash infra/test/purge_orphan_segments.sh                 # DRY-RUN (default), não apaga nada
#   bash infra/test/purge_orphan_segments.sh --apply         # executa
#   CUTOFF=2026-08-15 bash infra/test/purge_orphan_segments.sh --apply
#
# Sai: 0 = ok (dry-run ou apply bem-sucedido) · 1 = falhou · 2 = INCONCLUSIVO

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
DB="${DB:-plughub_demo}"
# Corte: o conserto entrou em 2026-08-18 e todo órfão medido é anterior a 2026-08-15. O corte
# protege contra apagar um defeito NOVO — que é achado, não lixo, e tem de ficar visível.
CUTOFF="${CUTOFF:-2026-08-15}"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

echo "══ expurgo de segmentos órfãos irrecuperáveis ══"
echo "   tenant=$TENANT · corte=< $CUTOFF · modo=$([ $APPLY -eq 1 ] && echo APPLY || echo DRY-RUN)"
echo

PING=$(chq 'SELECT 1' | tr -d '\r')
[ "$PING" = "1" ] || { echo "   ⛔ INCONCLUSIVO: clickhouse não respondeu ('$PING')"; exit 2; }

# ── O ALVO, nomeado antes de qualquer escrita ────────────────────────────────
ALVO="
  SELECT g.segment_id
    FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT'
     AND g.ended_at IS NULL
     AND g.date < toDate('$CUTOFF')"

echo "── 1. o que será apagado (nomeado, não contado) ──────────────────────────"
chq "
  SELECT g.segment_id AS segmento, g.session_id AS sessao, g.role AS papel,
         g.pool_id AS pool, g.agent_type AS tipo, g.started_at AS abriu
    FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL
     AND g.date < toDate('$CUTOFF')
   ORDER BY g.started_at
   FORMAT PrettyCompactMonoBlock"
echo

N_ALVO=$(chq "SELECT count() FROM ($ALVO)" | tr -d '\r')
# ⚠️ CONTADOR-TESTEMUNHA ao lado do contador de alvo: quantos abertos existem DEPOIS do corte.
# Sem ele, "0 alvos" não distingue "já expurgado" de "o corte está errado e não pega nada".
N_DEPOIS=$(chq "
  SELECT count()
    FROM $DB.segments AS g FINAL
   INNER JOIN (SELECT session_id FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT' AND closed_at IS NOT NULL) AS s
      ON s.session_id = g.session_id
   WHERE g.tenant_id='$TENANT' AND g.ended_at IS NULL
     AND g.date >= toDate('$CUTOFF')" | tr -d '\r')

echo "   alvos (antes do corte) = ${N_ALVO:-?}"
echo "   abertos DEPOIS do corte = ${N_DEPOIS:-?}  ← testemunha: estes NÃO são tocados"
echo

case "${N_ALVO:-x}" in
  ''|*[!0-9]*) echo "   ⛔ INCONCLUSIVO: contagem não numérica ('$N_ALVO')"; exit 2 ;;
  0) echo "   nada a expurgar antes de $CUTOFF."
     [ "${N_DEPOIS:-0}" -gt 0 ] 2>/dev/null && {
       echo "   ⚠️  mas há $N_DEPOIS aberto(s) DEPOIS do corte — isso é DEFEITO VIVO, não lixo."
       echo "      Investigar, não expurgar: bash infra/test/probe_participant_event_in_kafka.sh <sid>"; }
     exit 0 ;;
esac

if [ "${N_DEPOIS:-0}" -gt 0 ] 2>/dev/null; then
  echo "   ⛔ ABORTA: existem $N_DEPOIS segmento(s) aberto(s) DEPOIS do corte."
  echo "      Expurgar com defeito vivo em curso apagaria a evidência junto com o lixo."
  echo "      Diagnosticar primeiro; o expurgo do passado espera."
  exit 2
fi

if [ "$APPLY" -eq 0 ]; then
  echo "── DRY-RUN — nada foi apagado ────────────────────────────────────────────"
  echo "   Para executar:  bash infra/test/purge_orphan_segments.sh --apply"
  exit 0
fi

# ── 2. APPLY — lista explícita, montada a partir do que foi impresso acima ───
echo "── 2. apagando por lista explícita de segment_id ─────────────────────────"
# ⚠️ A LISTA É MONTADA NO BASH, NÃO NO SQL. A v1 fazia
#   `arrayStringConcat(groupArray(concat('''', segment_id, '''')), ',')`
# e o clickhouse-client devolveu as aspas ESCAPADAS (`\'id\'`), que voltaram para
# dentro do ALTER e mataram a mutação com SYNTAX_ERROR na posição 91. Duas lições:
# (1) quoting que atravessa shell→SQL→saída→SQL de novo tem três chances de errar,
# e uma só de acertar; (2) o erro só apareceu porque a saída da submissão é IMPRESSA
# e a seção 3 CONFERE o resultado — um script que apenas submetesse teria dito
# "mutação submetida" e seguido em frente com 9 linhas intactas.
IDS=$(chq "SELECT segment_id FROM ($ALVO)" | tr -d '\r' | sed "s/^/'/;s/\$/'/" | paste -sd,)
[ -n "$IDS" ] || { echo "   ⛔ INCONCLUSIVO: não montei a lista de ids"; exit 2; }
# A lista tem de PARECER uma lista SQL antes de virar uma. Barra invertida aqui é o
# sintoma exato da v1.
case "$IDS" in
  *\\*) echo "   ⛔ INCONCLUSIVO: a lista de ids veio com barra invertida — quoting quebrado:"
        echo "      $(printf '%s' "$IDS" | head -c 200)"; exit 2 ;;
  "'"*) : ;;
  *)    echo "   ⛔ INCONCLUSIVO: a lista de ids não começa com aspa simples:"
        echo "      $(printf '%s' "$IDS" | head -c 200)"; exit 2 ;;
esac
echo "   ids: $(printf '%s' "$IDS" | head -c 300)..."

MUT=$(chq "ALTER TABLE $DB.segments DELETE WHERE tenant_id='$TENANT' AND segment_id IN ($IDS)")
echo "   mutação submetida: ${MUT:-<sem saída>}"
# Erro de submissão NÃO pode virar "espera a mutação": não há mutação para esperar, e
# o laço de espera terminaria com 0 pendentes — um zero que parece sucesso.
case "$MUT" in
  *Exception*|*"Code:"*|*SYNTAX_ERROR*)
    echo "   ❌ a mutação NÃO foi aceita pelo ClickHouse. Nada foi apagado."
    exit 1 ;;
esac

# Mutação é ASSÍNCRONA. Ler a tabela logo depois devolveria o valor PRÉ-mudança, que pareceria
# "não aplicou" — esperar o `is_done` é a diferença entre medir e adivinhar.
echo "   aguardando a mutação concluir ..."
for _ in $(seq 1 30); do
  PEND=$(chq "SELECT count() FROM system.mutations
               WHERE database='$DB' AND table='segments' AND is_done=0" | tr -d '\r')
  [ "${PEND:-1}" -eq 0 ] 2>/dev/null && break
  sleep 2
done
echo "   mutações pendentes: ${PEND:-?}"

FALHA=$(chq "SELECT latest_fail_reason FROM system.mutations
              WHERE database='$DB' AND table='segments' AND latest_fail_reason != ''
              ORDER BY create_time DESC LIMIT 1" | tr -d '\r')
[ -n "$FALHA" ] && echo "   ⚠️  última falha de mutação registrada: $FALHA"

# ── 3. VERIFICAÇÃO — o alvo sumiu, e o resto continua lá ────────────────────
RESTA=$(chq "SELECT count() FROM ($ALVO)" | tr -d '\r')
TOTAL=$(chq "SELECT count() FROM $DB.segments FINAL WHERE tenant_id='$TENANT'" | tr -d '\r')
echo
echo "── 3. verificação ────────────────────────────────────────────────────────"
echo "   alvos restantes = ${RESTA:-?}   ·   total de segmentos = ${TOTAL:-?}"

if [ "${RESTA:-1}" -eq 0 ] 2>/dev/null && [ "${TOTAL:-0}" -gt 0 ] 2>/dev/null; then
  echo "   ✅ expurgo concluído. A baseline do gate agora é ZERO — a partir daqui,"
  echo "      QUALQUER aberto em sessão fechada é defeito vivo."
  echo "      Conferir: bash infra/test/probe_open_segments_closed_sessions.sh"
  exit 0
fi
if [ "${TOTAL:-0}" -eq 0 ] 2>/dev/null; then
  echo "   ❌ a tabela ficou VAZIA — isto não é sucesso, é dano. Investigar imediatamente."
  exit 1
fi
echo "   ❌ ainda restam ${RESTA:-?} alvo(s): a mutação não completou ou não alcançou as linhas."
exit 1
