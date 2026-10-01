#!/usr/bin/env bash
# mut_aas06_a2a_tasks.sh — o probe_aas06_a2a_tasks.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 o pedido deixa de ser conferido contra o input_schema          (gateway, a2a_tasks.py) → A1
# M2 contextId de OUTRO principal é adotado                         (gateway, a2a_tasks.py) → C2
# M3 a task de outro principal passa a ser lida                     (gateway, a2a_tasks.py) → E2
# M4 cancelar publica `client_disconnect` em vez de `caller_cancel` (gateway, a2a_tasks.py) → D1/D4
# M5 resultado tardio volta a mandar sobre a primeira causa do fim  (gateway, a2a_tasks.py) → D5
# M6 o menu ESTACIONADO volta a não contar para o fechamento        (bridge, main.py)       → D3
# M7 `caller_cancel` volta a cair no `else` do fechamento (flow_complete) (bridge, main.py) → D4
#
# ⚠️ Cada mutação declara o RAMO que tem de reprovar, e só conta como pega se ELE reprovar: na
# primeira rodada, sobras de rodadas anteriores lotavam o pool e o A2 reprovava por SUBMITTED em
# quase toda mutação — "vermelho" pelo motivo errado, que parecia proteção.
# Por `docker cp` + `docker restart` do container mutado, `cmp` confirmando que o arquivo mudou; o
# `trap` recria os dois da IMAGEM.
# NÃO MEDIDO AO VIVO, guardado por teste de unidade: a interrompida que só assenta com fala NOVA
# do agente (`test_interrompida_so_assenta_com_fala_NOVA_do_agente` — ao vivo, o B1 reconsulta e
# passaria mesmo assim); o carimbo do principal no AuditRecord (sem borda `invoke` configurada,
# CAP-07); o canal só de entrada fora da eleição do collect (`test_negotiate_nunca_elege…`).
# ⚠️ ASSISTIDO: reinicia o channel-gateway e o orchestrator-bridge (chamada, chat e conversa em
# curso caem). Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GWC="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
BRC="${BRIDGE_CONTAINER:-plughub-demo-orchestrator-bridge-1}"
PROBE=infra/test/probe_aas06_a2a_tasks.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate \
    channel-gateway orchestrator-bridge >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

GWPATH=$(docker exec "$GWC" python -c 'import plughub_channel_gateway.a2a_tasks as m;print(m.__file__)' 2>/dev/null)
BRPATH=$(docker exec "$BRC" python -c 'import plughub_orchestrator_bridge.main as m;print(m.__file__)' 2>/dev/null)
[ -n "$GWPATH" ] && [ -n "$BRPATH" ] || { echo "INCONCL gateway ou bridge fora do ar"; exit 2; }
ALVOS=("$GWC|$GWPATH" "$BRC|$BRPATH")
i=0
for a in "${ALVOS[@]}"; do
  docker cp "${a%%|*}:${a#*|}" "$TMP/$i.orig" >/dev/null 2>&1 || { echo "INCONCL não copiou ${a#*|}"; exit 2; }
  i=$((i + 1))
done

saude() {
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:8010/health | grep -q '^200$' \
      && [ "$(docker inspect -f '{{.State.Running}}' "$BRC")" = true ] && { sleep 20; return 0; }   # grupo Kafka do bridge
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 nome, $2 índice do alvo, $3 par (velho ===== novo)
  local alvo=${ALVOS[$2]} c=${ALVOS[$2]%%|*}
  python3 - "$TMP/$2.orig" "$TMP/$1.mut" "$3" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho.rstrip("\n"), novo.rstrip("\n"), 1))
PY
  if cmp -s "$TMP/$2.orig" "$TMP/$1.mut"; then echo "NAO_APLICOU"; return; fi
  i=0
  for a in "${ALVOS[@]}"; do docker cp "$TMP/$i.orig" "${a%%|*}:${a#*|}" >/dev/null; i=$((i + 1)); done
  docker cp "$TMP/$1.mut" "$c:${alvo#*|}" >/dev/null
  docker restart "$GWC" "$BRC" >/dev/null
  saude || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
        if erros:
            raise A2AError(INVALID_PARAMS, "o pedido não cumpre o input_schema do agente", {"errors": erros})
=====
        if False:
            raise A2AError(INVALID_PARAMS, "o pedido não cumpre o input_schema do agente", {"errors": erros})
EOF
cat > "$TMP/M2.par" <<'EOF'
            if dono != caller.sub:
=====
            if dono == "nunca":
EOF
cat > "$TMP/M3.par" <<'EOF'
        if not rec or rec.get("sub") != caller.sub or rec.get("pool_id") != caller.pool_id:
=====
        if not rec:
EOF
cat > "$TMP/M4.par" <<'EOF'
            "channel": "a2a", "reason": "caller_cancel", "started_at": rec["created_at"],
=====
            "channel": "a2a", "reason": "client_disconnect", "started_at": rec["created_at"],
EOF
cat > "$TMP/M5.par" <<'EOF'
        if closed and closed not in CLOSE_DEFERS_TO_RESULT:
            return CLOSE_TO_STATE.get(closed, FAILED), closed
        if result is not None:
=====
        if result is not None:
EOF
cat > "$TMP/M6.par" <<'EOF'
                elif (campo in estacionados and campo != "_default_") or await redis_client.exists(
=====
                elif False or await redis_client.exists(
EOF
cat > "$TMP/M7.par" <<'EOF'
        elif _transport_reason == "caller_cancel":
=====
        elif _transport_reason == "caller_cancel_mutado":
EOF

saude || { echo "INCONCL serviços fora do ar"; exit 2; }
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 0 A1" "M2 0 C2" "M3 0 E2" "M4 0 D1|D4" "M5 0 D5|D1" "M6 1 D3" "M7 1 D4"; do
  set -- $par
  rc=$(aplica "$1" "$2" "$TMP/$1.par")
  if [ "$rc" = 1 ] && ! grep -qE "^  FALHA   ($3) " "$TMP/out"; then rc=OUTRO; fi
  case "$rc" in
    1) echo "  ✓ $1 pega pelo ramo $3: $(grep -E "^  FALHA   ($3) " "$TMP/out" | head -1 | sed 's/^ *//' | cut -c1-140)";;
    OUTRO) echo "  ? $1 vermelho por OUTRO ramo, não por $3 — não mede: $(grep -E '^  FALHA' "$TMP/out" | head -2 | sed 's/^ *//' | cut -c1-120 | tr '\n' ' ')"; falhas=$((falhas + 1));;
    NAO_APLICOU) echo "  ? $1 a mutação não se aplicou (o fonte mudou?) — não mede"; falhas=$((falhas + 1));;
    NAO_SUBIU) echo "  ? $1 o serviço não subiu com a mutação — não mede"; falhas=$((falhas + 1));;
    *) echo "  ✗ $1 SOBREVIVEU (probe rc=$rc) $(grep -E '^  (FALHA|INCONCL)' "$TMP/out" | head -2 | tr '\n' ' ')"; falhas=$((falhas + 1));;
  esac
done
[ "$falhas" -eq 0 ] && { echo "TODAS AS MUTAÇÕES PEGAS"; exit 0; }
echo "$falhas mutação(ões) não pegas ou não medidas"; exit 1
