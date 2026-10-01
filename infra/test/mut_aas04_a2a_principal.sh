#!/usr/bin/env bash
# mut_aas04_a2a_principal.sh — o probe_aas04_a2a_principal.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 a introspecção aceita principal DESATIVADO            → D3 / D4
# M2 conceder pool deixa de exigir que se detenha o pool   → A5
# M3 conceder pool deixa de conferir que ele expõe A2A     → A3 / A4
# M4 a credencial é gravada em CLARO                       → A2
# M5 a porta deixa de exigir o pool em `allowed_pools`     → C4
# M6 a porta aceita a resposta da introspecção sem `active`→ C6
#
# M1–M4 em `agent_principals.py` do auth-api; M5 em `main.py` e M6 em `a2a_principal.py` do
# channel-gateway — por `docker cp` + `docker restart`, `cmp` confirmando que o arquivo mudou. O
# `trap` recria os dois containers da IMAGEM.
# NÃO MEDIDO: a recusa de credencial de OUTRO tenant na porta — o probe não a mede ao vivo (ver o
# cabeçalho dele); quem a guarda é `test_aas04_a2a_principal.py`.
# ⚠️ ASSISTIDO: reinicia o auth-api (login cai por segundos) e o channel-gateway (chamada e chat em
# curso caem); cada rodada espera 31 s pelo cache — a bateria leva uns 7 minutos.
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
AUC="${AUTH_CONTAINER:-plughub-demo-auth-api-1}"
GWC="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
PROBE=infra/test/probe_aas04_a2a_principal.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate auth-api channel-gateway >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

AUPATH=$(docker exec "$AUC" python -c 'import plughub_auth_api.agent_principals as m;print(m.__file__)' 2>/dev/null)
GWPKG=$(docker exec "$GWC" python -c 'import os,plughub_channel_gateway as p;print(os.path.dirname(p.__file__))' 2>/dev/null)
[ -n "$AUPATH" ] && [ -n "$GWPKG" ] || { echo "INCONCL auth-api ou channel-gateway fora do ar"; exit 2; }
ALVOS=("$AUC|$AUPATH" "$GWC|$GWPKG/main.py" "$GWC|$GWPKG/a2a_principal.py")
i=0
for a in "${ALVOS[@]}"; do
  docker cp "${a%%|*}:${a#*|}" "$TMP/$i.orig" >/dev/null 2>&1 || { echo "INCONCL não copiou ${a#*|}"; exit 2; }
  i=$((i + 1))
done

saude() {
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:3202/health | grep -q '^200$' \
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
  docker restart "$AUC" "$GWC" >/dev/null
  saude || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
    if not row["active"]:
=====
    if False:
EOF
cat > "$TMP/M2.par" <<'EOF'
    fora = grants.violacoes(claims, accessible_pools=pools)
=====
    fora = []
EOF
cat > "$TMP/M3.par" <<'EOF'
        motivo = await _pool_exposes_a2a_to_partner(settings, tenant_id, p)
=====
        motivo = None
EOF
cat > "$TMP/M4.par" <<'EOF'
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()
=====
    return plain
EOF
cat > "$TMP/M5.par" <<'EOF'
    if ep.pool_id not in p.allowed_pools:
=====
    if False:
EOF
cat > "$TMP/M6.par" <<'EOF'
    if body.get("active") is True and body.get("tenant_id") and body.get("sub"):
=====
    if body.get("tenant_id") or True:
EOF

saude || { echo "INCONCL auth-api ou gateway fora do ar"; exit 2; }
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 0" "M2 0" "M3 0" "M4 0" "M5 1" "M6 2"; do
  set -- $par
  rc=$(aplica "$1" "$2" "$TMP/$1.par")
  case "$rc" in
    1) echo "  ✓ $1 pega: $(grep -E '^  FALHA' "$TMP/out" | head -2 | sed 's/^ *//' | cut -c1-160 | tr '\n' ' ')";;
    NAO_APLICOU) echo "  ? $1 a mutação não se aplicou (o fonte mudou?) — não mede"; falhas=$((falhas + 1));;
    NAO_SUBIU) echo "  ? $1 o serviço não subiu com a mutação — não mede"; falhas=$((falhas + 1));;
    *) echo "  ✗ $1 SOBREVIVEU (probe rc=$rc)"; falhas=$((falhas + 1));;
  esac
done
[ "$falhas" -eq 0 ] && { echo "TODAS AS MUTAÇÕES PEGAS"; exit 0; }
echo "$falhas mutação(ões) não pegas ou não medidas"; exit 1
