#!/usr/bin/env bash
# cleanup_approval_orphan_segments.sh — apaga os órfãos PRÉ-FIX da aprovação.
#
# CONTEXTO, porque a limpeza só é legítima depois dele. Os 17 segmentos humanos
# abertos de `aprovacao_deploy` foram MANTIDOS em 2026-08-03 como instrumento: o
# TODO afirmava que "a aprovação SEGUE produzindo órfãos". A medição refutou —
# todos vão de 2026-07-16 a 2026-07-24, o último SEIS DIAS antes do fix H1/H2 — e
# o `smoke_approval_segment_closes.sh` (7/7) provou o positivo: hoje a aprovação
# fecha o segmento com `close_reason=task_submitted`.
#
# O instrumento já respondeu. Sem isso, apagar teria destruído a única evidência
# disponível sobre uma pergunta em aberto — foi exatamente o erro que o TODO §
# "Erros de método" registra em *"limpar antes de medir"*.
#
# CORTE DELIBERADO: só apaga o que é ANTERIOR ao fix. Órfão posterior é evidência
# de defeito vivo e não pode ser varrido junto — se aparecer algum, o script PARA.
#
# `ALTER TABLE … DELETE` é MUTAÇÃO ASSÍNCRONA no ClickHouse: o comando retorna
# antes de o dado sair. Por isso a contagem é conferida DEPOIS, com espera — o
# retorno do comando não é prova de nada.
#
# Uso:  bash infra/scripts/cleanup_approval_orphan_segments.sh [--apply]
#       (sem --apply = dry-run; imprime o que sairia e não toca em nada)
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
DB=plughub_demo
CUT="2026-07-30"
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

WHERE="tenant_id='$TENANT' AND agent_type='human' AND ended_at IS NULL AND pool_id LIKE 'aprovacao%'"

echo "── alvo: segmentos humanos ABERTOS de pools 'aprovacao*'"
chq "SELECT multiIf(date < toDate('$CUT'),'ANTES do fix (alvo)','DEPOIS do fix (NÃO tocar)') AS janela,
            count() AS n, min(started_at) AS primeiro, max(started_at) AS ultimo
       FROM $DB.segments FINAL WHERE $WHERE GROUP BY janela ORDER BY janela
       FORMAT PrettyCompactMonoBlock"

# Exceção NOMINAL, nunca por padrão. `ALLOW_SESSIONS` lista session_ids cuja causa
# já foi determinada, e o script IMPRIME cada uma com o motivo — o custo de abrir a
# exceção é ter de escrevê-la. Uma allowlist por POOL seria cega (qualquer vazamento
# futuro naquele pool entraria junto, calado); por SESSÃO, cada linha isentada é uma
# decisão datada e auditável.
#
# Conhecida em 2026-08-03: `63effb77-8efa-4e81-99a5-536d033569e6` — 1ª rodada do
# `smoke_approval_segment_closes.sh`, que reivindicou o item e falhou no resume com
# 422 (corpo sem `tenant_id`). Claim sem submit ⇒ segmento aberto. NÃO é defeito da
# aprovação; é a **lacuna 2** (não há reaper de claim abandonado) exposta por um teste
# malformado — e vale como achado: a lacuna é alcançável por acidente trivial.
ALLOW_SESSIONS="${ALLOW_SESSIONS:-63effb77-8efa-4e81-99a5-536d033569e6}"
ALLOW_SQL=""
if [ -n "$ALLOW_SESSIONS" ]; then
  ALLOW_SQL=" AND session_id NOT IN ('$(printf '%s' "$ALLOW_SESSIONS" | sed "s/,/','/g")')"
  echo "── exceções nominais (causa determinada, isentas do corte pós-fix):"
  # `echo`, não `printf '%s'`: sem a quebra final a próxima linha cola no último id.
  echo "$ALLOW_SESSIONS" | tr ',' '\n' | sed 's/^/     /'
fi

POST=$(chq "SELECT count() FROM $DB.segments FINAL WHERE $WHERE AND date >= toDate('$CUT')$ALLOW_SQL" | tr -d '\r')
if [ "${POST:-0}" -gt 0 ] 2>/dev/null; then
  echo
  echo "❌ ABORTADO: há $POST órfão(s) POSTERIOR(es) a $CUT sem causa determinada."
  echo "   Isso contradiz o smoke e significa defeito VIVO. Apagar agora destruiria"
  echo "   a evidência — que é o erro que esta limpeza existe para não repetir."
  echo "   Investigue e, se a causa for conhecida, acrescente o session_id a"
  echo "   ALLOW_SESSIONS com o motivo NO CÓDIGO — não por variável de ambiente solta."
  exit 1
fi

BEFORE=$(chq "SELECT count() FROM $DB.segments FINAL WHERE $WHERE" | tr -d '\r')
echo "   total a apagar: ${BEFORE:-?}"

if [ "$APPLY" != "1" ]; then
  echo
  echo "   (dry-run) rode com --apply para executar."
  exit 0
fi

echo "── apagando (só o PRÉ-corte; as exceções nominais também saem, com motivo)"
chq "ALTER TABLE $DB.segments DELETE WHERE $WHERE AND date < toDate('$CUT')" >/dev/null
if [ -n "$ALLOW_SESSIONS" ]; then
  chq "ALTER TABLE $DB.segments DELETE WHERE $WHERE AND session_id IN ('$(printf '%s' "$ALLOW_SESSIONS" | sed "s/,/','/g")')" >/dev/null
fi

echo "── conferindo (mutação é assíncrona — o retorno acima não prova nada)"
for i in $(seq 1 20); do
  NOW=$(chq "SELECT count() FROM $DB.segments FINAL WHERE $WHERE" | tr -d '\r')
  echo "   tentativa $i: restam ${NOW:-?}"
  [ "${NOW:-1}" = "0" ] && { echo "   ✅ zero órfãos de aprovação."; exit 0; }
  sleep 3
done
echo "   ⚠️  ainda restam ${NOW:-?} após 60 s. Mutação pendente ou filtro incompleto:"
chq "SELECT mutation_id, command, is_done, latest_fail_reason
       FROM system.mutations WHERE table='segments' AND is_done=0
       FORMAT PrettyCompactMonoBlock"
exit 2
