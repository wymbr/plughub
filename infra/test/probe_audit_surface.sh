#!/usr/bin/env bash
# probe_audit_surface.sh — o que a auditoria LGPD tem de fato.
#
# A varredura de código de 2026-08-22 mostrou que o problema NÃO é o que o TODO
# dizia. O circuito não está morto: `invoke` publica em `mcp.audit`, o consumer
# da analytics-api consome e `parse_mcp_audit_event` grava — só que em
# `session_timeline`, e é de lá que `/v1/audit/mcp-calls` lê. Ou seja, a chamada
# MCP É registrada e É legível. O que não existe:
#
#   (1) `mcp_audit_log`      — DDL ausente de `_ALL_DDL` (clickhouse.py:1051)
#   (2) `audit_access_log`   — DDL ausente E nenhum INSERT em audit.py,
#                              embora audit.py:8 afirme que grava e a UI mostre
#                              banner dizendo que todo acesso fica registrado
#   (3) `_require_audit_access()` — não existe; roda `optional_pool_principal`
#
# ── ATUALIZAÇÃO 2026-08-28 (o bloco acima é o diagnóstico de 08-22, preservado) ──
# (2) e (3) estão FECHADOS. O gate existe (`_check_audit_access`, cinco ramos, no
# verificador canônico `plughub_authz`), `audit_access_log` existe e é escrita — e o
# passo 4 da migração tirou o `optional_pool_principal` das duas rotas: sendo `Depends`,
# o `401` dele era levantado ANTES do corpo do handler, então a recusa SEM CREDENCIAL
# nunca era gravada. Era exatamente o que este P4 media, e é por isso que ele saiu do
# vermelho sem que o critério mudasse. (1) segue aberto de propósito: `mcp_audit_log`
# não tem produtor neste ambiente, e criar tabela que ninguém preenche é o
# "existe ≠ está pronto" de novo.
#
# Este probe mede os três, e mede também o que pode ME REFUTAR: se
# `analytics_open_access` estiver ligado, a porta aberta é DECISÃO de config, não
# ausência de portão, e o veredicto do P3 muda de nome.
#
# ── PREVISÕES (antes de rodar) ───────────────────────────────────────────────
#   P1  tabelas de audit presentes: 0 · tabela-testemunha (session_timeline): 1
#   P2  linhas `mcp.tool_call` em session_timeline: > 0 se `invoke` já rodou aqui.
#       Se 0, o defeito da tabela é DORMENTE (mesma família do sentimento sem
#       produtor) e a prioridade cai — mas o P3 continua valendo, porque a porta
#       está aberta independentemente de haver dado atrás dela.
#   P3  GET /v1/audit/mcp-calls SEM Authorization → 200 (defeito, se open_access=false)
#       CONTROLE: o mesmo serviço tem de saber RECUSAR alguma coisa; sem esse
#       contraste, "200 sem header" também é compatível com "auth desligada".
#
# Veredicto de TRÊS estados: 0 = nada a apontar · 1 = achado · 3 = INCONCLUSIVO
set -uo pipefail

COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
AN="${AN:-http://localhost:3500}"
CFG="${CFG:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"
DB="${DB:-plughub_demo}"

CH() { $COMPOSE exec -T clickhouse clickhouse-client -q "$1" < /dev/null 2>/dev/null | tr -d '\r'; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }
body() { curl -s --max-time 15 "$@"; }

RC=0
echo "══ superfície de auditoria LGPD ══"
echo "   analytics=$AN  db=$DB  tenant=$TENANT"

# ── Preflight ────────────────────────────────────────────────────────────────
# `/v1/health` — o `/health` sem prefixo dá 404 nesta API, e medir o 404 seria
# concluir "serviço fora" com o serviço de pé.
[ "$(code "$AN/v1/health")" = "200" ] || {
  echo "   ⛔ INCONCLUSIVO — analytics-api fora do ar em $AN/v1/health"; exit 3; }
[ "$(CH 'SELECT 1')" = "1" ] || {
  echo "   ⛔ INCONCLUSIVO — clickhouse inalcançável pelo compose"; exit 3; }

# ── P1 · as tabelas existem? ─────────────────────────────────────────────────
# Contador de AUSÊNCIA sempre com TESTEMUNHA de presença: se a query estiver
# errada (database errado, coluna errada), os dois dão 0 e a ausência some no ruído.
echo
echo "── P1 · tabelas de auditoria no ClickHouse ───────────────────────────────"
T_AUDIT="$(CH "SELECT count() FROM system.tables WHERE name IN ('mcp_audit_log','audit_access_log')")"
T_TESTE="$(CH "SELECT count() FROM system.tables WHERE name = 'session_timeline'")"
echo "      mcp_audit_log + audit_access_log : ${T_AUDIT:-?}"
echo "      session_timeline (testemunha)    : ${T_TESTE:-?}"
if [ "${T_TESTE:-0}" != "1" ]; then
  echo "   ⛔ INCONCLUSIVO — a testemunha não apareceu; a query é que está errada,"
  echo "      não o sistema. (database '$DB'? nome da tabela?)"
  exit 3
fi
if [ "${T_AUDIT:-0}" = "0" ]; then
  echo "   ❌ nenhuma das duas existe — e não há DROP registrado em clickhouse.py,"
  echo "      ao contrário de outras remoções deliberadas do mesmo arquivo."
  RC=1
else
  echo "   ✅ presentes ($T_AUDIT/2)"
fi

# ── P2 · há dado de auditoria em algum lugar? ────────────────────────────────
# A lição de 08-20: procure a TESTEMUNHA antes de consertar. Um store ausente
# para um fluxo que nunca roda é dívida, não incidente.
echo
echo "── P2 · tráfego de chamada MCP registrado ────────────────────────────────"
N_TOOL="$(CH "SELECT count() FROM $DB.session_timeline WHERE event_type = 'mcp.tool_call'")"
N_TOTAL="$(CH "SELECT count() FROM $DB.session_timeline")"
echo "      linhas mcp.tool_call : ${N_TOOL:-?}"
echo "      linhas no timeline   : ${N_TOTAL:-?}   (denominador)"
if [ "${N_TOTAL:-0}" = "0" ]; then
  echo "   ⛔ timeline VAZIO — P2 não julga: 'sem auditoria' e 'sem uso' são a mesma tela."
elif [ "${N_TOOL:-0}" = "0" ]; then
  echo "   ⚠️  ZERO chamadas MCP auditadas em ${N_TOTAL} linhas de timeline."
  echo "      O store ausente é DORMENTE: a borda `invoke` não foi exercitada aqui."
  echo "      Isso REBAIXA (1) e (2); NÃO rebaixa o P3, que é porta aberta."
else
  echo "   ✅ há $N_TOOL chamadas MCP registradas — o caminho tem tráfego real"
fi

# ── P3 · a porta de auditoria tem portão? ────────────────────────────────────
echo
echo "── P3 · /v1/audit/* sem credencial ───────────────────────────────────────"
# Primeiro o CONFUNDIDOR: se o analytics estiver em open_access, um 200 sem
# header é decisão declarada, não buraco. Sem ler isto, o P3 acusaria a coisa errada.
#
# ⚠️ `analytics_open_access` NÃO é namespace do config-api — é settings/env
# (`config.py:75`, `PLUGHUB_ANALYTICS_OPEN_ACCESS`). A v1 deste probe consultava
# `$CFG/v1/config/$TENANT/analytics` e imprimia `<não lido>`; eu li o valor por
# `grep` no compose, à mão, e só então soube que o 200 era bypass declarado.
# Ler do CONTAINER, que é onde o valor vale.
OPEN="$($COMPOSE exec -T analytics-api printenv PLUGHUB_ANALYTICS_OPEN_ACCESS < /dev/null 2>/dev/null | tr -d '\r')"
echo "      PLUGHUB_ANALYTICS_OPEN_ACCESS : ${OPEN:-<vazio ⇒ default false>}"

C_SEM="$(code "$AN/v1/audit/mcp-calls?limit=1")"
C_LIXO="$(code -H 'Authorization: Bearer nao-e-um-token' "$AN/v1/audit/mcp-calls?limit=1")"
echo "      GET /v1/audit/mcp-calls sem header   : $C_SEM"
echo "      GET /v1/audit/mcp-calls token inválido: $C_LIXO"

# CONTROLE: o serviço precisa saber RECUSAR alguma coisa. Um caso permitido que
# passa não prova que o portão existe — prova só que aquele caso é permitido.
C_CTRL="$(code "$AN/v1/audit/sessions/nao-existe-esta-sessao/messages")"
echo "      CONTROLE — /v1/audit/sessions/{id}/messages sem header : $C_CTRL"

if [ "$OPEN" = "true" ]; then
  # ⚠️ METADE NÃO EXERCITADA, e o portão TEM de dizer isso. Com o bypass ligado,
  # `_check_audit_access` devolve no primeiro ramo e o ABAC nunca é alcançado:
  # um 200 aqui é compatível com "gate correto" E com "gate inexistente".
  echo "   ⚠️  bypass de demo LIGADO — este probe NÃO julga o gate ABAC."
  echo "      Um 200 aqui não distingue gate correto de gate ausente. A metade"
  echo "      ABAC é coberta por teste unitário (test_audit_gate.py), não daqui."
  if [ "$C_SEM" = "200" ]; then
    echo "      (200 sem header = comportamento ESPERADO sob open_access=true)"
  else
    echo "   ❌ com open_access=true era esperado 200, veio $C_SEM — o bypass quebrou."
    RC=1
  fi
elif [ "$C_SEM" = "403" ] || [ "$C_SEM" = "401" ]; then
  echo "   ✅ recusa sem credencial ($C_SEM) com o bypass desligado — há portão."
elif [ "$C_SEM" = "200" ]; then
  echo "   ❌ passa SEM credencial e SEM bypass — a superfície de auditoria LGPD"
  echo "      está aberta. (token inválido devolveu $C_LIXO: isso é autenticação,"
  echo "      não autorização — é o que faz o defeito parecer coberto.)"
  RC=1
else
  echo "   ⛔ INCONCLUSIVO — código $C_SEM não é nem passagem nem recusa."
  [ "$RC" -eq 0 ] && RC=3
fi

# ── P4 · a promessa de registrar o acesso ────────────────────────────────────
# ANTES × DEPOIS com a base CONTADA: um contador absoluto não distingue "gravou
# agora" de "já tinha linha de ontem".
echo
echo "── P4 · 'todo acesso é registrado' — é verdade? ──────────────────────────"
if [ "${T_AUDIT:-0}" = "0" ]; then
  echo "   ❌ não há onde registrar: audit_access_log não existe, e audit.py não"
  echo "      tem INSERT. O docstring (audit.py:8) e o banner da UI afirmam que"
  echo "      todo acesso fica registrado. A afirmação é falsa."
  RC=1
else
  N_ANTES="$(CH "SELECT count() FROM $DB.audit_access_log")"
  code "$AN/v1/audit/mcp-calls?limit=1" > /dev/null
  code "$AN/v1/audit/sessions/probe-session-inexistente/messages" > /dev/null
  sleep 2
  N_DEPOIS="$(CH "SELECT count() FROM $DB.audit_access_log")"
  DELTA=$(( ${N_DEPOIS:-0} - ${N_ANTES:-0} ))
  echo "      linhas antes : ${N_ANTES:-?}"
  echo "      2 acessos feitos"
  echo "      linhas depois: ${N_DEPOIS:-?}   (delta $DELTA, previsto 2)"
  if [ "$DELTA" -ge 2 ]; then
    echo "   ✅ o acesso é registrado — a promessa da tela virou verdade"
    CH "SELECT actor_kind, endpoint, result, row_count FROM $DB.audit_access_log ORDER BY accessed_at DESC LIMIT 4" | sed 's/^/         /'
  elif [ "$DELTA" -gt 0 ]; then
    echo "   ❌ registrou $DELTA de 2 — um dos dois caminhos não grava"
    RC=1
  else
    echo "   ❌ tabela existe e NADA foi gravado — pior que a ausência, porque"
    echo "      agora a tabela vazia parece 'ninguém acessou'."
    RC=1
  fi
fi

echo
echo "══ veredicto: rc=$RC (0 nada a apontar · 1 achado · 3 inconclusivo) ══"
exit "$RC"
