#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# gate_pool_config_ttl_source.sh — o TTL de `{t}:pool_config:{p}` obedece ao
# Config API, e obedece nos DOIS escritores.
#
# ── A proposição, e por que os unit tests não a alcançam ─────────────────────
# `test_pool_config_ttl_single_source.py` (bridge e routing-engine) prova que o
# `ex=` passado ao Redis vem do cache de config. Não prova que o VALOR DA TELA
# chega até lá: entre a config e o Redis há Kafka (`config.changed`), o reload
# HTTP e o heartbeat de 15 s, e um mock não atravessa nada disso.
#
# A proposição aqui é a ponta a ponta:
#
#     mudar `session.pool_config_ttl_s` no Config API muda o TTL da chave no
#     Redis, sem restart de serviço nenhum.
#
# ── Por que o valor é 4242, e não 86 400 ────────────────────────────────────
# ⚠️ Um gate que usasse 86 400 — o valor que a config JÁ TEM depois desta fatia —
# passaria idêntico sobre o código velho no dia em que alguém trocasse só a
# constante hardcoded. O 4242 não é produzido por default nenhum do repositório
# (nem o 3 600 antigo, nem o 86 400 novo), então só há uma explicação para ele
# aparecer no Redis: veio da config. É a mesma lição do `discord = 0` no gate da
# D14-iii — um teste de concordância só julga se contiver o caso discriminante.
#
# ── O que este gate teria pego, e ninguém pegou por meses ───────────────────
# Três causas empilhadas mantinham o namespace `session` inteiro inerte no
# bridge (medido 2026-08-25): `CONFIG_API_URL` ausente do compose, default
# hardcoded apontando para a porta da analytics-api (3500), e GET sem
# `?tenant_id=` (422). Cada uma sozinha bastava, e as três degradavam para "usa
# o default", que quase sempre parece certo.
#
# ⚠️ Este gate ESCREVE na config do tenant real e restaura no fim (trap EXIT).
# Se abortar no meio, rode de novo — ele é idempotente e sempre restaura.
#
# Uso:  bash infra/test/gate_pool_config_ttl_source.sh
# Env:  CONFIG_API · CONFIG_TOKEN · DC · TENANT · POOL
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

CONFIG_API="${CONFIG_API:-http://localhost:3600}"
CONFIG_TOKEN="${CONFIG_TOKEN:-demo_config_admin_token}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
TENANT="${TENANT:-tenant_demo}"
POOL="${POOL:-retencao_humano}"

PROBE_TTL=4242          # não é default de ninguém — ver cabeçalho
FINAL_TTL=86400         # valor de produção (24h), restaurado no fim

FAIL=0
note() { printf '%s\n' "$*"; }
bad()  { printf '🔴 %s\n' "$*"; FAIL=1; }
ok()   { printf '🟢 %s\n' "$*"; }
inc()  { printf '🟡 INCONCLUSIVO — %s\n' "$*"; exit 2; }

# TTL decai desde a escrita, então "é este valor" é sempre uma JANELA. 120s
# cobre a batida do heartbeat (15s) com folga larga.
near() { [ "$1" -le "$2" ] && [ "$1" -gt $(( $2 - 120 )) ]; }

put_ttl() {
  curl -s -o /dev/null -w '%{http_code}' -X PUT \
    -H "X-Admin-Token: ${CONFIG_TOKEN}" -H 'Content-Type: application/json' \
    "${CONFIG_API}/config/session/pool_config_ttl_s" \
    -d "{\"tenant_id\": null, \"value\": $1, \"description\": \"TTL de {t}:pool_config:{p} — fonte única (bridge + routing-engine)\"}"
}

get_ttl_config() {
  curl -s "${CONFIG_API}/config/session?tenant_id=${TENANT}" \
    | python3 -c 'import json,sys; e=json.load(sys.stdin).get("entries",{}); v=e.get("pool_config_ttl_s"); print(v.get("value") if isinstance(v,dict) else v)'
}

get_ttl_redis() {
  ${DC} exec -T redis redis-cli TTL "${TENANT}:pool_config:${POOL}" | tr -d '\r'
}

restore() {
  note "-- restaurando pool_config_ttl_s = ${FINAL_TTL}"
  put_ttl "${FINAL_TTL}" >/dev/null
}
trap restore EXIT

# ── 0. Testemunha de presença ────────────────────────────────────────────────
# Um contador de ausência precisa do contador de presença ao lado: sem a chave
# no Redis, TODA asserção abaixo passaria por vacuidade.
BEFORE=$(get_ttl_redis)
case "${BEFORE}" in
  ''|*[!0-9-]*) inc "TTL do Redis ilegível ('${BEFORE}') — o stack está de pé?" ;;
esac
[ "${BEFORE}" = "-2" ] && inc "a chave ${TENANT}:pool_config:${POOL} NÃO EXISTE — sem população, o gate não julga nada"
[ "${BEFORE}" = "-1" ] && inc "a chave existe SEM TTL — estado que nenhum escritor produz; investigar antes de julgar"
ok "testemunha de presença: chave viva, TTL=${BEFORE}s"

# ── 1. A config responde ─────────────────────────────────────────────────────
CFG0=$(get_ttl_config)
case "${CFG0}" in
  ''|None|null) inc "Config API não devolveu pool_config_ttl_s — namespace vazio ou API fora" ;;
esac
note "-- valor corrente na config: ${CFG0}"

# ── 2. Veredicto: a config MANDA no TTL ──────────────────────────────────────
note "== veredicto 1: mudar a config muda o TTL no Redis, sem restart"
CODE=$(put_ttl "${PROBE_TTL}")
[ "${CODE}" = "200" ] || inc "PUT devolveu HTTP ${CODE} (token? namespace?)"

# ⚠️ Conferir que a CONFIG mudou antes de olhar o Redis separa duas falhas de
# aparência idêntica: "o PUT não venceu" (override por tenant sobrepondo o
# global que este gate escreve) × "o escritor ignorou a config". Sem esta
# conferência, a primeira seria reportada como a segunda.
CFG_PROBE=$(get_ttl_config)
[ "${CFG_PROBE}" = "${PROBE_TTL}" ] || inc \
  "o valor resolvido para ${TENANT} ficou em ${CFG_PROBE}, não ${PROBE_TTL} — \
provável override POR TENANT sobrepondo o default global. O gate escreve global \
de propósito (é onde o seed escreve); ajuste o override antes de julgar o TTL."

# O caminho é config.changed → invalidate → reload → heartbeat (15 s).
# 40 s cobre duas batidas do heartbeat com folga; menos que isso mediria a
# latência do pipeline em vez de medir o valor.
note "-- aguardando 40s (config.changed → reload → heartbeat de 15s)"
sleep 40

AFTER=$(get_ttl_redis)
note "-- TTL após a mudança: ${AFTER}"

if near "${AFTER}" "${PROBE_TTL}"; then
  ok "o TTL seguiu a config: ${AFTER} ≈ ${PROBE_TTL}"
elif near "${AFTER}" 3600 || near "${AFTER}" 86400; then
  # ⚠️ Este ramo existe porque o anterior atribuía a causa ERRADA. Um TTL
  # parado num DEFAULT CONHECIDO não é "chave não renovada" — é renovação
  # acontecendo com o número errado, que é o defeito original desta fatia
  # voltando. Distinguir importa: as duas hipóteses mandam procurar em
  # lugares opostos (código × processo parado).
  bad "TTL=${AFTER} está num DEFAULT CONHECIDO, não no valor da config."
  bad "   O escritor renova, mas IGNORA a config. Suspeitos, em ordem:"
  bad "   (a) constante hardcoded de volta em instance_bootstrap;"
  bad "   (b) CONFIG_API_URL ausente/errado no compose do bridge;"
  bad "   (c) reload sem ?tenant_id= (422 → cache preso nos _DEFAULTS)."
  bad "   ${DC} logs orchestrator-bridge | grep SessionConfigCache | tail -3"
elif [ "${AFTER}" -gt "${PROBE_TTL}" ]; then
  bad "TTL=${AFTER} > ${PROBE_TTL} e fora de qualquer default conhecido —"
  bad "   alguém escreveu a chave com um terceiro valor. Procure um ESCRITOR"
  bad "   novo: o invariante desta fatia é que só existem dois."
else
  bad "TTL=${AFTER} abaixo de ${PROBE_TTL} e fora de qualquer default —"
  bad "   chave não renovada. O heartbeat do bridge parou? (com ele parado o"
  bad "   TTL decai e não reseta — foi assim que se provou o renovador único)"
fi

# ── 3. Veredicto: a restauração também vale ──────────────────────────────────
# Não é simetria decorativa: prova que o caminho não é de mão única (um valor
# que sobe e não desce indicaria escrita só no ramo de criação, não no de
# renovação).
note "== veredicto 2: o caminho de volta também obedece"
put_ttl "${FINAL_TTL}" >/dev/null
sleep 40
FINAL=$(get_ttl_redis)
note "-- TTL após restaurar: ${FINAL}"
if near "${FINAL}" "${FINAL_TTL}"; then
  ok "voltou ao valor de produção: ${FINAL} ≈ ${FINAL_TTL}"
else
  bad "TTL=${FINAL}, esperado ≈ ${FINAL_TTL} — restauração não pegou"
fi

# ── 4. A config ficou no valor de produção ───────────────────────────────────
CFG1=$(get_ttl_config)
[ "${CFG1}" = "${FINAL_TTL}" ] \
  && ok "config restaurada em ${CFG1}" \
  || bad "config ficou em ${CFG1}, esperado ${FINAL_TTL} — RESTAURE À MÃO"

exit "${FAIL}"
