#!/usr/bin/env bash
# mut_att02_attachment_internal_door.sh — o probe_att02_attachment_internal_door.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 a capacidade deixa de ser conferida ANTES do id   → id inexistente vira 404: oráculo (A2)
# M2 o escopo pela sessão some                          → domínio PB lê o anexo de PA (A3)
# M3 a recusa por capacidade não vai à trilha           → T1 conta menos recusas
#
# Mutação em `attachments.py` da analytics-api, por `docker cp` + `docker restart`; `cmp` confirma
# que o arquivo mudou. O `trap` recria o container da IMAGEM (sem mutação).
# ⚠️ ASSISTIDO: reinicia a analytics-api quatro vezes.
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
AN="${AN_CONTAINER:-plughub-demo-analytics-api-1}"
PROBE=infra/test/probe_att02_attachment_internal_door.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate analytics-api >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

PKG=$(docker exec "$AN" python -c 'import os,plughub_analytics_api as p;print(os.path.dirname(p.__file__))' 2>/dev/null)
[ -n "$PKG" ] || { echo "INCONCL analytics-api fora do ar"; exit 2; }
docker cp "$AN:$PKG/attachments.py" "$TMP/orig.py" >/dev/null || { echo "INCONCL não copiou o original"; exit 2; }

espera_subir() {
  for _ in $(seq 1 60); do
    docker exec "$AN" python -c 'import urllib.request;urllib.request.urlopen("http://localhost:3500/v1/health",timeout=2)' >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 = nome, $2 = arquivo com o par (velho, novo) separado por uma linha '====='
  python3 - "$TMP/orig.py" "$TMP/$1.py" "$2" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho, novo, 1))
PY
  if cmp -s "$TMP/orig.py" "$TMP/$1.py"; then echo "NAO_APLICOU"; return; fi
  docker cp "$TMP/$1.py" "$AN:$PKG/attachments.py" >/dev/null
  docker restart "$AN" >/dev/null
  espera_subir || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
    if principal.module_config is not None and not abac_can(
=====
    if False and principal.module_config is not None and not abac_can(
EOF
cat > "$TMP/M2.par" <<'EOF'
        await authorize_session_scope(
=====
        pass
        if False: await authorize_session_scope(
EOF
cat > "$TMP/M3.par" <<'EOF'
        await trilha("denied", file_id)
=====
        pass
EOF

rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for m in M1 M2 M3; do
  rc=$(aplica "$m" "$TMP/$m.par")
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
