#!/usr/bin/env bash
# probe_session_volume_origin.sh — de onde vieram os +167 contatos (item 2 do kickoff).
#
# O FATO: duas leituras de `/analise/sessions`, MESMA janela (07/08→14/08), MESMO escopo,
# MESMO build, separadas por uma execução de e2e: 118 → 285. O e2e deveria criar UMA
# sessão (≤ ~10 com as internas). Sobraram ~167 sem dono.
#
# As duas explicações têm consequências OPOSTAS, e por isso o probe não pergunta "quantos":
#   (a) a suíte rodou muito mais do que um cenário  → operação normal, item morre aqui;
#   (b) algo produz contato sozinho                 → contamina contagem, TMA e atribuição
#       por pool de TODO o demo, inclusive as medições que a F1b e a F3 usaram como base.
#
# QUATRO FINGERPRINTS, cada um separando (a) de (b):
#   · CONCENTRAÇÃO NO TEMPO — rajada em poucos minutos = suíte; espalhado = produtor vivo.
#   · POOL/CANAL            — pool de teste = suíte; pool de produção do demo = produtor.
#   · SEGMENTOS = 0         — sessão sem NENHUM segmento não passou por participante algum.
#                             É a assinatura de INSERT direto no ClickHouse pelos
#                             `infra/test/seed_*.sh`, que é também exatamente o caminho
#                             que entra sem `origin` e cai no default 'live'.
#   · ORIGIN                — o discriminador existe para isto. Se tudo é 'live', ou não há
#                             importação, ou o produtor está entrando pela porta errada.
#
# Read-only. Não apaga, não roda e2e (rodar o e2e para "gerar dado" DUPLICA o fenômeno
# sob investigação — e ainda apaga `tenant_demo:*` e `session:*` antes de cada cenário).
#
# Uso:  bash infra/test/probe_session_volume_origin.sh [tenant] [desde]
#       desde = 'YYYY-MM-DD HH:MM:SS' (default 2026-08-07 00:00:00 — a janela da F3)

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
SINCE="${2:-2026-08-07 00:00:00}"
DB="plughub_demo"

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }

# ── preflight ───────────────────────────────────────────────────────────────────
PING=$(chq 'SELECT 1' | tr -d '\r')
if [ "$PING" != "1" ]; then
  echo "⚠️  INCONCLUSIVO: clickhouse não respondeu ('$PING')."; exit 2
fi

TOTAL=$(chq "SELECT count() FROM $DB.sessions FINAL
              WHERE tenant_id='$TENANT' AND opened_at >= '$SINCE'" | tr -d '\r')
echo "── preflight ───────────────────────────────────────────────────────────────"
echo "   tenant=$TENANT  desde='$SINCE'  sessões=$TOTAL"
if [ "${TOTAL:-0}" -eq 0 ] 2>/dev/null; then
  echo "⚠️  INCONCLUSIVO: zero sessões na janela — prefixo/tenant/janela errados."; exit 2
fi

# `origin` entrou por ALTER (ADR quality-substrate-isolation). Se a coluna não existir
# nesta base, dizer isso é honesto; inventar 'live' seria o mesmo defeito que o
# discriminador existe para impedir.
HAS_ORIGIN=$(chq "SELECT count() FROM system.columns
                   WHERE database='$DB' AND table='sessions' AND name='origin'" | tr -d '\r')
echo "   coluna origin presente: ${HAS_ORIGIN:-?}"
echo

# ── 1. concentração no tempo (o corte do kickoff) ───────────────────────────────
echo "── 1. sessões por MINUTO × pool × canal (40 minutos mais recentes) ─────────"
echo "   rajada em poucos minutos ⇒ é a suíte · espalhado ⇒ há produtor ativo"
chq "
  SELECT toStartOfMinute(s.opened_at) AS minuto,
         s.pool_id                    AS pool,
         s.channel                    AS canal,
         count()                      AS n
    FROM $DB.sessions AS s FINAL
   WHERE s.tenant_id='$TENANT' AND s.opened_at >= '$SINCE'
   GROUP BY minuto, pool, canal
   ORDER BY minuto DESC
   LIMIT 40
   FORMAT PrettyCompactMonoBlock"
echo

# ── 2. o total por pool/canal na janela inteira ─────────────────────────────────
echo "── 2. total por pool × canal na janela ─────────────────────────────────────"
chq "
  SELECT s.pool_id      AS pool,
         s.channel      AS canal,
         count()        AS n,
         min(s.opened_at) AS primeiro,
         max(s.opened_at) AS ultimo
    FROM $DB.sessions AS s FINAL
   WHERE s.tenant_id='$TENANT' AND s.opened_at >= '$SINCE'
   GROUP BY pool, canal
   ORDER BY n DESC
   FORMAT PrettyCompactMonoBlock"
echo

# ── 3. sessões SEM NENHUM segmento — assinatura de INSERT direto (seed) ─────────
# Uma sessão real sempre passa por pelo menos um participante. Zero segmentos significa
# que ninguém publicou `conversations.participants` para ela: ou nasceu de INSERT direto,
# ou nasceu de um `conversations.inbound` que nunca foi roteado a lugar nenhum.
echo "── 3. sessões SEM segmento (assinatura de seed / inbound órfão) ────────────"
chq "
  SELECT s.pool_id            AS pool,
         s.channel            AS canal,
         count()              AS sem_segmento,
         min(s.opened_at)     AS primeiro,
         max(s.opened_at)     AS ultimo
    FROM $DB.sessions AS s FINAL
    LEFT ANY JOIN (SELECT DISTINCT session_id, 1 AS tem
                     FROM $DB.segments FINAL WHERE tenant_id='$TENANT') AS g
      ON g.session_id = s.session_id
   WHERE s.tenant_id='$TENANT' AND s.opened_at >= '$SINCE' AND g.tem = 0
   GROUP BY pool, canal
   ORDER BY sem_segmento DESC
   FORMAT PrettyCompactMonoBlock"
echo

# ── 4. origin ───────────────────────────────────────────────────────────────────
if [ "${HAS_ORIGIN:-0}" -gt 0 ] 2>/dev/null; then
  echo "── 4. distribuição de origin na janela ─────────────────────────────────────"
  chq "
    SELECT s.origin   AS origem,
           count()    AS n,
           uniq(s.pool_id) AS pools
      FROM $DB.sessions AS s FINAL
     WHERE s.tenant_id='$TENANT' AND s.opened_at >= '$SINCE'
     GROUP BY origem
     ORDER BY n DESC
     FORMAT PrettyCompactMonoBlock"
else
  echo "── 4. origin ───────────────────────────────────────────────────────────────"
  echo "   ⚠️  coluna ausente nesta base — INCONCLUSIVO para o item 'origin', e não"
  echo "      equivalente a 'tudo é live'."
fi
echo

# ── 5. por dia, para ver se o fenômeno é contínuo ou pontual ────────────────────
echo "── 5. sessões por DIA (o fenômeno é contínuo ou pontual?) ──────────────────"
chq "
  SELECT toDate(s.opened_at) AS dia,
         count()             AS n,
         uniq(s.pool_id)     AS pools,
         countIf(s.closed_at IS NULL) AS abertas
    FROM $DB.sessions AS s FINAL
   WHERE s.tenant_id='$TENANT' AND s.opened_at >= '$SINCE'
   GROUP BY dia
   ORDER BY dia
   FORMAT PrettyCompactMonoBlock"
echo
echo "── leitura ─────────────────────────────────────────────────────────────────"
echo "   Este probe não tem verde/vermelho: ele CLASSIFICA. O veredicto é a resposta"
echo "   à pergunta 'concentrado ou espalhado?', e ele muda o alvo do conserto."
