#!/usr/bin/env bash
# mut_att07_upload_limit.sh — o probe_att07_upload_limit.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 o reserve volta a ignorar o teto do tenant       → imagem declarada com 2 MB reserva (R1)
# M2 o POST deixa de conferir o tamanho real          → corpo de 2 MB num slot pequeno passa (R2)
# M3 o tipo lê a chave errada (imagem lê `pdf`)       → o teto de 1 MB de imagem some (R1)
#
# Mutação em `adapters/webchat.py`, `upload_router.py` e `attachment_store.py` do channel-gateway,
# por `docker cp` + `docker restart`; `cmp` confirma que o arquivo mudou. O `trap` recria o
# container da IMAGEM (sem mutação). O probe restaura o valor de config que grava.
# ⚠️ ASSISTIDO: reinicia o channel-gateway quatro vezes (chamada e chat em curso caem).
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
PROBE=infra/test/probe_att07_upload_limit.sh
TMP=$(mktemp -d)
ARQS="adapters/webchat upload_router attachment_store"
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate channel-gateway >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

PKG=$(docker exec "$GW" python -c 'import os,plughub_channel_gateway as p;print(os.path.dirname(p.__file__))' 2>/dev/null)
[ -n "$PKG" ] || { echo "INCONCL channel-gateway fora do ar"; exit 2; }
for f in $ARQS; do
  docker cp "$GW:$PKG/$f.py" "$TMP/$(basename $f).orig.py" >/dev/null || { echo "INCONCL não copiou $f.py"; exit 2; }
done

espera_subir() {
  for _ in $(seq 1 90); do
    docker exec "$GW" python -c 'import urllib.request;urllib.request.urlopen("http://localhost:8010/health",timeout=2)' >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 = nome, $2 = arquivo (relativo ao pacote, sem .py), $3 = par (velho ===== novo)
  local base; base=$(basename "$2")
  python3 - "$TMP/$base.orig.py" "$TMP/$1.py" "$3" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho, novo.rstrip("\n"), 1))
PY
  if cmp -s "$TMP/$base.orig.py" "$TMP/$1.py"; then echo "NAO_APLICOU"; return; fi
  for f in $ARQS; do docker cp "$TMP/$(basename $f).orig.py" "$GW:$PKG/$f.py" >/dev/null; done
  docker cp "$TMP/$1.py" "$GW:$PKG/$2.py" >/dev/null
  docker restart "$GW" >/dev/null
  espera_subir || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
        err = FilesystemAttachmentStore.validate_mime(req.mime_type, req.size_bytes, limit=limite)
=====
        err = FilesystemAttachmentStore.validate_mime(req.mime_type, req.size_bytes)
EOF
cat > "$TMP/M2.par" <<'EOF'
        if limite is not None and len(data) > limite:
=====
        if False:
EOF
cat > "$TMP/M3.par" <<'EOF'
    "image/jpeg": "image", "image/png": "image", "image/webp": "image", "image/gif": "image",
=====
    "image/jpeg": "pdf", "image/png": "pdf", "image/webp": "pdf", "image/gif": "pdf",
EOF

rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 adapters/webchat" "M2 upload_router" "M3 attachment_store"; do
  set -- $par
  rc=$(aplica "$1" "$2" "$TMP/$1.par")
  case "$rc" in
    1)           echo "  ✓ $1 pega (probe VERMELHO)"; grep -E '✗' "$TMP/out" | sed 's/^/      /';;
    NAO_APLICOU) echo "  ✗ $1 a mutação não se aplicou (âncora mudou?)"; falhas=$((falhas+1));;
    NAO_SUBIU)   echo "  ✗ $1 o channel-gateway não subiu com a mutação — não mediu"; falhas=$((falhas+1));;
    *)           echo "  ✗ $1 SOBREVIVEU (probe rc=$rc)"; grep -E '✓|✗|\?' "$TMP/out" | sed 's/^/      /'; falhas=$((falhas+1));;
  esac
done
echo "────────────────────────────────────────────────────────────────────"
[ "$falhas" = 0 ] && { echo " TODAS PEGAS"; exit 0; }
echo " $falhas SOBREVIVERAM OU NÃO MEDIRAM"; exit 1
