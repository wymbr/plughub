#!/usr/bin/env bash
# probe_block2.sh — mede o que as três continuações do bloco 2 AFIRMAM.
#
# Mesma disciplina da `probe_hygiene.sh`: previsão impressa antes da medição, e
# três estados (verde / vermelho / INCONCLUSIVO), nunca dois.
#
# As três afirmações sob teste:
#   1. "a aprovação SEGUE produzindo segmentos órfãos" (17, contra 9 em 29/07)
#   2. "nenhum YAML passa `$.segment_id`, então hoje só a rede (C) atribui"
#   3. "wrap-up fatias 3 e 4 estavam bloqueadas pelo `segment_id` do Arc 12"
#
# Uso:  bash infra/test/probe_block2.sh
# Pré:  stack demo no ar.
# Saída: sempre 0 — mede, não julga.

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
DB=plughub_demo
CUT="2026-07-30"   # data do fix H1/H2

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }
hr()  { printf '\n%s\n' "══════════════════════════════════════════════════════════════════════"; }

if ! chq 'SELECT 1' >/dev/null 2>&1; then
  echo "⚠️  INCONCLUSIVO: clickhouse não respondeu. Nada abaixo é 'zero'."
  exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
hr
echo "BLOCO A — a aprovação ainda produz segmento humano órfão?"
echo
echo "PREVISÃO A: pelo menos parte dos órfãos de \`aprovacao_deploy\` tem data >= $CUT"
echo "  (o fix H1/H2). Se TODOS forem anteriores, a afirmação 'a aprovação SEGUE"
echo "  produzindo' está errada — seriam dívida histórica, e o item vira 'limpar',"
echo "  não 'consertar'. Os dois desfechos mudam o trabalho."
hr

echo "── A1. órfãos por pool × janela (o corte vem ANTES do total)"
chq "
  SELECT pool_id,
         multiIf(date < toDate('$CUT'), 'antes do fix', 'DEPOIS do fix') AS janela,
         count()          AS abertos,
         min(started_at)  AS primeiro,
         max(started_at)  AS ultimo
    FROM $DB.segments FINAL
   WHERE tenant_id='$TENANT' AND agent_type='human' AND ended_at IS NULL
   GROUP BY pool_id, janela
   ORDER BY janela DESC, abertos DESC
   FORMAT PrettyCompactMonoBlock"

echo
echo "── A2. os órfãos de aprovação, um a um, com o estado da SESSÃO"
echo "     (sessão ABERTA ⇒ claim abandonado — lacuna 2, condição conhecida;"
echo "      sessão FECHADA ⇒ vazamento de teardown — defeito de código)"
chq "
  SELECT g.session_id,
         g.started_at,
         if(s.closed_at IS NULL, 'sessão ABERTA', 'sessão FECHADA') AS estado_sessao,
         s.closed_at
    FROM (SELECT session_id, started_at, pool_id FROM $DB.segments FINAL
           WHERE tenant_id='$TENANT' AND agent_type='human' AND ended_at IS NULL
             AND pool_id LIKE 'aprovacao%') AS g
    LEFT JOIN (SELECT session_id, closed_at FROM $DB.sessions FINAL
                WHERE tenant_id='$TENANT') AS s USING (session_id)
   ORDER BY g.started_at
   FORMAT PrettyCompactMonoBlock"

echo
echo "── A3. contraprova: o wrap-up FECHA mesmo? (o fix que a aprovação não teve)"
echo "     Se estas linhas tiverem duration_ms e close_reason, o produtor existe e"
echo "     funciona — e a pergunta certa passa a ser por que ele não cobre a aprovação."
chq "
  SELECT pool_id, close_reason, count() AS n,
         round(avg(duration_ms)) AS dur_medio_ms,
         max(started_at)         AS ultimo
    FROM $DB.segments FINAL
   WHERE tenant_id='$TENANT' AND agent_type='human' AND ended_at IS NOT NULL
     AND date >= toDate('$CUT') AND pool_id LIKE '%-int'
   GROUP BY pool_id, close_reason
   ORDER BY n DESC
   FORMAT PrettyCompactMonoBlock"
echo "   ↑ vazio = nenhum wrap-up fechado no período; a contraprova não roda e"
echo "     o BLOCO A fica sem baseline (INCONCLUSIVO, não 'aprovação é igual')."

# ─────────────────────────────────────────────────────────────────────────────
hr
echo "BLOCO B — Arc 12: quem, de fato, escreve \`agent_business_events\`?"
echo
echo "PREVISÃO B: a tabela está VAZIA (ou só com resíduo de teste). Motivo: a busca"
echo "  estática não achou UM ÚNICO chamador de \`agent_event\` — nenhum YAML de skill,"
echo "  nenhum smoke, nenhum serviço. Se estiver assim, o item do TODO ('nenhum YAML"
echo "  passa \$.segment_id, então só a rede C atribui') está SUAVE demais: não é que o"
echo "  caminho A não esteja ligado — é que NÃO HÁ PRODUTOR, e portanto a rede C também"
echo "  não atribui nada. Linhas com segment_id preenchido REFUTAM a previsão."
hr

# Nomes de coluna CONFERIDOS no DDL (`analytics-api/clickhouse.py`), não supostos:
# a v1 usou `ts` e o ClickHouse recusou a query inteira. O timestamp é `emitted_at`,
# e `segment_id` é `Nullable(String)` — logo o teste de preenchimento é
# `isNotNull(...)`, NÃO `!= ''` (numa coluna Nullable, `!= ''` descarta o NULL e o
# vazio junto, e a contagem sairia plausível e errada).
echo "── B1. volume total e cobertura de segment_id"
chq "
  SELECT count()                          AS linhas,
         countIf(isNotNull(segment_id))   AS com_segment_id,
         countIf(isNull(segment_id))      AS sem_segment_id,
         min(emitted_at)                  AS primeiro,
         max(emitted_at)                  AS ultimo
    FROM $DB.agent_business_events
   WHERE tenant_id='$TENANT'
   FORMAT PrettyCompactMonoBlock" 2>&1

echo
echo "── B2. por categoria (quem seria o produtor, se houvesse)"
chq "
  SELECT category, count() AS n,
         countIf(isNotNull(segment_id)) AS com_seg,
         max(emitted_at) AS ultimo
    FROM $DB.agent_business_events
   WHERE tenant_id='$TENANT'
   GROUP BY category ORDER BY n DESC LIMIT 20
   FORMAT PrettyCompactMonoBlock" 2>&1
echo "   ↑ vazio aqui NÃO é 'a coluna funciona': é 'a coluna nunca foi exercitada'."

# ─────────────────────────────────────────────────────────────────────────────
hr
echo "BLOCO C — o que o wrap-up JÁ grava hoje (base das fatias 3 e 4)"
echo
echo "PREVISÃO C: os campos de wrap-up chegam em \`segments\` (via"
echo "  \`segment_outcome_record\`, que grava no segmento da ORIGEM por referência),"
echo "  e NÃO em \`agent_business_events\`. É isso que a fatia 3 muda de lugar."
hr

chq "
  SELECT name, type
    FROM system.columns
   WHERE database='$DB' AND table='segments' AND name LIKE '%wrapup%'
   FORMAT PrettyCompactMonoBlock" 2>&1

# As colunas são `wrapup_summary`/`wrapup_next_steps` (inglês — invariante de
# nomenclatura do projeto). A v1 usou `wrapup_resumo`, nome que só existe na PROSA
# do TODO; o ClickHouse recusou. Ler o nome do doc em vez do DDL é a mesma falha
# de "supor o caminho" que a probe_hygiene cometeu com o diretório de testes.
chq "
  SELECT countIf(isNotNull(wrapup_summary)     AND wrapup_summary     != '') AS com_summary,
         countIf(isNotNull(wrapup_next_steps)  AND wrapup_next_steps  != '') AS com_next_steps,
         max(started_at)                                                     AS ultimo
    FROM $DB.segments FINAL
   WHERE tenant_id='$TENANT'
   FORMAT PrettyCompactMonoBlock" 2>&1

hr
echo "FIM — compare cada bloco com a sua PREVISÃO antes de concluir."
exit 0
