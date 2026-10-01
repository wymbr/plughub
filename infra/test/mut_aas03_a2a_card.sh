#!/usr/bin/env bash
# mut_aas03_a2a_card.sh — o probe_aas03_a2a_card.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 o card deixa de exigir `discoverable`             → R3 / G3
# M2 a versão vira o relógio, não o set_at do deploy   → C3
# M3 pool sem deploy passa a ter card                  → R2 / G1
# M4 o cadastro de endpoint a2a deixa de conferir o pool e o slug → E1 / E2
# M5 a borda passa a dizer o MOTIVO no 404 (oráculo)   → G1
#
# M1–M4 no JS COMPILADO do agent-registry, M5 no `main.py` do channel-gateway, por `docker cp` +
# `docker restart`; `cmp` confirma que o arquivo mudou. O `trap` recria os dois containers da
# IMAGEM (sem mutação).
# ⚠️ ASSISTIDO: reinicia o agent-registry e o channel-gateway (chamada e chat em curso caem), e cada
# rodada do probe espera 31 s pelo cache da borda — a bateria leva uns 6 minutos.
# Sob M4 o E1/E2 CRIAM endereços de verdade; o probe os apaga antes do ramo E (medido: sem isso a
# rodada seguinte via 409 no lugar do 422).
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
REGC="${REGISTRY_CONTAINER:-plughub-demo-agent-registry-1}"
GWC="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
PROBE=infra/test/probe_aas03_a2a_card.sh
DIST=/app/packages/agent-registry/dist
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate agent-registry channel-gateway >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

GWPKG=$(docker exec "$GWC" python -c 'import os,plughub_channel_gateway as p;print(os.path.dirname(p.__file__))' 2>/dev/null)
[ -n "$GWPKG" ] || { echo "INCONCL channel-gateway fora do ar"; exit 2; }
# alvo = "container|caminho"; o original de cada um fica em $TMP/<n>.orig
ALVOS=("$REGC|$DIST/lib/a2a-card.js" "$REGC|$DIST/routes/channel-endpoints.js" "$GWC|$GWPKG/main.py")
i=0
for a in "${ALVOS[@]}"; do
  docker cp "${a%%|*}:${a#*|}" "$TMP/$i.orig" >/dev/null 2>&1 || { echo "INCONCL não copiou ${a#*|}"; exit 2; }
  i=$((i + 1))
done

saude() {
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:3300/v1/health | grep -q '^200$' \
      && curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:8010/health | grep -q '^200$' && return 0
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
io.open(dst, "w", encoding="utf-8").write(s.replace(velho, novo.rstrip("\n"), 1))
PY
  if cmp -s "$TMP/$2.orig" "$TMP/$1.mut"; then echo "NAO_APLICOU"; return; fi
  i=0
  for a in "${ALVOS[@]}"; do docker cp "$TMP/$i.orig" "${a%%|*}:${a#*|}" >/dev/null; i=$((i + 1)); done
  docker cp "$TMP/$1.mut" "$c:${alvo#*|}" >/dev/null
  docker restart "$REGC" "$GWC" >/dev/null
  saude || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
    if (!parsed.data.discoverable)
=====
    if (false)
EOF
cat > "$TMP/M2.par" <<'EOF'
        deployedAt: setAt instanceof Date ? setAt.toISOString() : String(setAt),
=====
        deployedAt: new Date().toISOString(),
EOF
cat > "$TMP/M3.par" <<'EOF'
    if (!current || !current["skill_id"] || !setAt)
=====
    if (false)
EOF
cat > "$TMP/M4.par" <<'EOF'
        if (body.channel === "a2a") {
            const v = _a2aEndpointViolation(identifier, pool);
=====
        if (false) {
            const v = _a2aEndpointViolation(identifier, pool);
EOF
cat > "$TMP/M5.par" <<'EOF'
        return JSONResponse({"error": "not_found"}, status_code=404)
=====
        return JSONResponse({"error": "not_found", "reason": res.reason}, status_code=404)
EOF

saude || { echo "INCONCL registry ou gateway fora do ar"; exit 2; }
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 0" "M2 0" "M3 0" "M4 1" "M5 2"; do
  set -- $par
  rc=$(aplica "$1" "$2" "$TMP/$1.par")
  case "$rc" in
    1) echo "  ✓ $1 pega: $(grep -E '^  FALHA' "$TMP/out" | head -2 | sed 's/^ *//' | tr '\n' ' ')";;
    NAO_APLICOU) echo "  ? $1 a mutação não se aplicou (o fonte mudou?) — não mede"; falhas=$((falhas + 1));;
    NAO_SUBIU) echo "  ? $1 o serviço não subiu com a mutação — não mede"; falhas=$((falhas + 1));;
    *) echo "  ✗ $1 SOBREVIVEU (probe rc=$rc)"; falhas=$((falhas + 1));;
  esac
done
[ "$falhas" -eq 0 ] && { echo "TODAS AS MUTAÇÕES PEGAS"; exit 0; }
echo "$falhas mutação(ões) não pegas ou não medidas"; exit 1
