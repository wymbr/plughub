#!/usr/bin/env bash
# mut_aas08_a2a_sdk.sh — o probe_aas08_a2a_sdk.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 a porta não confere `A2A-Version` (qualquer versão passa)                 → V1
# M2 `A2A-Version` ausente vale 1.0, não 0.3 (o "conserto" leniente)           → V1
# M3 a porta perde a rota COM barra final (o endereço que o card anuncia)       → V4 (e J2)
# M4 a porta não confere o tenant da credencial                                 → X1
# M5 a task volta a carregar `"kind": "task"` (campo da v0.3, fora do proto v1.0) → K2 (parser
#    ESTRITO do SDK Python) — é o defeito que só o SDK vê; `curl` e os probes AAS-06/07 passam
#
# M1/M2/M5 em `a2a_tasks.py`, M3/M4 em `main.py`, por `docker cp` + `docker restart`, `cmp`
# confirmando que o arquivo mudou; só conta a mutação pega pelo RAMO declarado. O `trap` recria o
# container da IMAGEM.
# NÃO MEDIDO AO VIVO: a barra final no card (projeção do agent-registry; a mutação exigiria rebuild
# do registry — guardada pelo V4 como presença e por `a2a-cards.test.ts`).
# ⚠️ ASSISTIDO: reinicia o channel-gateway (chamada e chat em curso caem).
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GWC="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
PROBE=infra/test/probe_aas08_a2a_sdk.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate channel-gateway >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

TASKS=$(docker exec "$GWC" python -c 'import plughub_channel_gateway.a2a_tasks as m;print(m.__file__)' 2>/dev/null)
MAIN=$(docker exec "$GWC" python -c 'import plughub_channel_gateway, os;print(os.path.join(os.path.dirname(plughub_channel_gateway.__file__), "main.py"))' 2>/dev/null)
[ -n "$TASKS" ] && [ -n "$MAIN" ] || { echo "INCONCL gateway fora do ar"; exit 2; }
docker cp "$GWC:$TASKS" "$TMP/tasks.orig" >/dev/null 2>&1 && docker cp "$GWC:$MAIN" "$TMP/main.orig" >/dev/null 2>&1 \
  || { echo "INCONCL não copiei os fontes do container"; exit 2; }

saude() {
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:8010/health | grep -q '^200$' && { sleep 3; return 0; }
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 nome  $2 tasks|main  — devolve o original do OUTRO arquivo antes de mutar este
  local orig="$TMP/$2.orig" alvo; [ "$2" = tasks ] && alvo=$TASKS || alvo=$MAIN
  python3 - "$orig" "$TMP/$1.mut" "$TMP/$1.par" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho.rstrip("\n"), novo.rstrip("\n"), 1))
PY
  if cmp -s "$orig" "$TMP/$1.mut"; then echo "NAO_APLICOU"; return; fi
  docker cp "$TMP/tasks.orig" "$GWC:$TASKS" >/dev/null
  docker cp "$TMP/main.orig" "$GWC:$MAIN" >/dev/null
  docker cp "$TMP/$1.mut" "$GWC:$alvo" >/dev/null
  docker restart "$GWC" >/dev/null
  saude || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
    if major_minor in SUPPORTED_VERSIONS:
=====
    if True:
EOF
cat > "$TMP/M2.par" <<'EOF'
    pedida = raw or "0.3"
=====
    pedida = raw or "1.0"
EOF
cat > "$TMP/M3.par" <<'EOF'
@app.post("/a2a/{slug}/")
=====
# (rota com barra removida pela mutação M3)
EOF
cat > "$TMP/M4.par" <<'EOF'
    if p.tenant_id != settings.tenant_id:
=====
    if False:
EOF
cat > "$TMP/M5.par" <<'EOF'
        task: dict = {"id": sid, "contextId": ctx, "status": status, "metadata": meta}
=====
        task: dict = {"kind": "task", "id": sid, "contextId": ctx, "status": status, "metadata": meta}
EOF

saude || { echo "INCONCL gateway fora do ar"; exit 2; }
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 tasks V1" "M2 tasks V1" "M3 main V4|J2" "M4 main X1" "M5 tasks K2"; do
  set -- $par
  rc=$(aplica "$1" "$2")
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
