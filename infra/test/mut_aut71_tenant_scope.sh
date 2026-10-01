#!/usr/bin/env bash
# mut_aut71_tenant_scope.sh — o probe_aut71_tenant_scope.sh SABE reprovar?
#
# M0 controle: sem mutação, o probe está VERDE (senão as mutações não provam nada → 2)
# M1 `same_tenant` aceita tenant declarado diferente do token              → A1
# M2 `own_row` aceita linha de outro tenant                                → B1
# M3 SÓ a criação de usuário perde a conferência (as outras rotas ficam)   → A1
# M4 SÓ o grupo por id perde a conferência (usuários e templates ficam)    → B5
# M5 o módulo de PLATAFORMA deixa de ser recusado como tal                 → C1
#
# M3 e M4 existem porque M1/M2 derrubam a casa inteira e qualquer ramo os pegaria: o que
# importa é que o probe veja uma rota que volte a ler o tenant do corpo SOZINHA.
# NÃO MEDIDO AO VIVO (declarado): a listagem sem `?tenant_id=` voltando ao default fixo
# `"tenant_demo"` — o atacante do probe É do tenant_demo, então a mutação não muda o que ele
# vê. Guardado por `test_aut71_tenant_scope.py` (asserta o tenant passado ao banco).
#
# Mutações em `tenant_scope.py`, `router.py` e `groups_router.py` do auth-api, por `docker cp`
# + `docker restart`, `cmp` confirmando que o arquivo mudou; só conta a mutação pega pelo RAMO
# declarado. O `trap` recria o container da IMAGEM.
# ⚠️ ASSISTIDO: reinicia o auth-api (login e refresh caem por alguns segundos).
# Saída: 0 todas pegas · 1 alguma sobreviveu · 2 não mediu. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
AC="${AUTH_CONTAINER:-plughub-demo-auth-api-1}"
PROBE=infra/test/probe_aut71_tenant_scope.sh
TMP=$(mktemp -d)
restaura() {
  docker compose -p plughub-demo -f docker-compose.demo.yml up -d --force-recreate auth-api >/dev/null 2>&1
  rm -rf "$TMP"
}
trap restaura EXIT INT TERM

DIR=$(docker exec "$AC" python -c 'import plughub_auth_api, os;print(os.path.dirname(plughub_auth_api.__file__))' 2>/dev/null)
[ -n "$DIR" ] || { echo "INCONCL auth-api fora do ar"; exit 2; }
for f in tenant_scope router groups_router; do
  docker cp "$AC:$DIR/$f.py" "$TMP/$f.orig" >/dev/null 2>&1 || { echo "INCONCL não copiei $f.py"; exit 2; }
done

saude() {
  for _ in $(seq 1 120); do
    curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:3202/health | grep -q '^200$' && { sleep 2; return 0; }
    sleep 1
  done
  return 1
}
roda() { bash "$PROBE" >"$TMP/out" 2>&1; echo $?; }

aplica() {  # $1 nome  $2 arquivo (sem .py)
  python3 - "$TMP/$2.orig" "$TMP/$1.mut" "$TMP/$1.par" <<'PY'
import io, sys
src, dst, par = sys.argv[1:4]
s = io.open(src, encoding="utf-8").read()
velho, novo = io.open(par, encoding="utf-8").read().split("\n=====\n")
io.open(dst, "w", encoding="utf-8").write(s.replace(velho.rstrip("\n"), novo.rstrip("\n"), 1))
PY
  if cmp -s "$TMP/$2.orig" "$TMP/$1.mut"; then echo "NAO_APLICOU"; return; fi
  for f in tenant_scope router groups_router; do docker cp "$TMP/$f.orig" "$AC:$DIR/$f.py" >/dev/null; done
  docker cp "$TMP/$1.mut" "$AC:$DIR/$2.py" >/dev/null
  docker restart "$AC" >/dev/null
  saude || { echo "NAO_SUBIU"; return; }
  roda
}

cat > "$TMP/M1.par" <<'EOF'
    if pedido and pedido != tenant:
=====
    if False:
EOF
cat > "$TMP/M2.par" <<'EOF'
    if str(row.get("tenant_id") or "") != tenant:
=====
    if False:
EOF
cat > "$TMP/M3.par" <<'EOF'
    ts.same_tenant(claims, body.tenant_id, "criar usuario")   # AUT-71: antes de qualquer leitura
=====
    pass
EOF
cat > "$TMP/M4.par" <<'EOF'
    return ts.own_row(claims, row, "Group")
=====
    if not row:
        raise HTTPException(status_code=404, detail="Group not found")
    return row
EOF
cat > "$TMP/M5.par" <<'EOF'
    if not body.get("tenant_id"):
        logger.warning("modules RECUSA: %s tentou gravar modulo de PLATAFORMA `%s`",
=====
    if False:
        logger.warning("modules RECUSA: %s tentou gravar modulo de PLATAFORMA `%s`",
EOF

saude || { echo "INCONCL auth-api fora do ar"; exit 2; }
rc0=$(roda)
if [ "$rc0" != 0 ]; then echo "  M0 o probe não está verde sem mutação (rc=$rc0) — nada medido"; exit 2; fi
echo "  ✓ M0 controle: verde sem mutação"
falhas=0
for par in "M1 tenant_scope A1" "M2 tenant_scope B1" "M3 router A1" "M4 groups_router B5" "M5 router C1"; do
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
