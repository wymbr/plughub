#!/usr/bin/env bash
# check_wrapup_prose_persisted.sh — SONDA (lê, não cria) do fix de 2026-07-30:
# o resumo e os próximos passos do wrap-up passaram a ser gravados em TODA
# disposição, inclusive `resolved` — que é o caso mais comum e o que descartava.
#
# ANTES: `segment_outcome_record` só montava `handoff_reason` quando
# `outcome !== "resolved"`. Um wrap-up resolvido com resumo preenchido gravava
#     outcome: resolved | issue_status: resolvido | handoff_reason: NULL
# e o texto sumia — sem erro, sem log, sem sinal na tela.
#
# DEPOIS: `wrapup_summary` / `wrapup_next_steps` (colunas próprias). O
# `handoff_reason` fica INALTERADO de propósito: ele define `handoff_rate`
# (`countIf(handoff_reason != '') / count()`), e escrever o resumo ali levaria a
# taxa de repasse a ~100% — trocar perda silenciosa por métrica que muda de
# sentido é piorar.
#
# COMO CRIAR A CONDIÇÃO:
#   atenda um contato, encerre e SUBMETA o wrap-up com classificação **Resolvido**
#   e o campo Resumo PREENCHIDO. É o caso exato que falhava.
#
# Uso (raiz do repo):
#   bash infra/test/check_wrapup_prose_persisted.sh
#   SINCE_MIN=180 bash infra/test/check_wrapup_prose_persisted.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CH_DB="plughub_demo"
CH="$COMPOSE exec -T clickhouse clickhouse-client"
SINCE_MIN="${SINCE_MIN:-60}"

pass=0; fail=0
ok()  { echo "   ✅ $1"; pass=$((pass+1)); }
bad() { echo "   ❌ $1"; fail=$((fail+1)); }

echo "══ 0) as colunas existem (migration idempotente rodou) ══"
COLS=$($CH -q "SELECT count() FROM system.columns \
       WHERE database='${CH_DB}' AND table='segments' \
       AND name IN ('wrapup_summary','wrapup_next_steps')" | tr -d '\r')
if [ "${COLS:-0}" -eq 2 ]; then
  ok "wrapup_summary e wrapup_next_steps presentes em ${CH_DB}.segments"
else
  bad "esperado 2 colunas, achei ${COLS:-0} — o analytics-api rebuildou e reiniciou?"
  echo "══════════════════════════════════════"
  echo "  passou: $pass    falhou: $fail"
  exit 1
fi

# ── Corte de deploy — a asserção só vale sobre linha escrita DEPOIS do fix ─────
#
# A 1ª versão desta sonda filtrava por `started_at` numa janela de minutos, e
# reprovou com dado PRÉ-FIX: wrap-ups submetidos antes do rebuild, que não tinham
# como carregar as colunas novas. Afirmar sobre uma amostra que não pode satisfazer
# a asserção é o mesmo erro de método que estas sondas existem para evitar.
#
# O timestamp certo é `ingested_at`, não `started_at`: a linha do segmento é
# RE-PUBLICADA no submit do wrap-up (ReplacingMergeTree(ingested_at) + FINAL ⇒ a
# sobrevivente carrega o instante da última escrita). `started_at` é quando o
# atendimento começou — pode ser muito anterior ao deploy.
#
# O corte é o start do container MAIS NOVO entre os três do caminho: antes dele o
# caminho não era capaz, e nenhuma linha ali pode ser cobrada.
CUTOFF=""
for svc in analytics-api orchestrator-bridge mcp-server-plughub; do
  CID=$($COMPOSE ps -q "$svc" 2>/dev/null | head -1)
  [ -n "$CID" ] || continue
  ST=$(docker inspect -f '{{.State.StartedAt}}' "$CID" 2>/dev/null | cut -c1-19 | tr 'T' ' ')
  [ -n "$ST" ] || continue
  [ -z "$CUTOFF" ] && CUTOFF="$ST"
  [[ "$ST" > "$CUTOFF" ]] && CUTOFF="$ST"
done
if [ -z "$CUTOFF" ]; then
  echo "   ⚠️  não consegui derivar o corte de deploy dos containers; usando SINCE_MIN"
  CUT_SQL="ingested_at >= now() - INTERVAL $SINCE_MIN MINUTE"
else
  echo "   corte de deploy (container mais novo do caminho): $CUTOFF UTC"
  CUT_SQL="ingested_at >= toDateTime('$CUTOFF', 'UTC')"
fi

# Segmentos humanos COM wrap-up submetido (issue_status é o discriminador — nada
# mais no sistema o escreve) e disposição RESOLVIDA: o caso que descartava.
WHERE="tenant_id='$TENANT' AND agent_type='human' \
       AND $CUT_SQL \
       AND ifNull(issue_status,'') != '' \
       AND outcome='resolved'"

echo "══ janela: submissões após o corte de deploy ══"
TOTAL=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL WHERE $WHERE" | tr -d '\r')
echo "   wrap-ups RESOLVIDOS submetidos: $TOTAL"

# Ausência de amostra RESOLVIDA NÃO aborta a sonda (corrigido 2026-07-31).
#
# A 1ª versão saía aqui com `exit 2`, e com isso o bloco C — regressão do caminho
# NÃO-resolvido, que não depende desta amostra — ficava inalcançável. Um bloco que
# nunca chega a executar não é verde nem vermelho: é silêncio que se parece com
# cobertura. Cada lado agora se declara INCONCLUSIVO por conta própria.
RESOLVED_SEEN=1
if [ "${TOTAL:-0}" -eq 0 ]; then
  RESOLVED_SEEN=0
  echo
  echo "   ⚠️  NENHUMA amostra RESOLVIDA após o corte de deploy — A e B INCONCLUSIVOS."
  echo "      Encerre um contato e submeta o wrap-up com classificação RESOLVIDO e"
  echo "      o Resumo preenchido; é o caso exato que o fix endereça."
  echo "      (wrap-ups submetidos ANTES do rebuild ficam de fora de propósito —"
  echo "       não tinham como gravar as colunas novas.)"
  echo "      Seguindo para o bloco C, que é independente desta amostra."
fi

if [ "$RESOLVED_SEEN" -eq 1 ]; then
  echo "══ detalhe ══"
  $CH -q "SELECT substring(session_id,1,8) AS sess, pool_id, outcome, issue_status, \
          ifNull(handoff_reason,'∅')    AS handoff_reason, \
          ifNull(wrapup_summary,'∅')    AS wrapup_summary, \
          ifNull(wrapup_next_steps,'∅') AS next_steps, ingested_at \
          FROM ${CH_DB}.segments FINAL WHERE $WHERE \
          ORDER BY ingested_at DESC LIMIT 10 FORMAT PrettyCompact"

  echo "══ A) o resumo sobreviveu ao caso 'resolvido' ══"
  MISSING=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL \
            WHERE $WHERE AND ifNull(wrapup_summary,'')=''" | tr -d '\r')
  if [ "${MISSING:-1}" -eq 0 ]; then
    ok "$TOTAL wrap-up(s) resolvido(s), todos com wrapup_summary"
  else
    echo "   ⚠️  $MISSING de $TOTAL sem wrapup_summary."
    echo "      Isto reprova SÓ se o Resumo foi realmente preenchido no formulário —"
    echo "      campo vazio grava vazio, e isso é correto. Confira o detalhe acima."
    bad "$MISSING de $TOTAL sem wrapup_summary (ver ressalva)"
  fi

  echo "══ B) handoff_reason NÃO foi contaminado (handoff_rate intacto) ══"
  # A tentação era escrever o resumo em handoff_reason. Se alguém o fizer, a taxa de
  # repasse vira ~100% e nenhum alarme dispara — por isso a asserção é explícita.
  LEAK=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL \
         WHERE $WHERE AND ifNull(handoff_reason,'') != ''" | tr -d '\r')
  [ "${LEAK:-1}" -eq 0 ] \
    && ok "nenhum segmento resolvido com handoff_reason preenchido" \
    || bad "$LEAK segmento(s) resolvido(s) com handoff_reason — handoff_rate está sendo inflado"
fi

echo "══ C) o caminho não-resolvido continua gravando handoff_reason ══"
# Regressão simétrica: o fix não pode ter quebrado o comportamento antigo.
#
# RESSALVA (simétrica à do bloco A, faltava aqui): a tool só monta `handoff_reason`
# a partir de `resumo` / `proximos_passos` (`segment.ts:109-113`). Wrap-up
# não-resolvido submetido com a prosa VAZIA grava `handoff_reason` vazio, e isso é
# CORRETO — não há o que escrever. Sem esta ressalva o bloco reprovaria por um
# defeito do cenário, não do código.
NR_SEEN=1
NR_WHERE="tenant_id='$TENANT' AND agent_type='human' \
          AND $CUT_SQL \
          AND ifNull(issue_status,'') != '' AND outcome != 'resolved'"
NR=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL WHERE $NR_WHERE" | tr -d '\r')
if [ "${NR:-0}" -eq 0 ]; then
  NR_SEEN=0
  echo "   ⚠️  nenhum wrap-up NÃO-resolvido na janela — regressão NÃO EXERCITADA."
  echo "      Submeta um com 'Escalado' ou 'Pendente' (e o Resumo PREENCHIDO) para"
  echo "      cobrir este lado."
else
  echo "   detalhe (não-resolvidos):"
  $CH -q "SELECT substring(session_id,1,8) AS sess, pool_id, outcome, issue_status, \
          ifNull(handoff_reason,'∅')    AS handoff_reason, \
          ifNull(wrapup_summary,'∅')    AS wrapup_summary, \
          ifNull(wrapup_next_steps,'∅') AS next_steps, ingested_at \
          FROM ${CH_DB}.segments FINAL WHERE $NR_WHERE \
          ORDER BY ingested_at DESC LIMIT 10 FORMAT PrettyCompact"

  # A cobrança é condicional à prosa existir — senão o vazio é a resposta certa.
  NR_WITH_PROSE=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL \
                  WHERE $NR_WHERE AND (ifNull(wrapup_summary,'') != '' \
                                    OR ifNull(wrapup_next_steps,'') != '')" | tr -d '\r')
  if [ "${NR_WITH_PROSE:-0}" -eq 0 ]; then
    NR_SEEN=0
    echo "   ⚠️  $NR não-resolvido(s), nenhum com prosa preenchida — a asserção não se"
    echo "      aplica (sem resumo, handoff_reason vazio é o correto). NÃO EXERCITADO."
  else
    NR_MISSING=$($CH -q "SELECT count() FROM ${CH_DB}.segments FINAL \
                 WHERE $NR_WHERE AND (ifNull(wrapup_summary,'') != '' \
                                   OR ifNull(wrapup_next_steps,'') != '') \
                 AND ifNull(handoff_reason,'')=''" | tr -d '\r')
    [ "${NR_MISSING:-1}" -eq 0 ] \
      && ok "$NR_WITH_PROSE não-resolvido(s) com prosa, todos ainda com handoff_reason" \
      || bad "$NR_MISSING de $NR_WITH_PROSE não-resolvidos com prosa SEM handoff_reason — regressão no caminho antigo"
  fi
fi

echo
echo "══════════════════════════════════════"
echo "  passou: $pass    falhou: $fail"
[ "$fail" -eq 0 ] || exit 1

# Verde parcial não pode se passar por verde completo. Os dois lados são
# independentes e cada um pode faltar sozinho; o exit code diz qual.
if [ "$RESOLVED_SEEN" -eq 0 ] && [ "$NR_SEEN" -eq 0 ]; then
  echo "  ⚠️  INCONCLUSIVO — nenhum dos dois lados foi exercitado nesta janela"
  exit 2
elif [ "$RESOLVED_SEEN" -eq 0 ]; then
  echo "  ⚠️  PARCIAL — só o caminho NÃO-resolvido (C) foi exercitado; A e B seguem sem amostra"
  exit 2
elif [ "$NR_SEEN" -eq 0 ]; then
  echo "  ⚠️  PARCIAL — só o caminho RESOLVIDO (A/B) foi exercitado; C segue sem amostra"
  exit 2
fi
echo "  ✅ prosa do wrap-up persiste em toda disposição (resolvido E não-resolvido)"
