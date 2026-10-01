#!/usr/bin/env bash
# mut_att05_ingest_pipeline.sh — o probe_att05_ingest_pipeline.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 a porta serve o que o antivírus não liberou           → quarentena sai 200 (Q1)
# M2 a imagem deixa de ser re-codificada                   → EXIF e carga chegam ao store (X1/X2)
# M3 "não deu para perguntar" vira limpo (fail-open)        → quarentena nunca acontece (Q1)
# M4 o infectado deixa de ser recusado no commit            → EICAR gravado (V1)
# M5 a nova varredura não libera a quarentena               → segue 423 depois do clamd voltar (Q2)
#
# Mutação em `attachment_store.py` / `attachment_rescan.py` do channel-gateway, por `docker cp` +
# `docker restart` (a porta roda no serviço; o commit e a varredura, no processo do probe — os dois
# importam o arquivo mutado); `cmp` confirma que o arquivo mudou. O `trap` recria o container da
# IMAGEM (sem mutação).
# ⚠️ ASSISTIDO: reinicia o channel-gateway seis vezes (chamada e chat em curso caem).
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
PROBE=infra/test/probe_att05_ingest_pipeline.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate channel-gateway >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

PKG=$(docker exec "$GW" python -c 'import os,plughub_channel_gateway as p;print(os.path.dirname(p.__file__))' 2>/dev/null)
[ -n "$PKG" ] || { echo "INCONCL channel-gateway fora do ar"; exit 2; }
for f in attachment_store attachment_rescan; do
  docker cp "$GW:$PKG/$f.py" "$TMP/$f.orig.py" >/dev/null || { echo "INCONCL não copiou $f.py"; exit 2; }
done

espera_subir() {
  for _ in $(seq 1 90); do
    docker exec "$GW" python -c 'import urllib.request;urllib.request.urlopen("http://localhost:8010/health",timeout=2)' >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 = nome, $2 = módulo, $3 = arquivo com o par (velho, novo) separado por '====='
  python3 - "$TMP/$2.orig.py" "$TMP/$1.py" "$3" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho, novo.rstrip("\n") + ("\n" if velho.endswith("\n") else ""), 1))
PY
  if cmp -s "$TMP/$2.orig.py" "$TMP/$1.py"; then echo "NAO_APLICOU"; return; fi
  # cada mutação parte dos DOIS originais: só o arquivo da vez muda
  for f in attachment_store attachment_rescan; do
    docker cp "$TMP/$f.orig.py" "$GW:$PKG/$f.py" >/dev/null
  done
  docker cp "$TMP/$1.py" "$GW:$PKG/$2.py" >/dev/null
  docker restart "$GW" >/dev/null
  espera_subir || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
    if status == "clean":
        return None
=====
    if True:
        return None
EOF
cat > "$TMP/M2.par" <<'EOF'
    if is_image(mime_type):
        data = sanitize_image(data, mime_type)
=====
    if False:
        data = sanitize_image(data, mime_type)
EOF
cat > "$TMP/M3.par" <<'EOF'
    return "quarantined", r.reason
=====
    return "clean", ""
EOF
cat > "$TMP/M4.par" <<'EOF'
    if status == "infected":
        raise AttachmentInfected(f"antivirus: {motivo}")
=====
    if False:
        raise AttachmentInfected(f"antivirus: {motivo}")
EOF
cat > "$TMP/M5.par" <<'EOF'
                    SET    scan_status = 'clean', sha256 = COALESCE(sha256, $2),
=====
                    SET    scan_status = 'quarantined', sha256 = COALESCE(sha256, $2),
EOF

rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 attachment_store" "M2 attachment_store" "M3 attachment_store" "M4 attachment_store" "M5 attachment_rescan"; do
  set -- $par
  rc=$(aplica "$1" "$2" "$TMP/$1.par")
  case "$rc" in
    1)           echo "  ✓ $1 pega (probe VERMELHO)"; grep -E '✗' "$TMP/out" | sed 's/^/      /';;
    NAO_APLICOU) echo "  ✗ $1 a mutação não se aplicou (âncora mudou?)"; falhas=$((falhas+1));;
    NAO_SUBIU)   echo "  ✗ $1 o channel-gateway não subiu com a mutação — não mediu"; falhas=$((falhas+1));;
    *)           echo "  ✗ $1 SOBREVIVEU (probe rc=$rc)"; grep -E '✓|✗|INCONCL' "$TMP/out" | sed 's/^/      /'; falhas=$((falhas+1));;
  esac
done
echo "────────────────────────────────────────────────────────────────────"
[ "$falhas" = 0 ] && { echo " TODAS PEGAS"; exit 0; }
echo " $falhas SOBREVIVERAM OU NÃO MEDIRAM"; exit 1
