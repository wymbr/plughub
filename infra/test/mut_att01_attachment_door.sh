#!/usr/bin/env bash
# mut_att01_attachment_door.sh — o probe_att01_attachment_door.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 a regra some do commit (`validate_content` devolve None)     → exige VERMELHO (W1/W2)
# M2 a porta perde os cabeçalhos (`SERVE_SECURITY_HEADERS = {}`)  → exige VERMELHO (S1/S2)
# M3 tudo inline (`content_disposition` ignora o tipo)            → exige VERMELHO (S2)
# M4 a assinatura deixa de ser conferida (ATT-03)                 → exige VERMELHO (S0/S4)
#
# Cada mutação é aplicada por `docker cp` no container e `docker restart` (o uvicorn não
# recarrega); `cmp` confirma que o arquivo mudou. O `trap` recria o container a partir da
# IMAGEM, que é a versão sem mutação. ⚠️ ASSISTIDO: reinicia o gateway cinco vezes.
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
PROBE=infra/test/probe_att01_attachment_door.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate channel-gateway >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

PKG=$(docker exec "$GW" python -c 'import os,plughub_channel_gateway as p;print(os.path.dirname(p.__file__))' 2>/dev/null)
[ -n "$PKG" ] || { echo "INCONCL gateway fora do ar"; exit 2; }
docker cp "$GW:$PKG/attachment_store.py" "$TMP/orig.py" >/dev/null || { echo "INCONCL não copiou o original"; exit 2; }

espera_subir() {
  for _ in $(seq 1 60); do
    docker exec "$GW" python -c 'import urllib.request;urllib.request.urlopen("http://localhost:8010/health",timeout=2)' >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 = nome, $2 = expressão python que transforma o texto
  python3 - "$TMP/orig.py" "$TMP/$1.py" "$2" <<'PY'
import sys, io
src, dst, expr = sys.argv[1], sys.argv[2], sys.argv[3]
s = io.open(src, encoding="utf-8").read()
t = eval(expr, {"s": s})
io.open(dst, "w", encoding="utf-8").write(t)
PY
  [ -s "$TMP/$1.py" ] || { echo "NAO_APLICOU"; return; }
  if cmp -s "$TMP/orig.py" "$TMP/$1.py"; then echo "NAO_APLICOU"; return; fi
  docker cp "$TMP/$1.py" "$GW:$PKG/attachment_store.py" >/dev/null
  docker restart "$GW" >/dev/null
  espera_subir || { echo "NAO_SUBIU"; return; }
  roda
}

falhas=0
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"

declare -A MUT=(
  [M1]='s.replace("def validate_content(artifact_class: str | None, mime_type: str, data: bytes) -> str | None:", "def validate_content(artifact_class: str | None, mime_type: str, data: bytes) -> str | None:\n    return None", 1)'
  [M2]='s.replace("SERVE_SECURITY_HEADERS = {", "SERVE_SECURITY_HEADERS = {} and {", 1)'
  [M3]='s.replace("disposition = \"inline\" if mime_type in INLINE_MIMES else \"attachment\"", "disposition = \"inline\"", 1)'
  [M4]='s.replace("    if not secret:\n        return \"no_secret\"", "    return None\n    if not secret:\n        return \"no_secret\"", 1)'
)
for m in M1 M2 M3 M4; do
  rc=$(aplica "$m" "${MUT[$m]}")
  case "$rc" in
    1)          echo "  ✓ $m pega (probe VERMELHO)";;
    NAO_APLICOU) echo "  ✗ $m a mutação não se aplicou (âncora mudou?)"; falhas=$((falhas+1));;
    NAO_SUBIU)  echo "  ✗ $m o gateway não subiu com a mutação — não mediu"; falhas=$((falhas+1));;
    *)          echo "  ✗ $m SOBREVIVEU (probe rc=$rc)"; sed 's/^/      /' "$TMP/out" | grep -E '✓|✗' ; falhas=$((falhas+1));;
  esac
done
echo "────────────────────────────────────────────────────────────────────"
[ "$falhas" = 0 ] && { echo " TODAS PEGAS"; exit 0; }
echo " $falhas SOBREVIVERAM OU NÃO MEDIRAM"; exit 1
