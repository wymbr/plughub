#!/usr/bin/env bash
# probe_transition.sh — Fase 2 do arco de workflow: a transição existe e FECHA.
#
# A transição é a LACUNA entre dois segmentos da mesma sessão (suspend → espera →
# resume). Até aqui ela não tinha lugar nomeado: `suspend_reason` só existia em
# `workflow_events` (produtor morto, zero linhas) e `resume_expires_at` só em Redis.
# Ver ADR journey/session/segment §5 (D4).
#
# Uso:  bash infra/test/probe_transition.sh [session_id]
#       sem argumento, mede a transição mais recente do tenant.
#
# Veredicto de TRÊS estados: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
set -uo pipefail

TENANT="${TENANT:-tenant_demo}"
DB="${CH_DB:-plughub_demo}"
SESSION="${1:-}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"

CH() { $COMPOSE exec -T clickhouse clickhouse-client -u plughub --password plughub \
         -d "$DB" --query "$1" < /dev/null 2>&1 | tr -d '\r'; }

# ⚠️ `CH` captura stderr no stdout, então uma query QUEBRADA devolve texto de
# exceção — e `[ "$X" -gt 0 ]` sobre texto falha silenciosamente e é lido como
# "não encontrou nada". Consulta quebrada passando por ausência de defeito é o
# verde acidental que este repositório mais paga. Todo número passa por aqui.
num() {  # num <valor> <rótulo> → ecoa o inteiro, ou aborta INCONCLUSIVO
  case "${1:-}" in
    ''|*[!0-9-]*)
      echo "   ⛔ INCONCLUSIVO — a consulta de '$2' não devolveu número:" >&2
      printf '      %s\n' "${1:-<vazio>}" >&2
      exit 3 ;;
  esac
  echo "$1"
}

echo "══ transição (D4) — tenant=$TENANT db=$DB ══"

# ── Preflight ─────────────────────────────────────────────────────────────────
[ "$(CH "SELECT 41 + 1")" = "42" ] || {
  echo "   ⛔ INCONCLUSIVO — clickhouse-client não respondeu 42"; exit 3; }

EXISTS="$(CH "EXISTS TABLE $DB.session_transitions")"
if [ "$EXISTS" != "1" ]; then
  echo "   ⛔ INCONCLUSIVO — a tabela session_transitions NÃO existe."
  echo "      O DDL roda no boot da analytics-api; rebuild pendente?"
  echo "      Sem a tabela, 'zero linhas' seria lido como 'não houve transição'."
  exit 3
fi

N_TOTAL="$(CH "SELECT count() FROM $DB.session_transitions WHERE tenant_id='$TENANT'")"
echo "   preflight: tabela existe · linhas no tenant = $N_TOTAL"
if [ "${N_TOTAL:-0}" -eq 0 ]; then
  echo "   ⛔ INCONCLUSIVO — nenhuma transição gravada."
  echo "      Rode um ciclo real (smoke_formfill_renderer.sh + claim/submit no Console,"
  echo "      ou gate_external_resume.sh) e meça de novo. Um 0 aqui é ausência de"
  echo "      AMOSTRA, não prova de que o produtor está mudo."
  exit 3
fi

if [ -z "$SESSION" ]; then
  SESSION="$(CH "SELECT session_id FROM $DB.session_transitions FINAL
                 WHERE tenant_id='$TENANT' ORDER BY row_version DESC LIMIT 1")"
  echo "   sessão (mais recente): $SESSION"
fi

# ── A transição, como os consumidores a leem ──────────────────────────────────
echo
echo "── A) linha da transição (FINAL) ─────────────────────────────────────────"
# ⚠️ Alias NUNCA repete nome de coluna real: no ClickHouse o alias do SELECT é
# visível no resto da query, então `toString(suspended_at) AS suspended_at` faz o
# `dateDiff` receber String e a query inteira morre com ILLEGAL_TYPE_OF_ARGUMENT.
# O CLAUDE.md documenta isto para alias de AGREGADO (`any(pool_id) AS pool_id`);
# vale igual para função simples. Sufixo `_s` resolve. (Custou uma execução em
# 2026-08-10 — e a exceção saiu ao lado de um ✅, que é o pior arranjo possível.)
CH "SELECT resume_token, step_id, suspend_reason, outcome, resume_origin,
           toString(suspended_at)      AS suspended_at_s,
           toString(resumed_at)        AS resumed_at_s,
           toString(resume_expires_at) AS expires_at_s,
           dateDiff('millisecond', suspended_at, resumed_at) AS lacuna_ms
    FROM $DB.session_transitions FINAL
    WHERE tenant_id='$TENANT' AND session_id='$SESSION'
    ORDER BY suspended_at
    FORMAT Vertical"

# ── B) versões cruas: os DOIS escritores dispararam? (tem PRAZO) ──────────────
# `FINAL` mostra a linha vencedora e não distingue "abertura + fechamento" de
# "só fechamento". Sem esta lente, a linha aberta poderia nunca ser escrita e o
# probe seguiria verde — o mesmo ponto cego da lente B do probe_sequence_index.
# O merge do ClickHouse apaga a evidência; medir logo após o ciclo.
echo
echo "── B) sem FINAL — versões cruas por token (o merge apaga isto) ───────────"
CH "SELECT resume_token, count() AS versoes,
           groupArray(outcome) AS outcomes
    FROM $DB.session_transitions
    WHERE tenant_id='$TENANT' AND session_id='$SESSION'
    GROUP BY resume_token
    FORMAT PrettyCompactMonoBlock"

RC=0
read -r N_TR N_OPEN N_NOREASON N_NORESUME <<<"$(CH "
  SELECT count(),
         countIf(outcome='open'),
         countIf(suspend_reason=''),
         countIf(resumed_at IS NULL)
  FROM $DB.session_transitions FINAL
  WHERE tenant_id='$TENANT' AND session_id='$SESSION'
  FORMAT TSV")"
N_TR="$(num "${N_TR:-}" 'transições')"
N_OPEN="$(num "${N_OPEN:-}" 'abertas')"
N_NOREASON="$(num "${N_NOREASON:-}" 'sem motivo')"
N_NORESUME="$(num "${N_NORESUME:-}" 'sem resumed_at')"

# Os dois escritores: 2 versões cruas = abertura E fechamento; 1 = só um deles.
N_PAIRED="$(num "$(CH "SELECT countIf(v > 1) FROM (
                        SELECT count() AS v FROM $DB.session_transitions
                        WHERE tenant_id='$TENANT' AND session_id='$SESSION'
                        GROUP BY resume_token)")" 'tokens com 2 versões')"

echo
echo "   transições=$N_TR · abertas=$N_OPEN · sem motivo=$N_NOREASON · sem resumed_at=$N_NORESUME"
echo "   tokens com abertura E fechamento (cru): $N_PAIRED de $N_TR"
if [ "$N_PAIRED" -eq 0 ]; then
  # A ambiguidade "merge comeu × nunca escreveu" é RESOLVÍVEL no escopo do tenant:
  # o merge não apaga a linha aberta de TODOS os tokens de forma uniforme em
  # segundos. Se nenhuma linha do tenant jamais teve `outcome='open'`, o produtor
  # do suspend está mudo — e isso é reprovação, não aviso. Deixar como ⚠️ foi o que
  # fez o probe imprimir ✅ sobre metade do caminho não exercitada (2026-08-10).
  RAW_ROWS="$(num "$(CH "SELECT count() FROM $DB.session_transitions
                         WHERE tenant_id='$TENANT'")" 'linhas cruas no tenant')"
  RAW_OPEN="$(num "$(CH "SELECT countIf(outcome='open') FROM $DB.session_transitions
                         WHERE tenant_id='$TENANT'")" 'linhas abertas no tenant')"
  echo "   escopo do tenant: linhas cruas=$RAW_ROWS · com outcome='open'=$RAW_OPEN"
  if [ "$RAW_OPEN" -eq 0 ] && [ "$RAW_ROWS" -ge 3 ]; then
    echo "   ❌ REPROVOU — NENHUMA linha aberta em $RAW_ROWS escritas do tenant."
    echo "      Não é merge: ele não colapsaria a abertura de todos os tokens de forma"
    echo "      uniforme. O produtor do lado do SUSPEND está mudo — a tabela só tem o"
    echo "      que o resume gravou, e 'por que esta sessão está parada AGORA?' (o"
    echo "      motivo da D4) segue sem resposta, porque sessão parada não tem linha."
    RC=1
  else
    echo "   ⚠️  este token não tem as duas versões, mas o tenant tem linhas abertas —"
    echo "      provável merge. Medir mais cedo para provar o par."
  fi
fi

# ── Julgamentos ───────────────────────────────────────────────────────────────
if [ "${N_TR:-0}" -eq 0 ]; then
  echo "   ⛔ INCONCLUSIVO — a sessão escolhida não tem transição."; exit 3
fi

# 1. Motivo vazio = o RMT apagou a linha aberta OU o resume_meta não tinha reason.
#    É o modo de falha CENTRAL desta fase: o resume grava a linha inteira lendo o
#    meta ANTES do HDEL; se ele mandar parcial, o motivo desaparece em silêncio.
if [ "${N_NOREASON:-0}" -gt 0 ]; then
  echo "   ❌ REPROVOU — $N_NOREASON transição(ões) com suspend_reason VAZIO."
  echo "      Duas causas, e as duas importam: (a) o resume escreveu linha PARCIAL e o"
  echo "      ReplacingMergeTree apagou o motivo da linha aberta; (b) o resume_meta"
  echo "      não tinha reason (engine não propagou). O log do bridge distingue:"
  echo "      'resume_meta sem suspend_reason'."
  RC=1
fi

# 2. Coerência temporal. Lacuna negativa = suspended_at veio do lugar errado.
NEG="$(num "$(CH "SELECT count() FROM $DB.session_transitions FINAL
           WHERE tenant_id='$TENANT' AND session_id='$SESSION'
             AND resumed_at IS NOT NULL AND resumed_at < suspended_at")" 'lacuna negativa')"
if [ "${NEG:-0}" -gt 0 ]; then
  echo "   ❌ REPROVOU — $NEG transição(ões) com resumed_at ANTES de suspended_at."
  RC=1
fi

# 3. Lacuna de zero é suspeita, não erro: significa que `suspended_at` caiu no
#    fallback do instante do resume (meta ausente). Valor plausível escondendo
#    fato ausente — a família que este repositório mais paga.
ZERO="$(num "$(CH "SELECT count() FROM $DB.session_transitions FINAL
            WHERE tenant_id='$TENANT' AND session_id='$SESSION'
              AND resumed_at IS NOT NULL
              AND dateDiff('millisecond', suspended_at, resumed_at) = 0")" 'lacuna zero')"
if [ "${ZERO:-0}" -gt 0 ]; then
  echo "   ❌ REPROVOU — $ZERO transição(ões) com lacuna de 0 ms."
  echo "      Zero aqui não é 'retomou instantâneo': é o fallback de suspended_at"
  echo "      caindo no instante do resume porque o resume_meta sumiu."
  RC=1
fi

# 4. Aberta é ESTADO VÁLIDO (sessão parada agora), não falha — mas se TODAS
#    estiverem abertas o fechamento nunca foi exercitado, e o probe não pode
#    afirmar que ele funciona.
if [ "${N_OPEN:-0}" -eq "${N_TR:-0}" ]; then
  echo "   ⚠️  todas as transições estão ABERTAS — o fechamento não foi exercitado."
  echo "      Não é reprovação (sessão suspensa é estado legítimo), mas o verde abaixo"
  echo "      NÃO cobriria o caminho do resume. Retome a sessão e meça de novo."
  [ "$RC" -eq 0 ] && exit 3
fi

echo
if [ "$RC" -eq 0 ]; then
  echo "✅ OK — a lacuna tem começo, fim, motivo e desfecho, num lugar nomeado."
else
  echo "❌ REPROVOU."
fi
exit "$RC"
