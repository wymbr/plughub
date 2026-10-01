#!/usr/bin/env bash
# mut_aas05_session_result.sh — o probe_aas05_session_result.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 o engine deixa de devolver o bloco `terminal`           (skill-flow-service, dist/engine.js) → R1 / S3
# M2 o veredicto aceita qualquer resultado                    (bridge, session_result.py)        → R2
# M3 o resultado é gravado SEM TTL                            (bridge, session_result.py)        → S3
# M4 sessão de outro tenant responde `closed`                 (gateway, adapters/webhook.py)     → S2
# M5 sessão sem fato nenhum volta a responder `closed`        (gateway, adapters/webhook.py)     → S1
#
# Por `docker cp` + `docker restart` do container mutado, `cmp` confirmando que o arquivo mudou; o
# `trap` recria os três da IMAGEM.
# NÃO MEDIDO ao vivo, e guardado pelos testes de unidade no lugar:
#   · a guarda de CONFERÊNCIA (especialista não grava o resultado da sessão) — o probe não monta
#     conferência; `test_aas05_session_result.py::test_conferencia_nao_fala_pela_sessao`;
#   · o resume que APAGA a marca `suspended` (antes: `active` sem TTL) — o fluxo de fixture não
#     suspende; `test_webhook_adapter.py::test_handle_resume_clears_the_suspended_mark`.
# ⚠️ ASSISTIDO: reinicia skill-flow-service, orchestrator-bridge e channel-gateway (conversas em
# curso caem). Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
SFC="${SFS_CONTAINER:-plughub-demo-skill-flow-service-1}"
BRC="${BRIDGE_CONTAINER:-plughub-demo-orchestrator-bridge-1}"
GWC="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
PROBE=infra/test/probe_aas05_session_result.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate \
    skill-flow-service orchestrator-bridge channel-gateway >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

BRPATH=$(docker exec "$BRC" python -c 'import plughub_orchestrator_bridge.session_result as m;print(m.__file__)' 2>/dev/null)
GWPATH=$(docker exec "$GWC" python -c 'import plughub_channel_gateway.adapters.webhook as m;print(m.__file__)' 2>/dev/null)
SFPATH=/app/packages/skill-flow-engine/dist/engine.js
docker exec "$SFC" test -f "$SFPATH" || SFPATH=""
[ -n "$BRPATH" ] && [ -n "$GWPATH" ] && [ -n "$SFPATH" ] || { echo "INCONCL algum dos três serviços fora do ar"; exit 2; }
ALVOS=("$SFC|$SFPATH" "$BRC|$BRPATH" "$GWC|$GWPATH")
i=0
for a in "${ALVOS[@]}"; do
  docker cp "${a%%|*}:${a#*|}" "$TMP/$i.orig" >/dev/null 2>&1 || { echo "INCONCL não copiou ${a#*|}"; exit 2; }
  i=$((i + 1))
done

saude() {
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:8010/health | grep -q '^200$' \
      && [ "$(docker inspect -f '{{.State.Health.Status}}' "$SFC")" = healthy ] \
      && [ "$(docker inspect -f '{{.State.Running}}' "$BRC")" = true ] && { sleep 20; return 0; }   # 20 s: o bridge entra no grupo Kafka
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
  docker restart "$SFC" "$BRC" "$GWC" >/dev/null
  saude || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
                    terminal: {
=====
                    terminal_mutado: {
EOF
cat > "$TMP/M2.par" <<'EOF'
    if not errors:
=====
    if True:
EOF
cat > "$TMP/M3.par" <<'EOF'
json.dumps(doc, default=str), ex=ttl_s)
=====
json.dumps(doc, default=str))
EOF
cat > "$TMP/M4.par" <<'EOF'
            return {"session_id": session_id, "status": "unknown"}
=====
            return {"session_id": session_id, "status": "closed"}
EOF
cat > "$TMP/M5.par" <<'EOF'
            return {"session_id": session_id, "status": "suspended"}
        return {"session_id": session_id, "status": "unknown"}
=====
            return {"session_id": session_id, "status": "suspended"}
        return {"session_id": session_id, "status": "closed"}
EOF

saude || { echo "INCONCL serviços fora do ar"; exit 2; }
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 0" "M2 1" "M3 1" "M4 2" "M5 2"; do
  set -- $par
  rc=$(aplica "$1" "$2" "$TMP/$1.par")
  case "$rc" in
    1) echo "  ✓ $1 pega: $(grep -E '^  FALHA' "$TMP/out" | head -2 | sed 's/^ *//' | cut -c1-160 | tr '\n' ' ')";;
    NAO_APLICOU) echo "  ? $1 a mutação não se aplicou (o fonte mudou?) — não mede"; falhas=$((falhas + 1));;
    NAO_SUBIU) echo "  ? $1 o serviço não subiu com a mutação — não mede"; falhas=$((falhas + 1));;
    *) echo "  ✗ $1 SOBREVIVEU (probe rc=$rc) $(grep -E '^  (FALHA|INCONCL)' "$TMP/out" | head -2 | tr '\n' ' ')"; falhas=$((falhas + 1));;
  esac
done
[ "$falhas" -eq 0 ] && { echo "TODAS AS MUTAÇÕES PEGAS"; exit 0; }
echo "$falhas mutação(ões) não pegas ou não medidas"; exit 1
