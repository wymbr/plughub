#!/usr/bin/env bash
# probe_entry_pool_fww.sh — GATE da F1b: o carimbo `entrou por` sobrevive ao ingest.
#
# DIFERENCIAL, e as duas leituras lado a lado, porque o conserto é FORWARD-ONLY:
#   · sessão NOVA (criada depois do deploy) → sessions.pool_id == pool do 1o segmento
#   · sessão HISTÓRICA divergente           → a divergência PERMANECE
# Um gate que contasse a população inteira sairia vermelho para sempre e pareceria
# "o fix não funcionou", quando estaria medindo o passado.
#
# Veredicto de TRÊS estados. Sem sessão nova para julgar, o resultado é
# INCONCLUSIVO — nunca verde por ausência de amostra.
#
# Uso: bash infra/test/probe_entry_pool_fww.sh
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
CH() { $DC exec -T clickhouse clickhouse-client -q "$1" </dev/null; }
DB=plughub_demo
T=tenant_demo
PASS=0; FAIL=0; INCONC=0

ok()  { echo "  ✅ $1"; PASS=$((PASS+1)); }
bad() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
huh() { echo "  ⚠️  INCONCLUSIVO: $1"; INCONC=$((INCONC+1)); }

# ── marco temporal: tudo antes disto é "o passado" ───────────────────────────
T0=$(date -u -d '-1 minute' +'%Y-%m-%d %H:%M:%S')
echo "marco T0 (UTC) = $T0"

echo
echo "── BASE: divergentes ANTES do marco (o passado, que não sara) ──"
HIST_ANTES=$(CH "
WITH primeiro AS (
  SELECT session_id, argMin(pool_id, started_at) AS p
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != ''
  GROUP BY session_id)
SELECT count()
FROM $DB.sessions AS s FINAL
INNER JOIN primeiro ON primeiro.session_id = s.session_id
WHERE s.tenant_id='$T' AND s.pool_id != primeiro.p
  AND s.opened_at < '$T0' FORMAT TSV")
echo "  divergentes históricos = ${HIST_ANTES:-<vazio>}"
if ! [[ "$HIST_ANTES" =~ ^[0-9]+$ ]]; then
  huh "a leitura da base não devolveu número — o resto do gate não pode julgar"
  echo; echo "PASS=$PASS FAIL=$FAIL INCONCLUSIVO=$INCONC"; exit 2
fi

# ── produzir a sessão nova ───────────────────────────────────────────────────
echo
echo "── rodando smoke_limite_tres_acessos.sh (cria processo com troca de pool) ──"
SMOKE_LOG=$(mktemp)
SMOKE_RC=0
if [ "${SKIP_SMOKE:-0}" = "1" ]; then
  # Diagnóstico sobre a população existente, sem criar sessão nova a cada rodada.
  # As asserções 1/1b ficam sem amostra e caem em INCONCLUSIVO — que é o correto:
  # sem sessão nova o gate não julga o conserto, e não deve fingir que julga.
  echo "  SKIP_SMOKE=1 — smoke NÃO executado"
else
  bash infra/test/smoke_limite_tres_acessos.sh > "$SMOKE_LOG" 2>&1 </dev/null
  SMOKE_RC=$?
fi
echo "  exit=$SMOKE_RC · últimas linhas:"
tail -4 "$SMOKE_LOG" | sed 's/^/    /'
# exit != 0 NÃO é o veredicto deste gate: o smoke tem asserções próprias, e o que
# aqui interessa é se ele deixou sessão nova para julgar. Ele é o INSTRUMENTO.

# ── esperar o ingest (ClickHouse não é síncrono) ─────────────────────────────
echo
echo "── esperando a sessão nova aparecer no substrato ──"
NOVAS=0
for _ in $(seq 1 20); do
  NOVAS=$(CH "
  WITH primeiro AS (
    SELECT session_id, argMin(pool_id, started_at) AS p
    FROM $DB.segments FINAL
    WHERE tenant_id='$T' AND pool_id != ''
    GROUP BY session_id)
  SELECT count()
  FROM $DB.sessions AS s FINAL
  INNER JOIN primeiro ON primeiro.session_id = s.session_id
  WHERE s.tenant_id='$T' AND s.opened_at >= '$T0' FORMAT TSV")
  [[ "$NOVAS" =~ ^[0-9]+$ ]] && [ "$NOVAS" -gt 0 ] && break
  sleep 3
done
echo "  sessões novas com segmento = ${NOVAS:-<vazio>}"

# ── ASSERÇÃO 1: a sessão NOVA carimba a entrada ──────────────────────────────
echo
echo "── 1. sessão NOVA: sessions.pool_id == pool do 1o segmento ──"
if ! [[ "$NOVAS" =~ ^[0-9]+$ ]] || [ "$NOVAS" -eq 0 ]; then
  huh "nenhuma sessão nova com segmento — o gate não tem o que julgar (smoke exit=$SMOKE_RC; log em $SMOKE_LOG)"
else
  DIV_NOVAS=$(CH "
  WITH primeiro AS (
    SELECT session_id, argMin(pool_id, started_at) AS p
    FROM $DB.segments FINAL
    WHERE tenant_id='$T' AND pool_id != ''
    GROUP BY session_id)
  SELECT count()
  FROM $DB.sessions AS s FINAL
  INNER JOIN primeiro ON primeiro.session_id = s.session_id
  WHERE s.tenant_id='$T' AND s.opened_at >= '$T0'
    AND s.pool_id != primeiro.p FORMAT TSV")
  echo "  novas=$NOVAS · divergentes entre elas=$DIV_NOVAS"
  if [ "${DIV_NOVAS:-x}" = "0" ]; then
    ok "as $NOVAS sessões novas carimbam o pool de ENTRADA"
  else
    bad "$DIV_NOVAS de $NOVAS sessões novas ainda divergem — o carimbo não pegou"
    CH "
    WITH primeiro AS (
      SELECT session_id, argMin(pool_id, started_at) AS p
      FROM $DB.segments FINAL
      WHERE tenant_id='$T' AND pool_id != ''
      GROUP BY session_id)
    SELECT s.session_id, s.pool_id AS sessions_pool, primeiro.p AS primeiro_seg
    FROM $DB.sessions AS s FINAL
    INNER JOIN primeiro ON primeiro.session_id = s.session_id
    WHERE s.tenant_id='$T' AND s.opened_at >= '$T0'
      AND s.pool_id != primeiro.p LIMIT 10 FORMAT TSV" | sed 's/^/    /'
  fi
fi

# ── ASSERÇÃO 1b: a asserção 1 PODE reprovar? ─────────────────────────────────
# Sessão que nunca trocou de pool concorda trivialmente, e um gate feito só de
# sessões assim é verde por construção. Aqui se conta quantas das novas têm DOIS
# OU MAIS pools distintos — ou seja, quantas o conserto teve de fato de defender.
echo
echo "── 1b. as sessões novas EXERCITAM a troca de pool? ──"
DISCRIM=$(CH "
WITH pools AS (
  SELECT session_id, uniqExact(pool_id) AS n_pools, argMin(pool_id, started_at) AS p1,
         argMax(pool_id, started_at) AS pN
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != ''
  GROUP BY session_id)
SELECT count()
FROM $DB.sessions AS s FINAL
INNER JOIN pools ON pools.session_id = s.session_id
WHERE s.tenant_id='$T' AND s.opened_at >= '$T0' AND pools.n_pools > 1 FORMAT TSV")
echo "  novas com 2+ pools distintos = ${DISCRIM:-<vazio>}"
CH "
WITH pools AS (
  SELECT session_id, uniqExact(pool_id) AS n_pools,
         argMin(pool_id, started_at) AS p_entrada,
         argMax(pool_id, started_at) AS p_ultimo
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != ''
  GROUP BY session_id)
SELECT s.session_id, s.pool_id AS coluna, pools.p_entrada, pools.p_ultimo, pools.n_pools
FROM $DB.sessions AS s FINAL
INNER JOIN pools ON pools.session_id = s.session_id
WHERE s.tenant_id='$T' AND s.opened_at >= '$T0' FORMAT TSV" | sed 's/^/    /'
if ! [[ "$DISCRIM" =~ ^[0-9]+$ ]] || [ "$DISCRIM" -eq 0 ]; then
  huh "nenhuma sessão nova trocou de pool — a asserção 1 concordou por construção, não por conserto"
else
  ok "$DISCRIM sessão(ões) nova(s) com troca de pool: a asserção 1 tinha como reprovar"
fi

# ── ASSERÇÃO 1c: MESMA FORMA, coluna oposta ──────────────────────────────────
# 1b mostrou que comparar a coluna com `segments` não julga este fluxo: o processo
# entra em `limite_processo`, passa por `aprovacao_credito` no MEIO e volta, então
# argMin == argMax e a concordância seria trivial.
#
# O julgamento verdadeiro é entre sessões da MESMA FORMA (entrada `limite_processo`
# + toca `aprovacao_credito`) executadas antes e depois do deploy: sob a regra
# antiga a coluna virava `aprovacao_credito`, sob a nova fica `limite_processo`.
# Precisam existir os DOIS grupos — só o novo seria "sempre foi assim", e só o
# velho seria "o conserto não pegou".
#
# ⚠️ `status='closed'` NÃO é conveniência: sem ele os dois grupos se entrelaçam no
# tempo e o contraste vira sorte. Medido em 2026-08-14 — uma sessão `suspended` de
# 13/08 aparecia no grupo "novo" um dia ANTES do deploy, porque sessão que nunca
# fecha nunca recebe a linha de `contact_closed`, que era quem trazia o pool
# errado. O bloco 1d mostra o corte inteiro. Fixado o status, o contraste é limpo.
echo
echo "── 1c. mesma forma (entra limite_processo, toca aprovacao_credito): coluna ──"
echo "(coluna / n / mais_antiga / mais_recente)"
CH "
WITH pools AS (
  SELECT session_id, argMin(pool_id, started_at) AS p1, groupUniqArray(pool_id) AS todos
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != ''
  GROUP BY session_id)
SELECT s.pool_id AS coluna, count() AS n,
       min(s.opened_at) AS mais_antiga, max(s.opened_at) AS mais_recente
FROM $DB.sessions AS s FINAL
INNER JOIN pools ON pools.session_id = s.session_id
WHERE s.tenant_id='$T' AND pools.p1 = 'limite_processo'
  AND has(pools.todos, 'aprovacao_credito')
GROUP BY coluna ORDER BY n DESC FORMAT TSV" | sed 's/^/    /'
VELHO=$(CH "
WITH pools AS (
  SELECT session_id, argMin(pool_id, started_at) AS p1, groupUniqArray(pool_id) AS todos
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != '' GROUP BY session_id)
SELECT count() FROM $DB.sessions AS s FINAL
INNER JOIN pools ON pools.session_id = s.session_id
WHERE s.tenant_id='$T' AND pools.p1='limite_processo'
  AND has(pools.todos,'aprovacao_credito') AND s.status='closed' AND s.pool_id='aprovacao_credito' FORMAT TSV")
NOVO=$(CH "
WITH pools AS (
  SELECT session_id, argMin(pool_id, started_at) AS p1, groupUniqArray(pool_id) AS todos
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != '' GROUP BY session_id)
SELECT count() FROM $DB.sessions AS s FINAL
INNER JOIN pools ON pools.session_id = s.session_id
WHERE s.tenant_id='$T' AND pools.p1='limite_processo'
  AND has(pools.todos,'aprovacao_credito') AND s.status='closed' AND s.pool_id='limite_processo' FORMAT TSV")
echo "  regra antiga (coluna=aprovacao_credito)=$VELHO · regra nova (coluna=limite_processo)=$NOVO"
if ! [[ "$VELHO" =~ ^[0-9]+$ ]] || ! [[ "$NOVO" =~ ^[0-9]+$ ]]; then
  huh "uma das duas leituras não devolveu número"
elif [ "$NOVO" -eq 0 ]; then
  bad "nenhuma sessão desta forma carimba a entrada — o conserto não pegou no caminho real"
elif [ "$VELHO" -eq 0 ]; then
  huh "não há sessão da regra ANTIGA para contrastar — o verde não distingue conserto de 'sempre foi assim'"
else
  ok "mesma forma, colunas opostas: $VELHO sob a regra antiga x $NOVO sob a nova"
fi

# ── 1d: por que a MESMA forma dava colunas diferentes ANTES do conserto ──────
# Medido em 2026-08-14: o grupo `limite_processo` tinha uma sessão de 13/08, um dia
# ANTES do deploy — as datas se entrelaçam, e sem explicação isso significaria que a
# regra antiga já era não-determinística e que o contraste do 1c é sorte.
# A hipótese que o corte abaixo testa: sob a regra antiga a coluna era o ÚLTIMO
# escritor, então sessão que NUNCA FECHOU nunca recebeu a linha de close (que é a
# que trazia `aprovacao_credito`) e ficou com o pool do próprio workflow. Se for
# isso, `status`/`closed_at` separam os dois grupos e nada é sorte.
echo
echo "── 1d. o mesmo grupo, cortado por status (explica o entrelaçamento?) ──"
echo "(coluna / status / aberta / n / mais_antiga / mais_recente)"
CH "
WITH pools AS (
  SELECT session_id, argMin(pool_id, started_at) AS p1, groupUniqArray(pool_id) AS todos
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != '' GROUP BY session_id)
SELECT s.pool_id AS coluna, coalesce(s.status,'?') AS status,
       s.closed_at IS NULL AS aberta, count() AS n,
       min(s.opened_at) AS mais_antiga, max(s.opened_at) AS mais_recente
FROM $DB.sessions AS s FINAL
INNER JOIN pools ON pools.session_id = s.session_id
WHERE s.tenant_id='$T' AND pools.p1='limite_processo'
  AND has(pools.todos,'aprovacao_credito')
GROUP BY coluna, status, aberta ORDER BY coluna, n DESC FORMAT TSV" | sed 's/^/    /'

# ── ASSERÇÃO 2: o passado NÃO muda (é o comportamento declarado) ─────────────
echo
echo "── 2. sessão HISTÓRICA divergente: a divergência PERMANECE ──"
HIST_DEPOIS=$(CH "
WITH primeiro AS (
  SELECT session_id, argMin(pool_id, started_at) AS p
  FROM $DB.segments FINAL
  WHERE tenant_id='$T' AND pool_id != ''
  GROUP BY session_id)
SELECT count()
FROM $DB.sessions AS s FINAL
INNER JOIN primeiro ON primeiro.session_id = s.session_id
WHERE s.tenant_id='$T' AND s.pool_id != primeiro.p
  AND s.opened_at < '$T0' FORMAT TSV")
echo "  antes=$HIST_ANTES · depois=$HIST_DEPOIS"
if [ "$HIST_ANTES" = "$HIST_DEPOIS" ]; then
  ok "forward-only confirmado: o passado ficou onde estava ($HIST_DEPOIS)"
elif [ "${HIST_DEPOIS:-x}" -lt "$HIST_ANTES" ] 2>/dev/null; then
  bad "o passado ENCOLHEU ($HIST_ANTES→$HIST_DEPOIS): alguma coisa reescreveu substrato histórico"
else
  bad "o passado CRESCEU ($HIST_ANTES→$HIST_DEPOIS): sessão antiga sendo re-carimbada errado"
fi

# ── ASSERÇÃO 3: o relatório de fila atribui ao pool da FILA ──────────────────
echo
echo "── 3. fila: nenhuma espera atribuída a pool que não é o da fila ──"
FILA_COM=$(CH "
SELECT uniqExact(session_id) FROM $DB.segments FINAL
WHERE tenant_id='$T' AND role='queue' AND pool_id!='' FORMAT TSV")
if ! [[ "$FILA_COM" =~ ^[0-9]+$ ]] || [ "$FILA_COM" -eq 0 ]; then
  huh "nenhuma sessão com segmento de fila no tenant — a inversão de precedência não é exercida"
else
  ok "há $FILA_COM sessões com segmento de fila (a inversão tem população)"
fi

# ── ASSERÇÃO 4: a inversão MUDA número, e muda na direção certa ──────────────
# Diferencial puro: as duas precedências lado a lado, na mesma query. Não depende
# de rebuild nem de stash — é a expressão ANTIGA e a NOVA computadas juntas, o que
# torna impossível o falso-verde de "duas leituras quebradas iguais entre si".
echo
echo "── 4. precedência ANTIGA x NOVA, lado a lado ──"
echo "(antiga=sessions-primeiro / nova=fila-primeiro / n) — linhas onde discordam"
CH "
WITH segs AS (
  SELECT session_id, anyIf(pool_id, role='queue') AS q_pool
  FROM $DB.segments FINAL WHERE tenant_id='$T' GROUP BY session_id)
SELECT if(ss.pool_id != '', ss.pool_id, segs.q_pool) AS antiga,
       if(segs.q_pool != '', segs.q_pool, ss.pool_id) AS nova,
       count() AS n
FROM (SELECT session_id, pool_id FROM $DB.sessions FINAL
      WHERE tenant_id='$T' AND coalesce(outcome,'') != 'outage') ss
INNER JOIN segs ON ss.session_id = segs.session_id
WHERE antiga != nova
GROUP BY antiga, nova ORDER BY n DESC FORMAT TSV" | sed 's/^/    /'
DIFF_N=$(CH "
WITH segs AS (
  SELECT session_id, anyIf(pool_id, role='queue') AS q_pool
  FROM $DB.segments FINAL WHERE tenant_id='$T' GROUP BY session_id)
SELECT countIf(if(ss.pool_id != '', ss.pool_id, segs.q_pool)
            != if(segs.q_pool != '', segs.q_pool, ss.pool_id))
FROM (SELECT session_id, pool_id FROM $DB.sessions FINAL
      WHERE tenant_id='$T' AND coalesce(outcome,'') != 'outage') ss
INNER JOIN segs ON ss.session_id = segs.session_id FORMAT TSV")
echo "  sessões que mudam de atribuição = ${DIFF_N:-<vazio>}"
if ! [[ "$DIFF_N" =~ ^[0-9]+$ ]]; then
  huh "a leitura diferencial não devolveu número"
elif [ "$DIFF_N" -eq 0 ]; then
  huh "as duas precedências dão o MESMO resultado — a inversão não é observável neste ambiente"
else
  ok "$DIFF_N sessões trocam de pool no relatório de fila (a inversão é observável)"
fi

# ── ASSERÇÃO 5: o escopo ABAC AMPLIA, e não estreita ─────────────────────────
# O carimbo de entrada, sozinho, faria o supervisor perder contato que os agentes
# DELE atenderam. O predicado novo (`_session_scope_clause`) une entrada + ausência
# + participação. Aqui as duas versões correm lado a lado sobre o escopo REAL do
# `operator@plughub.local` — nada de escopo inventado, que é o que faria a medição
# concordar consigo mesma.
echo
echo "── 5. ABAC: predicado ANTIGO x NOVO sobre o escopo real do operator ──"
P="'formfill_demo','retencao_humano','aprovacao_credito'"
ABAC=$(CH "
SELECT
  countIf(velho)                       AS so_velho_total,
  countIf(novo)                        AS novo_total,
  countIf(novo AND NOT velho)          AS ganhas,
  countIf(velho AND NOT novo)          AS perdidas
FROM (
  SELECT (s.pool_id IN ($P) OR s.pool_id='') AS velho,
         (s.pool_id IN ($P) OR s.pool_id='' OR s.session_id IN (
            SELECT session_id FROM $DB.segments FINAL
            WHERE tenant_id='$T' AND pool_id IN ($P))) AS novo
  FROM $DB.sessions AS s FINAL WHERE s.tenant_id='$T')
FORMAT TSV")
echo "  (velho / novo / ganhas / perdidas) = ${ABAC:-<vazio>}"
PERDIDAS=$(echo "$ABAC" | awk '{print $4}')
GANHAS=$(echo "$ABAC" | awk '{print $3}')
if ! [[ "${PERDIDAS:-x}" =~ ^[0-9]+$ ]]; then
  huh "a leitura do escopo não devolveu número"
elif [ "$PERDIDAS" -ne 0 ]; then
  bad "$PERDIDAS sessões SAEM do escopo — o predicado novo estreita, e não podia"
elif [ "${GANHAS:-0}" -eq 0 ]; then
  huh "o predicado novo não acrescenta nada — a participação por segmento não é observável neste escopo"
else
  ok "amplia sem estreitar: +$GANHAS sessões, -0"
fi

echo
echo "PASS=$PASS FAIL=$FAIL INCONCLUSIVO=$INCONC"
[ "$FAIL" -gt 0 ] && exit 1
[ "$INCONC" -gt 0 ] && exit 2
exit 0
