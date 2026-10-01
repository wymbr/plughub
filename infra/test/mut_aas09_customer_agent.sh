#!/usr/bin/env bash
# mut_aas09_customer_agent.sh — o probe_aas09_customer_agent.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 mcp-server  prova de DOIS clientes na sessão deixa de ser ambígua          → G2
# M2 auth-api    o código de retirada serve de novo (sem `redeemed_at IS NULL`)  → R3
# M3 gateway     o teto de tasks ATIVAS não é imposto                            → Q1
# M4 gateway     a sessão do assistente nasce com cliente de sistema            → S2
# M5 auth-api    a introspecção ignora o principal desativado (revogar não vale) → V1
#
# Por `docker cp` + `docker restart` no container de cada uma, `cmp` confirmando que o arquivo
# mudou; só conta a mutação pega pelo RAMO declarado. O `trap` recria os três da IMAGEM.
# NÃO MEDIDO AQUI (declarado): `freshProofs` aceitando prova de outra sessão ou velha — o probe só
# semeia prova da própria sessão; guardam `aas09-customer-agent.test.ts` e `customer-agent.test.ts`.
#
# ⚠️ "Titular vindo do INPUT" NÃO é mutável, e foi medido: a 1ª versão desta bateria trocava o
# titular por `input["customer_id"]` e a mutação SOBREVIVEU — o SDK do MCP valida o input contra o
# formato declarado da tool e DESCARTA campo desconhecido, então `customer_id` nunca chega ao
# handler. A garantia tem duas camadas (o formato declarado e o código que lê a prova); o S2 do
# probe a mede de fora, e esta bateria mede a camada de código pela ambiguidade (M1).
# ⚠️ ASSISTIDO: reinicia mcp-server, auth-api e channel-gateway (login, chat e chamada caem).
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
PROBE=infra/test/probe_aas09_customer_agent.sh
TMP=$(mktemp -d)
MCP=plughub-demo-mcp-server-plughub-1; AUTH=plughub-demo-auth-api-1; GWC=plughub-demo-channel-gateway-1
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate \
    mcp-server-plughub auth-api channel-gateway >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

F_MCP=/app/packages/mcp-server-plughub/dist/tools/customer-agent.js
F_AUTH=$(docker exec "$AUTH" python -c 'import plughub_auth_api.agent_principals as m;print(m.__file__)' 2>/dev/null)
F_GW=$(docker exec "$GWC" python -c 'import plughub_channel_gateway.a2a_tasks as m;print(m.__file__)' 2>/dev/null)
[ -n "$F_AUTH" ] && [ -n "$F_GW" ] || { echo "INCONCL serviços fora do ar"; exit 2; }
docker cp "$MCP:$F_MCP" "$TMP/mcp.orig" >/dev/null 2>&1 && docker cp "$AUTH:$F_AUTH" "$TMP/auth.orig" >/dev/null 2>&1 \
  && docker cp "$GWC:$F_GW" "$TMP/gw.orig" >/dev/null 2>&1 || { echo "INCONCL não copiei os fontes"; exit 2; }

saude() {  # $1 porta
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://localhost:$1/health" | grep -q '^200$' && { sleep 3; return 0; }
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 nome  $2 mcp|auth|gw
  local orig="$TMP/$2.orig" cont alvo porta
  case "$2" in mcp) cont=$MCP; alvo=$F_MCP; porta=3100;; auth) cont=$AUTH; alvo=$F_AUTH; porta=3202;; gw) cont=$GWC; alvo=$F_GW; porta=8010;; esac
  python3 - "$orig" "$TMP/$1.mut" "$TMP/$1.par" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho.rstrip("\n"), novo.rstrip("\n"), 1))
PY
  if cmp -s "$orig" "$TMP/$1.mut"; then echo "NAO_APLICOU"; return; fi
  docker cp "$TMP/$1.mut" "$cont:$alvo" >/dev/null
  docker restart "$cont" >/dev/null
  saude "$porta" || { echo "NAO_SUBIU"; return; }
  roda
  docker cp "$orig" "$cont:$alvo" >/dev/null     # volta antes da próxima (que pode ser outro serviço)
  docker restart "$cont" >/dev/null; saude "$porta" >/dev/null
}

cat > "$TMP/M1.par" <<'EOF'
    if (clientes.size > 1)
        return { refused: "ambiguous_holder" };
=====
    if (false)
        return { refused: "ambiguous_holder" };
EOF
cat > "$TMP/M2.par" <<'EOF'
                    WHERE pickup_hash = $1 AND tenant_id = $2 AND redeemed_at IS NULL
=====
                    WHERE pickup_hash = $1 AND tenant_id = $2
EOF
cat > "$TMP/M3.par" <<'EOF'
                if ativas >= max_ativas:
=====
                if False:
EOF
cat > "$TMP/M4.par" <<'EOF'
        customer_id = caller.holder.customer_id if caller.holder else f"sys:a2a:{uuid.uuid4().hex[:8]}"
=====
        customer_id = f"sys:a2a:{uuid.uuid4().hex[:8]}"
EOF
cat > "$TMP/M5.par" <<'EOF'
    if not row["active"]:
        logger.warning("introspecção: credencial de principal DESATIVADO
=====
    if False:
        logger.warning("introspecção: credencial de principal DESATIVADO
EOF

for p in 3100 3202 8010; do saude $p || { echo "INCONCL serviço na porta $p fora do ar"; exit 2; }; done
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 mcp G2" "M2 auth R3" "M3 gw Q1" "M4 gw S2" "M5 auth V1"; do
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
