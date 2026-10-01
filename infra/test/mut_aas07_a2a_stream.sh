#!/usr/bin/env bash
# mut_aas07_a2a_stream.sh — o probe_aas07_a2a_stream.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 fala nova sai como progresso sem esperar um ciclo (o prompt sai duas vezes)  → S1
# M2 o stream não fecha em estado INTERROMPIDO (só no terminal)                  → S1
# M3 o artefato não é transmitido antes do COMPLETED                              → S2
# M4 `SubscribeToTask` aceita task terminal                                       → S3
# M5 `SubscribeToTask` aceita task de outro principal                             → S5
#
# Todas em `a2a_tasks.py` do channel-gateway, por `docker cp` + `docker restart`, `cmp` confirmando
# que o arquivo mudou; só conta a mutação pega pelo RAMO declarado (lição da bateria da AAS-06). O
# `trap` recria o container da IMAGEM.
# NÃO MEDIDO AO VIVO: `capabilities.streaming` no card (projeção do agent-registry; a mutação
# exigiria rebuild do registry — guardado pelo S6 como presença, não como bateria); a fala no meio
# do trabalho e o batimento/teto do stream ocioso (`test_aas07_a2a_stream.py`).
# ⚠️ ASSISTIDO: reinicia o channel-gateway (chamada e chat em curso caem).
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GWC="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
PROBE=infra/test/probe_aas07_a2a_stream.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate channel-gateway >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

ALVO=$(docker exec "$GWC" python -c 'import plughub_channel_gateway.a2a_tasks as m;print(m.__file__)' 2>/dev/null)
[ -n "$ALVO" ] || { echo "INCONCL gateway fora do ar"; exit 2; }
docker cp "$GWC:$ALVO" "$TMP/orig" >/dev/null 2>&1 || { echo "INCONCL não copiou $ALVO"; exit 2; }

saude() {
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:8010/health | grep -q '^200$' && { sleep 3; return 0; }
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 nome, $2 par (velho ===== novo)
  python3 - "$TMP/orig" "$TMP/$1.mut" "$2" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho.rstrip("\n"), novo.rstrip("\n"), 1))
PY
  if cmp -s "$TMP/orig" "$TMP/$1.mut"; then echo "NAO_APLICOU"; return; fi
  docker cp "$TMP/$1.mut" "$GWC:$ALVO" >/dev/null
  docker restart "$GWC" >/dev/null
  saude || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
                prontos = [m for m in novos if m["id"] in vistos]
=====
                prontos = list(novos)
EOF
cat > "$TMP/M2.par" <<'EOF'
            return v["state"] in TERMINAL or (v["state"] in INTERRUPTED and novo)
=====
            return v["state"] in TERMINAL
EOF
cat > "$TMP/M3.par" <<'EOF'
            if final and view["state"] == COMPLETED and task.get("artifacts"):
=====
            if False and task.get("artifacts"):
EOF
cat > "$TMP/M4.par" <<'EOF'
            if view["state"] in TERMINAL:
                raise A2AError(UNSUPPORTED, "UnsupportedOperationError",
                               {"detail": f"a task já terminou ({view['state']}); use GetTask"})
=====
            if False:
                raise A2AError(UNSUPPORTED, "UnsupportedOperationError",
                               {"detail": f"a task já terminou ({view['state']}); use GetTask"})
EOF
cat > "$TMP/M5.par" <<'EOF'
        if not rec or rec.get("sub") != caller.sub or rec.get("pool_id") != caller.pool_id:
=====
        if not rec:
EOF

saude || { echo "INCONCL gateway fora do ar"; exit 2; }
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 S1" "M2 S1|S4" "M3 S2" "M4 S3" "M5 S5"; do
  set -- $par
  rc=$(aplica "$1" "$TMP/$1.par")
  if [ "$rc" = 1 ] && ! grep -qE "^  FALHA   ($2) " "$TMP/out"; then rc=OUTRO; fi
  case "$rc" in
    1) echo "  ✓ $1 pega pelo ramo $2: $(grep -E "^  FALHA   ($2) " "$TMP/out" | head -1 | sed 's/^ *//' | cut -c1-140)";;
    OUTRO) echo "  ? $1 vermelho por OUTRO ramo, não por $2 — não mede: $(grep -E '^  FALHA' "$TMP/out" | head -2 | cut -c1-120 | tr '\n' ' ')"; falhas=$((falhas + 1));;
    NAO_APLICOU) echo "  ? $1 a mutação não se aplicou (o fonte mudou?) — não mede"; falhas=$((falhas + 1));;
    NAO_SUBIU) echo "  ? $1 o serviço não subiu com a mutação — não mede"; falhas=$((falhas + 1));;
    *) echo "  ✗ $1 SOBREVIVEU (probe rc=$rc) $(grep -E '^  (FALHA|INCONCL)' "$TMP/out" | head -2 | tr '\n' ' ')"; falhas=$((falhas + 1));;
  esac
done
[ "$falhas" -eq 0 ] && { echo "TODAS AS MUTAÇÕES PEGAS"; exit 0; }
echo "$falhas mutação(ões) não pegas ou não medidas"; exit 1
