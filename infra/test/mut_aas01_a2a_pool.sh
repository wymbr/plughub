#!/usr/bin/env bash
# mut_aas01_a2a_pool.sh — o probe_aas01_a2a_pool.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 o descritor sem o canal deixa de ser recusado         → B4 / B7
# M2 o canal sem o descritor deixa de ser recusado         → B1 / B3
# M3 o espelho `internal` perde a isenção (todo pool exige) → C2
# M4 o PUT julga o CORPO do descritor, não o estado        → B7 (tirar o canal com descritor gravado)
# M5 o PUT julga o CORPO dos canais, não o estado          → B3 (limpar o descritor de pool a2a)
#
# Mutação no JS COMPILADO do agent-registry (`dist/lib/a2a-descriptor.js`, `dist/routes/pools.js`)
# por `docker cp` + `docker restart`; `cmp` confirma que o arquivo mudou. O `trap` recria o
# container da IMAGEM (sem mutação).
# ⚠️ ASSISTIDO: reinicia o agent-registry seis vezes (o registry fica fora por segundos a cada uma).
# Cada rodada deixa até 2 pools `probe_aas01_sem_contrato_*` (criados com o portão desligado), que o
# probe NEUTRALIZA na rodada seguinte (sem o canal, sem descritor); a API não apaga nem desativa pool.
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
REGC="${REGISTRY_CONTAINER:-plughub-demo-agent-registry-1}"
PROBE=infra/test/probe_aas01_a2a_pool.sh
DIST=/app/packages/agent-registry/dist
TMP=$(mktemp -d)
ARQS="lib/a2a-descriptor routes/pools"
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate agent-registry >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

for f in $ARQS; do
  docker cp "$REGC:$DIST/$f.js" "$TMP/$(basename $f).orig.js" >/dev/null 2>&1 \
    || { echo "INCONCL não copiou $f.js (registry fora do ar?)"; exit 2; }
done

espera_subir() {
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:3300/v1/health 2>/dev/null | grep -q '^200$' && return 0
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 = nome, $2 = arquivo (relativo a dist, sem .js), $3 = par (velho ===== novo)
  local base; base=$(basename "$2")
  python3 - "$TMP/$base.orig.js" "$TMP/$1.js" "$3" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho, novo.rstrip("\n"), 1))
PY
  if cmp -s "$TMP/$base.orig.js" "$TMP/$1.js"; then echo "NAO_APLICOU"; return; fi
  for f in $ARQS; do docker cp "$TMP/$(basename $f).orig.js" "$REGC:$DIST/$f.js" >/dev/null; done
  docker cp "$TMP/$1.js" "$REGC:$DIST/$2.js" >/dev/null
  docker restart "$REGC" >/dev/null
  espera_subir || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
    if (descriptor != null && !hasChannel) {
=====
    if (false) {
EOF
cat > "$TMP/M2.par" <<'EOF'
    if (hasChannel && isContact && descriptor == null) {
=====
    if (false) {
EOF
cat > "$TMP/M3.par" <<'EOF'
    const isContact = (purpose ?? "contact") === "contact";
=====
    const isContact = true;
EOF
cat > "$TMP/M4.par" <<'EOF'
body.a2a !== undefined ? body.a2a : exA2a.a2a);
=====
body.a2a);
EOF
cat > "$TMP/M5.par" <<'EOF'
body.channel_types !== undefined ? body.channel_types : exA2a.channel_types, body.purpose
=====
body.channel_types, body.purpose
EOF

espera_subir || { echo "INCONCL registry fora do ar"; exit 2; }
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 lib/a2a-descriptor" "M2 lib/a2a-descriptor" "M3 lib/a2a-descriptor" "M4 routes/pools" "M5 routes/pools"; do
  set -- $par
  rc=$(aplica "$1" "$2" "$TMP/$1.par")
  case "$rc" in
    1) echo "  ✓ $1 pega: $(grep -E '^  FALHA' "$TMP/out" | head -2 | sed 's/^ *//' | tr '\n' ' ')";;
    NAO_APLICOU) echo "  ? $1 a mutação não se aplicou (o compilado mudou?) — não mede"; falhas=$((falhas + 1));;
    NAO_SUBIU) echo "  ? $1 o registry não subiu com a mutação — não mede"; falhas=$((falhas + 1));;
    *) echo "  ✗ $1 SOBREVIVEU (probe rc=$rc)"; falhas=$((falhas + 1));;
  esac
done
[ "$falhas" -eq 0 ] && { echo "TODAS AS MUTAÇÕES PEGAS"; exit 0; }
echo "$falhas mutação(ões) não pegas ou não medidas"; exit 1
