#!/usr/bin/env bash
# mut_att06_attachment_view.sh — o probe_att06_attachment_view.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 todo mundo "atende"                          → quem não atende recebe o original (V2)
# M2 o papel deixa de ser conferido               → supervisor no roster vê nítido (V3)
# M3 a prévia pede o ORIGINAL ao gateway          → "borrado" com os bytes do original (V2)
# M4 revelar vai à trilha como acesso comum       → `revealed` some da trilha (T1)
# M5 não-imagem tratada como imagem               → o PDF não pede revelar (V5)
# M6 a transcrição volta a levar o link gravado   → o original abre fora da porta (S1)
#
# Mutação em `attachments.py` (M1–M5) e `sessions.py` (M6) da analytics-api, por `docker cp` + `docker restart`; `cmp` confirma
# que o arquivo mudou. O `trap` recria o container da IMAGEM (sem mutação).
# ⚠️ ASSISTIDO: reinicia a analytics-api seis vezes.
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
AN="${AN_CONTAINER:-plughub-demo-analytics-api-1}"
PROBE=infra/test/probe_att06_attachment_view.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate analytics-api >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

PKG=$(docker exec "$AN" python -c 'import os,plughub_analytics_api as p;print(os.path.dirname(p.__file__))' 2>/dev/null)
[ -n "$PKG" ] || { echo "INCONCL analytics-api fora do ar"; exit 2; }
for f in attachments sessions; do
  docker cp "$AN:$PKG/$f.py" "$TMP/$f.orig.py" >/dev/null || { echo "INCONCL não copiou $f.py"; exit 2; }
done

espera_subir() {
  for _ in $(seq 1 60); do
    docker exec "$AN" python -c 'import urllib.request;urllib.request.urlopen("http://localhost:3500/v1/health",timeout=2)' >/dev/null 2>&1 && return 0
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
io.open(dst, "w", encoding="utf-8").write(s.replace(velho, novo, 1))
PY
  if cmp -s "$TMP/$2.orig.py" "$TMP/$1.py"; then echo "NAO_APLICOU"; return; fi
  # cada mutação parte dos DOIS originais: só o arquivo da vez muda
  for f in attachments sessions; do docker cp "$TMP/$f.orig.py" "$AN:$PKG/$f.py" >/dev/null; done
  docker cp "$TMP/$1.py" "$AN:$PKG/$2.py" >/dev/null
  docker restart "$AN" >/dev/null
  espera_subir || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
    if principal.module_config is None or await _attends(request.app.state.redis, session_id, actor_sub):
=====
    if True:
EOF
cat > "$TMP/M2.par" <<'EOF'
               and p.get("role") in _ATTENDING_ROLES for p in roster)
=====
               for p in roster)
EOF
cat > "$TMP/M3.par" <<'EOF'
        view, ok_result, variant = "blurred", "ok_blurred", "blurred"
=====
        view, ok_result, variant = "blurred", "ok_blurred", None
EOF
cat > "$TMP/M4.par" <<'EOF'
        view, ok_result, variant = "revealed", "revealed", None
=====
        view, ok_result, variant = "revealed", "ok", None
EOF
cat > "$TMP/M5.par" <<'EOF'
    elif str(meta.get("mime_type") or "").startswith("image/"):
=====
    elif True:
EOF

cat > "$TMP/M6.par" <<'EOF'
    return {k: v for k, v in att.items() if k != "url"}
=====
    return dict(att)
EOF

rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 attachments" "M2 attachments" "M3 attachments" "M4 attachments" "M5 attachments" "M6 sessions"; do
  set -- $par; m=$1
  rc=$(aplica "$m" "$2" "$TMP/$m.par")
  case "$rc" in
    1)           echo "  ✓ $m pega (probe VERMELHO)";;
    NAO_APLICOU) echo "  ✗ $m a mutação não se aplicou (âncora mudou?)"; falhas=$((falhas+1));;
    NAO_SUBIU)   echo "  ✗ $m a analytics-api não subiu com a mutação — não mediu"; falhas=$((falhas+1));;
    *)           echo "  ✗ $m SOBREVIVEU (probe rc=$rc)"; grep -E '✓|✗|\?' "$TMP/out" | sed 's/^/      /'; falhas=$((falhas+1));;
  esac
done
echo "────────────────────────────────────────────────────────────────────"
[ "$falhas" = 0 ] && { echo " TODAS PEGAS"; exit 0; }
echo " $falhas SOBREVIVERAM OU NÃO MEDIRAM"; exit 1
