#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# gate_sla_segment_target.sh — D14 (iii): cada espera é julgada contra o alvo
# DA PRÓPRIA espera.
#
# ── A proposição, e por que o unit test não a alcança ────────────────────────
# `test_sla_reads_the_segment.py` prova que os três leitores APONTAM para o
# segmento. Não prova que o número sai certo: a agregação acontece dentro do
# ClickHouse, e um mock devolve o que se mandou — asserir sobre ele seria medir
# a fixture.
#
# A proposição aqui é a que a D14 existe para defender:
#
#     um contato que espera em DUAS filas com alvos DIFERENTES produz DUAS
#     linhas de veredicto, uma cumprindo e outra violando.
#
# Sobre o código anterior o mesmo dado dava DUAS linhas com o MESMO alvo (o da
# sessão), então uma das duas era julgada contra o alvo da outra.
#
# ⚠️ ESTA POPULAÇÃO NÃO EXISTE NO AMBIENTE. Medido em 2026-08-25
# (`q_sla_source_delta.py`): `discord = 0` — nenhuma espera do tenant tem os
# dois alvos presentes e diferentes. Um teste de "as duas fontes concordam"
# passaria idêntico sobre o código VELHO. É a mesma lição do `0` no
# `test_sla_target_predicate`: um teste de concordância só julga se a população
# contiver o caso em que elas DISCORDAM. Por isso o gate INSERE o caso.
#
# ── Tenant próprio, e isso não é higiene ────────────────────────────────────
# Escreve num `tenant_id` sintético (`t_gate_sla`), nunca no `tenant_demo`. Duas
# razões, e a segunda é a que importa: (1) não contamina medição real; (2) o
# `sessions.sla_target_ms` das linhas sintéticas é escrito de PROPÓSITO com um
# valor que NÃO é o de nenhum dos dois segmentos — se algum leitor voltar à
# sessão, o número sai errado de um jeito reconhecível, em vez de coincidir por
# acaso com o segmento.
#
# Uso:  bash infra/test/gate_sla_segment_target.sh
# Env:  CH · CH_USER · CH_PASS · DB · ANALYTICS (http://localhost:3500)
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

CH="${CH:-http://localhost:8123}"
CH_USER="${CH_USER:-plughub}"
CH_PASS="${CH_PASS:-plughub}"
DB="${DB:-plughub_demo}"
T="${GATE_TENANT:-t_gate_sla}"

# Época do corte (b). Tem de casar com `sla_source.SEGMENT_SLA_EPOCH`; as linhas
# sintéticas nascem DEPOIS dela, senão o corte as descarta e o gate mede o corte
# em vez de medir o alvo.
EPOCH_DAY="2026-08-26"

FAIL=0
note() { printf '%s\n' "$*"; }
bad()  { printf '🔴 %s\n' "$*"; FAIL=1; }
ok()   { printf '🟢 %s\n' "$*"; }

q() {
  curl -s --fail-with-body \
    -H "X-ClickHouse-User: ${CH_USER}" -H "X-ClickHouse-Key: ${CH_PASS}" \
    "${CH}" --data-binary "$1"
}

note "== limpando execução anterior (idempotente)"
q "ALTER TABLE ${DB}.segments DELETE WHERE tenant_id = '${T}'" >/dev/null
q "ALTER TABLE ${DB}.sessions DELETE WHERE tenant_id = '${T}'" >/dev/null
sleep 2

# ── A população discriminante ────────────────────────────────────────────────
#
#   sessão S1, DUAS esperas:
#     espera A — pool_a, 30 000 ms contra alvo de 300 000  ⇒ DENTRO
#     espera B — pool_b, 90 000 ms contra alvo de  60 000  ⇒ FORA
#
#   `sessions.sla_target_ms` = 999 999 999 — valor que não é o de nenhuma das
#   duas. Se um leitor voltar à sessão, AS DUAS passam a cumprir, e o gate
#   reprova apontando o número.
note "== semeando: 1 sessão, 2 esperas, alvos diferentes (tenant ${T})"
# `date` é coluna de PARTIÇÃO e NÃO tem DEFAULT no DDL — omiti-la faria a linha
# nascer em 1970-01 e o relatório continuaria achando (filtra por opened_at),
# mas a partição sintética ficaria pendurada num lugar improvável de limpar.
q "INSERT INTO ${DB}.sessions
   (tenant_id, session_id, pool_id, opened_at, closed_at, wait_time_ms,
    sla_target_ms, outcome, origin, date)
   VALUES
   ('${T}', 's_two_waits', 'pool_a', '${EPOCH_DAY} 10:00:00',
    '${EPOCH_DAY} 10:10:00', 30000, 999999999, 'resolved', 'live',
    '${EPOCH_DAY}')" >/dev/null || {
  bad "INSERT em sessions falhou — colunas divergem do DDL? INCONCLUSIVO"
  exit 1
}

# O segmento `primary` com `agent_type='human'` NÃO é enfeite: `_handoff` exige
# `primary_count > 0` com `agent_type != 'system'`. Sem ele a espera contaria
# como não-atendida e o gate mediria outra coisa.
q "INSERT INTO ${DB}.segments
   (tenant_id, session_id, segment_id, participant_id, instance_id,
    agent_type_id, pool_id, role, agent_type, sequence_index,
    started_at, ended_at, duration_ms, outcome, sla_target_ms, date)
   VALUES
   ('${T}', 's_two_waits', 'seg_wait_a', 'p_a', 'i_a', 'at_a', 'pool_a',
    'queue', 'system', 0, '${EPOCH_DAY} 10:00:00', '${EPOCH_DAY} 10:00:30',
    30000, 'answered', 300000, '${EPOCH_DAY}'),
   ('${T}', 's_two_waits', 'seg_wait_b', 'p_b', 'i_b', 'at_b', 'pool_b',
    'queue', 'system', 1, '${EPOCH_DAY} 10:05:00', '${EPOCH_DAY} 10:06:30',
    90000, 'answered', 60000, '${EPOCH_DAY}'),
   ('${T}', 's_two_waits', 'seg_primary', 'p_h', 'i_h', 'at_h', 'pool_b',
    'primary', 'human', 2, '${EPOCH_DAY} 10:06:30', '${EPOCH_DAY} 10:10:00',
    210000, 'resolved', NULL, '${EPOCH_DAY}')" >/dev/null || {
  bad "INSERT em segments falhou — a coluna sla_target_ms existe? INCONCLUSIVO"
  exit 1
}
sleep 1

# ── TESTEMUNHA DE PRESENÇA, antes de qualquer veredicto ──────────────────────
# Um contador de ausência sem testemunha de presença ao lado não distingue
# "o defeito não existe" de "o instrumento não mediu nada".
SEEDED=$(q "SELECT count() FROM ${DB}.segments FINAL
            WHERE tenant_id = '${T}' AND role = 'queue'" | tr -d '[:space:]')
note "== testemunha: esperas semeadas visíveis no CH = ${SEEDED}"
if [ "${SEEDED}" != "2" ]; then
  bad "esperava 2 esperas semeadas, vi '${SEEDED}' — INCONCLUSIVO, nenhum ramo abaixo vale"
  exit 1
fi

# ── VEREDICTO 1 — o alvo vem do PRÓPRIO segmento ─────────────────────────────
note ""
note "== veredicto 1: cada espera carrega o alvo do seu pool"
ROWS=$(q "SELECT concat(pool_id, '|', toString(duration_ms), '|',
                        toString(coalesce(sla_target_ms, -1)), '|',
                        toString(duration_ms <= sla_target_ms))
          FROM ${DB}.segments FINAL
          WHERE tenant_id = '${T}' AND role = 'queue'
          ORDER BY pool_id")
note "${ROWS}"
# Delimitador NÃO-BRANCO no concat: tab é IFS whitespace e o campo vazio seria
# dobrado, fazendo o campo seguinte escorregar de coluna.
echo "${ROWS}" | grep -q "^pool_a|30000|300000|1$" \
  && ok "pool_a: 30 s contra alvo de 300 s ⇒ DENTRO" \
  || bad "pool_a não carrega o próprio alvo"
echo "${ROWS}" | grep -q "^pool_b|90000|60000|0$" \
  && ok "pool_b: 90 s contra alvo de 60 s ⇒ FORA" \
  || bad "pool_b não carrega o próprio alvo"

# ── VEREDICTO 2 — o relatório dá DOIS veredictos, não um ─────────────────────
#
# É aqui que o código velho reprovava: com `ss.sla_target_ms`, as duas linhas
# levavam 999 999 999 e AS DUAS cumpriam ⇒ within=2, attainment=1.0.
note ""
note "== veredicto 2: o relatório Fila/SLA (esperado 1 dentro, 1 fora)"
REPORT=$(curl -s "${ANALYTICS:-http://localhost:3500}/reports/pools/queue?tenant_id=${T}&from_dt=${EPOCH_DAY}%2000:00:00&to_dt=${EPOCH_DAY}%2023:59:59" \
         )
if [ -z "${REPORT}" ]; then
  bad "analytics-api não respondeu — INCONCLUSIVO (não é evidência de ausência de defeito)"
else
  note "${REPORT}" | head -c 1200; note ""
  # `// empty` NÃO é usado aqui: ele trata `false` e `0` como ausentes, e `0`
  # é justamente um dos valores que o gate precisa distinguir de "não veio".
  A_IN=$(echo "${REPORT}" | jq -r '.data.by_pool[]|select(.pool_id=="pool_a")|.within_sla')
  A_EL=$(echo "${REPORT}" | jq -r '.data.by_pool[]|select(.pool_id=="pool_a")|.sla_eligible')
  B_IN=$(echo "${REPORT}" | jq -r '.data.by_pool[]|select(.pool_id=="pool_b")|.within_sla')
  B_EL=$(echo "${REPORT}" | jq -r '.data.by_pool[]|select(.pool_id=="pool_b")|.sla_eligible')
  note "   pool_a within/elig = ${A_IN}/${A_EL}   ·   pool_b within/elig = ${B_IN}/${B_EL}"
  [ "${A_IN}" = "1" ] && [ "${A_EL}" = "1" ] \
    && ok "pool_a: 1 elegível, 1 dentro" \
    || bad "pool_a saiu ${A_IN}/${A_EL}, esperado 1/1"
  [ "${B_IN}" = "0" ] && [ "${B_EL}" = "1" ] \
    && ok "pool_b: 1 elegível, 0 dentro — a violação da SEGUNDA fila apareceu" \
    || bad "pool_b saiu ${B_IN}/${B_EL}, esperado 0/1 (com o alvo da SESSÃO daria 1/1)"
fi

# ── VEREDICTO 3 — espera pós-época SEM alvo é contada, não escondida ─────────
note ""
note "== veredicto 3: espera sem alvo depois da época vira NÚMERO (sla_unstamped)"
q "INSERT INTO ${DB}.segments
   (tenant_id, session_id, segment_id, participant_id, instance_id,
    agent_type_id, pool_id, role, agent_type, sequence_index,
    started_at, ended_at, duration_ms, outcome, sla_target_ms, date)
   VALUES
   ('${T}', 's_no_target', 'seg_wait_c', 'p_c', 'i_c', 'at_c', 'pool_c',
    'queue', 'system', 0, '${EPOCH_DAY} 11:00:00', '${EPOCH_DAY} 11:00:20',
    20000, 'answered', NULL, '${EPOCH_DAY}'),
   ('${T}', 's_no_target', 'seg_prim_c', 'p_hc', 'i_hc', 'at_hc', 'pool_c',
    'primary', 'human', 1, '${EPOCH_DAY} 11:00:20', '${EPOCH_DAY} 11:05:00',
    280000, 'resolved', NULL, '${EPOCH_DAY}')" >/dev/null
q "INSERT INTO ${DB}.sessions
   (tenant_id, session_id, pool_id, opened_at, closed_at, wait_time_ms,
    sla_target_ms, outcome, origin, date)
   VALUES
   ('${T}', 's_no_target', 'pool_c', '${EPOCH_DAY} 11:00:00',
    '${EPOCH_DAY} 11:05:00', 20000, 999999999, 'resolved', 'live',
    '${EPOCH_DAY}')" >/dev/null
sleep 1
REPORT2=$(curl -s "${ANALYTICS:-http://localhost:3500}/reports/pools/queue?tenant_id=${T}&from_dt=${EPOCH_DAY}%2000:00:00&to_dt=${EPOCH_DAY}%2023:59:59" \
          )
C_UN=$(echo "${REPORT2}" | jq -r '.data.by_pool[]|select(.pool_id=="pool_c")|.sla_unstamped')
C_EL=$(echo "${REPORT2}" | jq -r '.data.by_pool[]|select(.pool_id=="pool_c")|.sla_eligible')
C_AT=$(echo "${REPORT2}" | jq -r '.data.by_pool[]|select(.pool_id=="pool_c")|.sla_attainment')
note "   pool_c unstamped=${C_UN} elegíveis=${C_EL} aderência=${C_AT}"
[ "${C_UN}" = "1" ] \
  && ok "a espera sem alvo foi CONTADA — o buraco do TTL de pool_config é visível" \
  || bad "sla_unstamped saiu '${C_UN}', esperado 1"
[ "${C_EL}" = "0" ] && [ "${C_AT}" = "null" ] \
  && ok "e ficou FORA do denominador, com aderência AUSENTE (não zero, não 100%)" \
  || bad "pool_c entrou no cálculo: elegíveis=${C_EL} aderência=${C_AT}"

# ── limpeza ──────────────────────────────────────────────────────────────────
note ""
note "== limpando"
q "ALTER TABLE ${DB}.segments DELETE WHERE tenant_id = '${T}'" >/dev/null
q "ALTER TABLE ${DB}.sessions DELETE WHERE tenant_id = '${T}'" >/dev/null

note ""
if [ "${FAIL}" = "0" ]; then
  ok "GATE VERDE — cada espera é julgada contra o alvo da própria espera"
  exit 0
fi
bad "GATE VERMELHO"
exit 1
