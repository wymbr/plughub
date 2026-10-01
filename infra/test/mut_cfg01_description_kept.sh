#!/usr/bin/env bash
# mut_cfg01_description_kept.sh — o probe_cfg01_description_kept.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 o upsert volta a gravar a descrição recebida   → PUT com "" apaga a descrição (K1)
# M2 o upsert passa a NUNCA trocar a descrição      → descrição nova não pega (C1, o controle
#                                                     positivo: "mantém" virou "congela")
#
# Mutação em `db.py` do config-api, por `docker cp` + `docker restart`; `cmp` confirma que o
# arquivo mudou. O `trap` recria o container da IMAGEM (sem mutação).
# ⚠️ ASSISTIDO: reinicia o config-api duas vezes (leitores de config usam o último valor em cache).
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
CFG="${CFG_CONTAINER:-plughub-demo-config-api-1}"
PROBE=infra/test/probe_cfg01_description_kept.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate config-api >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

PKG=$(docker exec "$CFG" python -c 'import os,plughub_config_api as p;print(os.path.dirname(p.__file__))' 2>/dev/null)
[ -n "$PKG" ] || { echo "INCONCL config-api fora do ar"; exit 2; }
docker cp "$CFG:$PKG/db.py" "$TMP/orig.py" >/dev/null || { echo "INCONCL não copiou db.py"; exit 2; }

espera_subir() {
  for _ in $(seq 1 60); do
    [ "$(docker inspect --format '{{.State.Health.Status}}' "$CFG" 2>/dev/null)" = healthy ] && return 0
    sleep 2
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 = nome, $2 = par (velho ===== novo)
  python3 - "$TMP/orig.py" "$TMP/$1.py" "$2" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho, novo.rstrip("\n"), 1))
PY
  if cmp -s "$TMP/orig.py" "$TMP/$1.py"; then echo "NAO_APLICOU"; return; fi
  docker cp "$TMP/$1.py" "$CFG:$PKG/db.py" >/dev/null
  docker restart "$CFG" >/dev/null
  espera_subir || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
                description = COALESCE(NULLIF(EXCLUDED.description, ''),
                                       public.platform_config.description),
=====
                description = EXCLUDED.description,
EOF
cat > "$TMP/M2.par" <<'EOF'
                description = COALESCE(NULLIF(EXCLUDED.description, ''),
                                       public.platform_config.description),
=====
                description = public.platform_config.description,
EOF

rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for m in M1 M2; do
  rc=$(aplica "$m" "$TMP/$m.par")
  case "$rc" in
    1)           echo "  ✓ $m pega (probe VERMELHO)"; grep -E '✗' "$TMP/out" | sed 's/^/      /';;
    NAO_APLICOU) echo "  ✗ $m a mutação não se aplicou (âncora mudou?)"; falhas=$((falhas+1));;
    NAO_SUBIU)   echo "  ✗ $m o config-api não subiu com a mutação — não mediu"; falhas=$((falhas+1));;
    *)           echo "  ✗ $m SOBREVIVEU (probe rc=$rc)"; grep -E '✓|✗|INCONCL' "$TMP/out" | sed 's/^/      /'; falhas=$((falhas+1));;
  esac
done
echo "────────────────────────────────────────────────────────────────────"
[ "$falhas" = 0 ] && { echo " TODAS PEGAS"; exit 0; }
echo " $falhas SOBREVIVERAM OU NÃO MEDIRAM"; exit 1
