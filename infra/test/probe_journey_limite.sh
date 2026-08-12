#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_journey_limite.sh — as três sessões do processo estão na MESMA journey?
#
# POR QUE EXISTE. Ao separar a espera pelo cliente em processo próprio
# (`limite_entrega`), o *Workflow trace* de `/analise/sessions` caiu de 7 execuções
# para 3: ele é SESSION-scoped, e metade da história mudou de sessão de propósito
# (§11 do design doc — foi o que consertou o re-enfileiramento na fila pull).
#
# O dado não deveria ter se perdido, só mudado de lugar: a leitura ponta-a-ponta
# passa a ser a Vista Processos, que agrupa por journey. Mas isso era SUPOSIÇÃO —
# o caminho testado até hoje era `workflow_trigger` disparado de um AGENTE de
# intake, nunca de dentro de OUTRO WORKFLOW. Este probe mede.
#
# PREVISÃO ESCRITA ANTES DA MEDIÇÃO (2026-08-12), pela leitura do código:
#
#   AGRUPA — três sessões sob uma raiz, igual ao session_id do intake.
#
#   Fundamento: `handle_trigger` (webhook.py:476-486) resolve a raiz lendo
#   `session.root_session_id` do ctx do CHAMADOR, e sempre semeia a tag na sessão
#   nova (`:534`) — logo a herança é transitiva por construção, e um workflow
#   disparando outro workflow não é caso especial. Os dois saltos passam
#   `origin_session_id` (entrada:375, processo:216) e nenhum passa `journey: new`.
#   Nenhum dos pools `limite_*` declara `purpose: internal`, então nenhum é
#   excluído pelo `_apply_contact_scope` do relatório de journeys.
#
#   Se REPROVAR, a previsão estava errada e é defeito de propagação de raiz —
#   não de desenho. Suspeito nº 1: o ctx da sessão da análise já ter expirado ou
#   sido limpo quando `disparar_entrega` roda.
#
# O QUE ELE NÃO JULGA: a tela. Ele afirma sobre o FATO (a coluna em
# `analytics.sessions`) e sobre as duas regras do relatório que poderiam esconder
# um agrupamento correto (pool interno, HAVING de significância). Confirmar na
# Vista Processos continua valendo — mas se este probe reprovar, a tela não tem
# como estar certa.
#
# Veredicto: 0 = agrupa · 1 = NÃO agrupa (defeito) · 2 = inconclusivo (sem amostra).
# Uso: bash infra/test/probe_journey_limite.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
REGISTRY="${REGISTRY:-http://localhost:3300}"   # agent-registry (compose demo: 3300:3300)

chq() { $COMPOSE exec -T clickhouse clickhouse-client -d plughub_demo --query "$1" < /dev/null 2>&1; }

PASS=0; FAIL=0
ok()  { echo "   ✅ $1"; PASS=$((PASS+1)); }
bad() { echo "   ❌ $1"; FAIL=$((FAIL+1)); }
die() { echo "   ⚠️  INCONCLUSIVO: $1"; exit 2; }

echo "══ probe_journey_limite — tenant=$TENANT ══"
echo

# ── Preflight ────────────────────────────────────────────────────────────────
# Duas leituras quebradas são iguais entre si: sem testemunha de que o leitor lê,
# "nenhuma sessão encontrada" é indistinguível de "ClickHouse fora do ar".
echo "0 · preflight — o leitor lê?"
TOTAL=$(chq "SELECT count() FROM sessions FINAL WHERE tenant_id='$TENANT'")
[[ "$TOTAL" =~ ^[0-9]+$ ]] || die "ClickHouse não respondeu um número: $TOTAL"
[[ "$TOTAL" -gt 0 ]]       || die "zero sessões no tenant — nada a medir"
echo "   ($TOTAL sessões no tenant; o leitor lê)"
echo

# ── Âncora: a sessão de ENTREGA mais recente ─────────────────────────────────
# É a última a nascer na cadeia, logo a partir dela se caminha para trás sem
# ambiguidade entre ensaios. Ancorar no intake pegaria um ensaio que parou no meio.
echo "1 · a cadeia intake → análise → entrega"
C=$(chq "SELECT session_id FROM sessions FINAL
         WHERE tenant_id='$TENANT' AND pool_id='limite_entrega'
         ORDER BY opened_at DESC LIMIT 1")
[[ -n "$C" ]] || die "nenhuma sessão em limite_entrega — rode um caso completo antes
      (webchat → limite_ia → aprovar no Console). Ausência de amostra não é verde."

read -r C_ROOT C_ORIGIN C_SPAWN <<<"$(chq "
  SELECT root_session_id, coalesce(origin_session_id,''), coalesce(spawn_reason,'')
  FROM sessions FINAL WHERE tenant_id='$TENANT' AND session_id='$C'" | tr '\t' ' ')"
B="$C_ORIGIN"
[[ -n "$B" ]] || bad "a entrega ($C) não tem origin_session_id — o fio de proveniência
      se rompeu no workflow_trigger de disparar_entrega"

if [[ -n "$B" ]]; then
  read -r B_ROOT B_ORIGIN B_POOL <<<"$(chq "
    SELECT root_session_id, coalesce(origin_session_id,''), pool_id
    FROM sessions FINAL WHERE tenant_id='$TENANT' AND session_id='$B'" | tr '\t' ' ')"
  A="$B_ORIGIN"
  [[ -n "$A" ]] || bad "a análise ($B) não tem origin_session_id — o fio se rompeu já no
      trigger do intake"
  if [[ -n "$A" ]]; then
    read -r A_ROOT A_POOL <<<"$(chq "
      SELECT root_session_id, pool_id
      FROM sessions FINAL WHERE tenant_id='$TENANT' AND session_id='$A'" | tr '\t' ' ')"
  fi
fi

echo "   intake   A=${A:-<ausente>}  pool=${A_POOL:-?}  root=${A_ROOT:-?}"
echo "   análise  B=${B:-<ausente>}  pool=${B_POOL:-?}  root=${B_ROOT:-?}"
echo "   entrega  C=$C  pool=limite_entrega  root=$C_ROOT  spawn=${C_SPAWN:-<vazio>}"
echo

# ── A asserção central ───────────────────────────────────────────────────────
echo "2 · as três raízes coincidem?"
if [[ -n "${A_ROOT:-}" && "$A_ROOT" == "$B_ROOT" && "$B_ROOT" == "$C_ROOT" ]]; then
  ok "raiz única: $A_ROOT — a herança é transitiva através de dois workflow_trigger"
  [[ "$A_ROOT" == "$A" ]] \
    && ok "a raiz é o próprio intake (topo da árvore, como previsto)" \
    || ok "raiz herdada de sessão anterior ($A_ROOT) — journey maior; não é defeito"
else
  bad "raízes DIVERGEM (A=${A_ROOT:-?} B=${B_ROOT:-?} C=$C_ROOT)"
  echo "      ⇒ a Vista Processos vai mostrar linhas separadas. Investigar a leitura"
  echo "        de session.root_session_id do ctx do chamador em handle_trigger."
fi

# `spawn_reason` é o rótulo da aresta (Journey T4). Ausente = a sessão se declara
# topo de árvore, o que contradiria o origin_session_id que ela mesma carrega.
[[ "$C_SPAWN" == "trigger" ]] \
  && ok "spawn_reason='trigger' na entrega — a aresta foi registrada" \
  || bad "spawn_reason='${C_SPAWN:-<vazio>}' na entrega (esperado 'trigger')"
echo

# ── As duas regras do relatório que esconderiam um agrupamento correto ───────
echo "3 · o relatório de journeys deixaria as três aparecerem?"
POOLS_JSON=$(curl -s --max-time 10 -H "x-tenant-id: $TENANT" "$REGISTRY/v1/pools" 2>&1)
if ! echo "$POOLS_JSON" | grep -q '"pools"'; then
  echo "   ⚠️  agent-registry não respondeu — regra do pool interno NÃO verificada"
  echo "      (o probe segue; isto não invalida a asserção 2)"
else
  INTERNAL=$(echo "$POOLS_JSON" | jq -r \
    '.pools[] | select(.purpose=="internal") | .pool_id' 2>/dev/null | tr '\n' ' ')
  echo "   pools internos do tenant: ${INTERNAL:-<nenhum>}"
  HIT=""
  for p in limite_ia limite_processo limite_entrega; do
    [[ " $INTERNAL " == *" $p "* ]] && HIT="$HIT $p"
  done
  [[ -z "$HIT" ]] \
    && ok "nenhum pool do cenário é purpose=internal — _apply_contact_scope não exclui" \
    || bad "pool(s) interno(s) no cenário:$HIT — a journey some do relatório mesmo com a raiz certa"
fi

# significant_only: HAVING (count() > 1 OR has(channel,'webhook')). Com 3 sessões e
# duas de canal webhook, passa pelos dois lados. Afirmado para nomear a regra.
N=$(chq "SELECT count() FROM sessions FINAL
         WHERE tenant_id='$TENANT' AND root_session_id='${A_ROOT:-__none__}'")
[[ "$N" =~ ^[0-9]+$ && "$N" -gt 1 ]] \
  && ok "a journey tem $N sessões — passa o HAVING de significância (count>1)" \
  || bad "a journey tem ${N:-?} sessão(ões) — significant_only=true a esconderia"
echo

# ── Veredicto ────────────────────────────────────────────────────────────────
echo "══ $PASS ok · $FAIL falha(s) ══"
if [[ $FAIL -eq 0 ]]; then
  echo "✅ VERDE — a previsão se confirmou: as três sessões formam uma journey."
  echo "   Abrir /analise/processos e buscar a raiz ${A_ROOT:-?} para ver o drill de 3 níveis."
  exit 0
fi
echo "❌ DEFEITO — a previsão falhou. Ler a seção PREVISÃO no topo deste arquivo:"
echo "   o que caiu foi a propagação de raiz, não o desenho de separar a entrega."
exit 1
