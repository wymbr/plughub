#!/usr/bin/env bash
# probe_pause_capacity_baseline.sh — 2026-08-21
#   TODO § "A capacidade de pool não conhece PAUSA"
#
# PERGUNTA (uma só, e ANTES de tocar em código): a pausa de um agente aparece
# em algum lugar da contabilidade de capacidade do pool?
#
# Este probe é READ-ONLY. Não pausa ninguém, não escreve chave nenhuma. Ele
# fotografa o estado que o operador deixou na tela e responde três coisas:
#
#   (A) o INSTRUMENTO funciona?  — o routing-engine imprime INFO neste ambiente?
#       Sem isso, "não achei o log de deactivate" não é evidência de nada, é
#       ausência de instrumento. Preflight antes de comparar.
#   (B) o GANCHO rodou?          — quantas linhas `[deactivate]` existem no log,
#       e quantos `agent_pause` foram publicados. Duas contagens, lado a lado:
#       um contador de AUSÊNCIA precisa da testemunha de presença ao lado.
#   (C) a ARITMÉTICA mente?      — para cada membro de ready_set ∪ busy_set do
#       pool: status da instância, max_concurrent, ocupação real (SCARD do
#       semáforo) — e o `available` que o snapshot publica.
#
# O veredicto do (C) NÃO é verde/vermelho: é a CONTA feita à mão ao lado da
# conta publicada, para que a divergência (se houver) tenha nome.
#
# USO:   bash infra/test/probe_pause_capacity_baseline.sh [pool_id]
# SAÍDA: 0 sempre que conseguiu medir · 2 = INCONCLUSIVO (redis mudo).

set -u

TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
POOL="${1:-${POOL:-retencao_humano}}"
LOG_TAIL="${LOG_TAIL:-20000}"

DC="docker compose -f $COMPOSE"
r() { $DC exec -T redis redis-cli "$@" < /dev/null; }

echo "== probe: a pausa entra na contabilidade de capacidade? =="
echo "   tenant=$TENANT  pool=$POOL  compose=$COMPOSE"
echo

# ── Preflight: o leitor lê ───────────────────────────────────────────────────
PING="$(r PING 2>/dev/null)"
if [ "$PING" != "PONG" ]; then
  echo "PREFLIGHT FALHOU: redis-cli não respondeu PING (obtido: '$PING')."
  echo "VEREDICTO: INCONCLUSIVO — o leitor não lê; nada abaixo vale."
  exit 2
fi

# ── (A) O INSTRUMENTO: o routing-engine imprime INFO neste ambiente? ─────────
echo "── (A) instrumento: nível de log do routing-engine ──"
RE_LOG="$($DC logs routing-engine --tail "$LOG_TAIL" 2>/dev/null)"
RE_LINES="$(printf '%s\n' "$RE_LOG" | wc -l | tr -d ' ')"
RE_INFO="$(printf '%s\n' "$RE_LOG" | grep -c 'INFO')"
echo "   linhas na janela .......... $RE_LINES"
echo "   linhas com INFO ........... $RE_INFO"
if [ "$RE_INFO" -eq 0 ]; then
  echo "   ⚠ INFO NÃO aparece — a seção (B) abaixo é INCONCLUSIVA por ausência de"
  echo "     instrumento, não por ausência do comportamento."
fi
echo

# ── (B) O GANCHO: publicado × consumido ──────────────────────────────────────
echo "── (B) gancho da pausa: publicado × consumido ──"
MCP_LOG="$($DC logs mcp-server-plughub --tail "$LOG_TAIL" 2>/dev/null)"
N_PAUSE_EP="$(printf '%s\n' "$MCP_LOG" | grep -c 'agent-pause')"
N_DEACT="$(printf '%s\n' "$RE_LOG" | grep -c '\[deactivate\]')"
N_DEACT_P="$(printf '%s\n' "$RE_LOG" | grep '\[deactivate\]' | grep -c 'state=paused')"
N_DEACT_L="$(printf '%s\n' "$RE_LOG" | grep '\[deactivate\]' | grep -c 'state=logged_out')"
N_READY_REFRESH="$(printf '%s\n' "$RE_LOG" | grep -c 'Pool snapshot')"
echo "   mcp-server: linhas citando 'agent-pause' ......... $N_PAUSE_EP"
echo "   routing-engine: '[deactivate]' (total) ........... $N_DEACT"
echo "                   …dessas, state=paused ............ $N_DEACT_P   ← só a PAUSA produz esta"
echo "                   …dessas, state=logged_out ........ $N_DEACT_L   ← testemunha: o MESMO"
echo "                                                              handler, pelo outro evento"
echo "   routing-engine: linhas 'Pool snapshot' ........... $N_READY_REFRESH   ← log vivo?"
echo "   (as duas últimas existem para que 'paused=0' signifique algo: se ambas"
echo "    forem 0, o log está mudo e nada se conclui do zero de cima.)"
echo

# ── (C) A ARITMÉTICA: conta à mão × conta publicada ──────────────────────────
echo "── (C) aritmética do pool $POOL ──"
READY="$(r SMEMBERS "${TENANT}:pool:${POOL}:instances" | tr -d '\r')"
BUSY="$(r SMEMBERS "${TENANT}:pool:${POOL}:busy_instances" | tr -d '\r')"
echo "   ready_set : $(printf '%s ' $READY)"
echo "   busy_set  : $(printf '%s ' $BUSY)"
echo

MEMBERS="$(printf '%s\n%s\n' "$READY" "$BUSY" | grep -v '^$' | sort -u)"
if [ -z "$MEMBERS" ]; then
  echo "   (nenhum membro — pool vazio; a conta abaixo é trivialmente 0)"
fi

TOTAL_ALL=0     # soma de max_concurrent sobre TODOS os membros (modelo de hoje)
TOTAL_ACTIVE=0  # soma de max_concurrent só sobre membros ATIVOS (modelo correto)
USED=0
PAUSED_CAP=0
printf '   %-28s %-10s %5s %5s\n' INSTANCE STATUS MAXC USED
for iid in $MEMBERS; do
  RAW="$(r GET "${TENANT}:instance:${iid}" | tr -d '\r')"
  if [ -z "$RAW" ]; then
    printf '   %-28s %-10s %5s %5s   ← chave AUSENTE (conta capacidade por default)\n' \
      "$iid" "(sem chave)" "?" "?"
    continue
  fi
  ST="$(printf '%s' "$RAW" | sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  [ -z "$ST" ] && ST="$(printf '%s' "$RAW" | sed -n 's/.*"state"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  MC="$(printf '%s' "$RAW" | sed -n 's/.*"max_concurrent"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')"
  [ -z "$MC" ] && MC=1
  N="$(r SCARD "${TENANT}:instance:${iid}:sessions" | tr -d '\r')"
  [ -z "$N" ] && N=0
  printf '   %-28s %-10s %5s %5s\n' "$iid" "$ST" "$MC" "$N"
  TOTAL_ALL=$(( TOTAL_ALL + MC ))
  USED=$(( USED + N ))
  case "$ST" in
    paused|logged_out|logout|draining) PAUSED_CAP=$(( PAUSED_CAP + MC )) ;;
    *)                                 TOTAL_ACTIVE=$(( TOTAL_ACTIVE + MC )) ;;
  esac
done
echo
echo "   Σ max_concurrent (TODOS os membros) ......... $TOTAL_ALL"
echo "   Σ max_concurrent (só membros ATIVOS) ........ $TOTAL_ACTIVE"
echo "   capacidade de membros INATIVOS (pausa etc) .. $PAUSED_CAP"
echo "   ocupação real (Σ SCARD do semáforo) ......... $USED"
A_HOJE=$(( TOTAL_ALL   - USED )); [ "$A_HOJE"  -lt 0 ] && A_HOJE=0
A_CERTO=$(( TOTAL_ACTIVE - USED )); [ "$A_CERTO" -lt 0 ] && A_CERTO=0
echo "   → available pelo modelo de HOJE (ignora status) .. $A_HOJE"
echo "   → available pelo modelo CORRETO (lê status) ...... $A_CERTO"
echo

SNAP="$(r GET "${TENANT}:pool:${POOL}:snapshot" | tr -d '\r')"
if [ -z "$SNAP" ]; then
  echo "   snapshot PUBLICADO: AUSENTE (TTL 3600 s expirado, ou pool nunca roteado)"
  echo "   ⚠ sem snapshot não há o que comparar — a divergência abaixo fica sem medir."
else
  echo "   snapshot PUBLICADO:"
  if command -v jq > /dev/null 2>&1; then
    printf '%s' "$SNAP" | jq -c '{available,busy,busy_elsewhere,untagged,total_instances,queue_length,model,updated_at}' 2>/dev/null \
      || printf '   %s\n' "$SNAP"
  else
    printf '   %s\n' "$SNAP"
  fi
  PUB="$(printf '%s' "$SNAP" | sed -n 's/.*"available"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')"
  echo
  echo "   available publicado = ${PUB:-?}   ·   modelo de hoje = $A_HOJE   ·   correto = $A_CERTO"
  if [ "$PAUSED_CAP" -eq 0 ]; then
    echo "   NOTA: nenhum membro INATIVO nesta foto — os dois modelos coincidem por"
    echo "   construção, e esta execução NÃO exercita a metade que interessa."
    echo "   Pause um agente no Console e rode de novo."
  fi
fi

# ── (D) A INSTÂNCIA PAUSADA É INVISÍVEL AOS SETs — procurá-la por fora ───────
# Achado da 1ª execução (2026-08-21): a pausa faz `SREM` de `:instances` no
# endpoint do mcp-server, então um agente pausado SEM sessão não está em
# ready_set nem em busy_set. A seção (C) acima, que só percorre a união dos
# dois, não o vê — e a ausência dele lá pareceu "pool vazio", que é um valor
# plausível. O rastro que sobrevive é o marcador durável da pausa.
echo "── (D) instâncias pausadas, vistas por FORA dos SETs ──"
MARKERS="$(r --scan --pattern "${TENANT}:agent_paused:*" 2>/dev/null | tr -d '\r' | sort -u)"
if [ -z "$MARKERS" ]; then
  echo "   nenhum marcador ${TENANT}:agent_paused:* — ninguém pausado agora."
  echo "   ⚠ esta execução NÃO exercita a metade da pausa."
else
  for mk in $MARKERS; do
    iid="${mk##*:agent_paused:}"
    RAW="$(r GET "${TENANT}:instance:${iid}" | tr -d '\r')"
    ST="$(printf '%s' "$RAW" | sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    MC="$(printf '%s' "$RAW" | sed -n 's/.*"max_concurrent"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')"
    N="$(r SCARD "${TENANT}:instance:${iid}:sessions" | tr -d '\r')"
    IN_R="$(r SISMEMBER "${TENANT}:pool:${POOL}:instances" "$iid" | tr -d '\r')"
    IN_B="$(r SISMEMBER "${TENANT}:pool:${POOL}:busy_instances" "$iid" | tr -d '\r')"
    echo "   $iid  status=${ST:-?}  max_concurrent=${MC:-?}  sessões=${N:-0}" \
         " ready_set=${IN_R}  busy_set=${IN_B}"
    if [ "${IN_R}" = "0" ] && [ "${IN_B}" = "0" ]; then
      echo "     → fora dos dois SETs: o recompute NÃO a alcança. Este é o caso em que"
      echo "       corrigir só o GATILHO (evento agent_pause) já dá o número certo."
    else
      echo "     → AINDA dentro de um SET: o recompute a alcança e soma a capacidade"
      echo "       dela sem olhar status. Este é o caso em que o gatilho sozinho troca"
      echo "       número congelado por número recalculado e igualmente errado."
    fi
  done
fi
echo

# ── (E) IDADE DA LINHA PUBLICADA ─────────────────────────────────────────────
# Um `available` congelado é indistinguível de um `available` medido agora —
# a não ser pelo `updated_at`, que nenhum consumidor lê como condição hoje.
if [ -n "$SNAP" ]; then
  UPD="$(printf '%s' "$SNAP" | sed -n 's/.*"updated_at"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  NOW_S="$(date -u +%s)"
  UPD_S="$(date -u -d "$UPD" +%s 2>/dev/null)"
  if [ -n "$UPD_S" ]; then
    echo "── (E) idade da linha publicada ──"
    echo "   updated_at = $UPD   (há $(( NOW_S - UPD_S )) s)"
    echo "   TTL do snapshot é 3600 s e o bootstrap escreve NX — uma linha congelada"
    echo "   sobrevive até UMA HORA sem que nada fique vermelho."
  fi
fi
echo
echo "(probe read-only: nada foi escrito)"
