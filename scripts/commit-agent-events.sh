#!/usr/bin/env bash
# Commit da descontinuação de `agent_events` (fatia 1) — exatamente os 13 arquivos
# listados no `git status --short`.
set -euo pipefail
cd ~/projects/plughub

git add \
  CHANGELOG.md \
  TODO.md \
  docs/arcos/arc12-agent-business-events.md \
  infra/metabase/clickhouse_users.sql \
  infra/metabase/setup.py \
  packages/analytics-api/src/plughub_analytics_api/clickhouse.py \
  packages/analytics-api/src/plughub_analytics_api/consumer.py \
  packages/analytics-api/src/plughub_analytics_api/models.py \
  packages/analytics-api/src/plughub_analytics_api/query.py \
  packages/analytics-api/src/plughub_analytics_api/reports.py \
  packages/analytics-api/src/plughub_analytics_api/reports_query.py \
  packages/analytics-api/src/plughub_analytics_api/tests/test_consumer.py \
  packages/analytics-api/src/plughub_analytics_api/tests/test_reports.py

git commit -F- <<'MSG'
refactor(analytics): descontinua agent_events — fatia 1 (para de escrever)

Começou como "corrigir o agent_done de crash-recovery descartado pelo analytics"
e a investigação desmontou a premissa duas vezes: (a) não eram 2 caminhos e sim
9 — TODO agent_done do bridge chaveia por conversation_id, então 100% era
descartado, não só o de recuperação; (b) nenhuma métrica de produto lê
agent_events — TMA/resolução vêm de sessions/segments — logo o dilema sobre TMA
que travava a decisão não existia.

Critério que fechou: no eixo de MARCAÇÃO há porta única (tool `agent_event` do
Arc 12 → agent_business_events); o resto é substrato derivado (sessions/segments/
messages) ou duplicata. `agent_events` não era nenhum dos dois — derivada (metade
é decisão do routing-engine, nenhum agente a emite) E duplicando `segments`, que
guarda o mesmo par routed/agent_done como UMA linha fechada com role, channel,
close_reason, sequence_index, conference_id, flow_id a mais.

- parsers, dispatch e cadeia insert_agent_event/_AGENT_COLS/_agent_row removidos
- /reports/events migrado para `segments` em duas branches; ganha channel sem
  JOIN, close_reason no conteúdo, e author_role recebe o papel REAL em vez de
  agent_type_id
- /reports/agents (bare) removido — zero chamadores; substitutos sobre segments
- /dashboard/metrics: chave preservada, fonte agora segments (by_outcome era
  100% "unknown" no vivo)
- card Metabase sobre segments.duration_ms com `> 0` e agent_type != 'system'
- GRANT + ROW POLICY de `segments` para os usuários Metabase (o card novo daria
  ACCESS_DENIED; e GRANT sem policy vazaria cross-tenant, pois os cards não
  filtram tenant_id no SQL)
- origin='live' uniformizado nas branches de substrato do UNION — filtrar só em
  algumas produzia stream incoerente (sessão importada aberta e com mensagens,
  sem nenhum agente)
- TestEventsSqlBranches: _events_sql_branches não tinha NENHUM teste e era a
  função de maior risco (branch com largura diferente derruba o UNION inteiro)

⚠️ O EVENTO agent_done NÃO foi removido — o routing-engine depende dele em
agent.lifecycle para liberar capacidade. Saiu só a gravação analítica redundante.

Fatia 2 (DROP + grants) no TODO.md, adiada: a auditoria cobre o código, não
consulta ad-hoc.
MSG

git --no-pager log --oneline -1
git status --short
