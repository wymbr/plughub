#!/usr/bin/env bash
# mut_aas19_auth_required.sh — o probe_aas19_auth_required.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 gateway  a2a_tasks: link pendente nunca vira AUTH_REQUIRED (vira INPUT_REQUIRED)  → A1
# M2 gateway  identity_proof: o GET da página ENVIA o código                          → P2
# M3 gateway  a2a_tasks: o stream FECHA em AUTH_REQUIRED                              → S1
# M4 bridge   o sinal `proof: settled` não é reconhecido (vira resposta do cliente)   → C2|C4
# M5 mcp      identity_proof_status responde `verified: true` sempre                  → N2
# M6 gateway  identity_proof: o código errado passa                                   → P5
#
# Por `docker cp` + `docker restart` dos três containers, `cmp` confirmando que o arquivo mudou;
# só conta a mutação pega pelo RAMO declarado. O `trap` recria os três da IMAGEM.
# NÃO MEDIDO AQUI (declarado): o juiz da evidência recusando `otp` não-`verified` por pedido — o
# probe só manda a prova concluída; guarda `arrival-evidence.test.ts`. A recusa de prova de OUTRO
# cliente na sessão do `customer_agent` (`customer_not_holder`) — o probe usa `partner`; guarda
# `identity-proof.test.ts`.
# ⚠️ ASSISTIDO: reinicia channel-gateway, orchestrator-bridge e mcp-server (login, chat e chamada
# caem). Cada rodada importa um cliente novo (o OTP limita 3 desafios por telefone em 15 min).
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
PROBE=infra/test/probe_aas19_auth_required.sh
GWC="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
BRC="${BRIDGE_CONTAINER:-plughub-demo-orchestrator-bridge-1}"
MCP="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate \
    channel-gateway orchestrator-bridge mcp-server-plughub >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

F_TASKS=$(docker exec "$GWC" python -c 'import plughub_channel_gateway.a2a_tasks as m;print(m.__file__)' 2>/dev/null)
F_PROOF=$(docker exec "$GWC" python -c 'import plughub_channel_gateway.identity_proof as m;print(m.__file__)' 2>/dev/null)
F_BR=$(docker exec "$BRC" python -c 'import plughub_orchestrator_bridge.main as m;print(m.__file__)' 2>/dev/null)
F_MCP=/app/packages/mcp-server-plughub/dist/tools/identity-proof.js
[ -n "$F_TASKS" ] && [ -n "$F_PROOF" ] && [ -n "$F_BR" ] || { echo "INCONCL serviços fora do ar"; exit 2; }
ALVOS=("$GWC|$F_TASKS" "$GWC|$F_PROOF" "$BRC|$F_BR" "$MCP|$F_MCP")
i=0
for a in "${ALVOS[@]}"; do
  docker cp "${a%%|*}:${a#*|}" "$TMP/$i.orig" >/dev/null 2>&1 || { echo "INCONCL não copiou ${a#*|}"; exit 2; }
  i=$((i + 1))
done

saude() {
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:8010/health | grep -q '^200$' \
      && curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:3100/health | grep -q '^200$' \
      && [ "$(docker inspect -f '{{.State.Running}}' "$BRC")" = true ] && { sleep 20; return 0; }   # grupo Kafka do bridge
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 nome, $2 índice do alvo
  local alvo=${ALVOS[$2]} c=${ALVOS[$2]%%|*}
  python3 - "$TMP/$2.orig" "$TMP/$1.mut" "$TMP/$1.par" <<'PY'
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
  docker restart "$GWC" "$BRC" "$MCP" >/dev/null
  saude || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
                if proof:
                    return AUTH_REQUIRED, "identity_proof"
=====
                if False:
                    return AUTH_REQUIRED, "identity_proof"
EOF
cat > "$TMP/M2.par" <<'EOF'
    return Page(page_offer(anchor_hint(rec["kind"], rec["value"])))
=====
    await deps.adapter.otp_challenge(tenant_id, rec["customer_id"], rec["kind"], rec["value"])
    return Page(page_offer(anchor_hint(rec["kind"], rec["value"])))
EOF
cat > "$TMP/M3.par" <<'EOF'
            return v["state"] in TERMINAL or (v["state"] in INTERRUPTED - OUT_OF_BAND and novo)
=====
            return v["state"] in TERMINAL or (v["state"] in INTERRUPTED and novo)
EOF
cat > "$TMP/M4.par" <<'EOF'
            if (content.get("payload") or {}).get("proof") == "settled":
=====
            if (content.get("payload") or {}).get("proof") == "__nunca__":
EOF
cat > "$TMP/M5.par" <<'EOF'
        return ok({ verified: j.verified, ...(j.verified
=====
        return ok({ verified: true, ...(j.verified
EOF
cat > "$TMP/M6.par" <<'EOF'
        if res.get("verified") is not True:
=====
        if False:
EOF

saude || { echo "INCONCL serviços fora do ar"; exit 2; }
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; grep -E '^  (FALHA|INCONCL)' "$TMP/out" | head -3; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 0 A1" "M2 1 P2" "M3 0 S1" "M4 2 C2|C4" "M5 3 N2" "M6 1 P5"; do
  set -- $par
  rc=$(aplica "$1" "$2" | tail -1)
  if [ "$rc" = 1 ] && ! grep -qE "^  FALHA   ($3) " "$TMP/out"; then rc=OUTRO; fi
  case "$rc" in
    1) echo "  ✓ $1 pega pelo ramo $3: $(grep -E "^  FALHA   ($3) " "$TMP/out" | head -1 | sed 's/^ *//' | cut -c1-140)";;
    OUTRO) echo "  ? $1 vermelho por OUTRO ramo, não por $3 — não mede: $(grep -E '^  FALHA' "$TMP/out" | head -2 | cut -c1-120 | tr '\n' ' ')"; falhas=$((falhas + 1));;
    NAO_APLICOU) echo "  ? $1 a mutação não se aplicou (o fonte mudou?) — não mede"; falhas=$((falhas + 1));;
    NAO_SUBIU) echo "  ? $1 o serviço não subiu com a mutação — não mede"; falhas=$((falhas + 1));;
    *) echo "  ✗ $1 SOBREVIVEU (probe rc=$rc) $(grep -E '^  (FALHA|INCONCL)' "$TMP/out" | head -2 | tr '\n' ' ')"; falhas=$((falhas + 1));;
  esac
done
[ "$falhas" -eq 0 ] && { echo "TODAS AS MUTAÇÕES PEGAS"; exit 0; }
echo "$falhas mutação(ões) não pegas ou não medidas"; exit 1
