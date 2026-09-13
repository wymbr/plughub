#!/usr/bin/env bash
# probe_identity_provenance.sh — 2026-09-13  (PID-12)
#
# PERGUNTA: `authoritative` tem UMA porta, e ela exige credencial?
#
# A DECISAO QUE ELE PROTEGE
#   O dono decidiu em 2026-09-12: a IMPORTACAO com credencial de admin e a unica
#   porta que carimba procedencia `authoritative` na ancora do cliente. E contra
#   essa procedencia que o OTP vai poder ser emitido (PID-10) — uma segunda porta,
#   ou uma porta sem credencial, faria o OTP confiar numa base que qualquer um
#   escreve. Ate aqui nenhum dos escritores carimbava procedencia alguma; o eixo
#   nao existia (IDN-07).
#
# QUATRO RAMOS
#   A  CENSO (AST, channel-gateway inteiro fora testes) — o literal
#      `authoritative` so existe na definicao da constante (e dentro do SQL do
#      upsert), e a constante so e passada como ARGUMENTO dentro de
#      `import_customers`. Escritor novo que a passe reprova aqui antes de rodar.
#   B  PORTAO AO VIVO — sem Bearer 401; operador (sem o campo) 403; e o controle
#      POSITIVO: o admin importa. Sem o positivo, os negativos passariam por uma
#      rota que recusa todo mundo.
#   C  SEMANTICA NO POSTGRES — importar cria e carimba; o resolve casa o importado;
#      reimportar atualiza sem duplicar; conflito de ancora RECUSA; um escritor
#      comum que reatribui a ancora a outro cliente ZERA o `authoritative`; e um
#      escritor comum pedindo `authoritative` levanta.
#   D  MUTACAO — o mesmo exercicio com o upsert que MANTEM a procedencia na
#      reatribuicao TEM de reprovar o caso da reatribuicao; e com a regra antiga do
#      `possessed` (IDN-09), o caso da posse tambem; e com a leitura que nao confere
#      o cliente (IDN-07), o caso da divergencia.
#
# ⚠️ IDN-07 (2026-09-13): a LEITURA vem SO do Postgres — `authoritative` so nasce
#    la, e copiar a procedencia no Redis criaria a segunda casa que a IDN-09 mediu
#    divergindo. E ela confere o cliente: o indice Redis pode apontar a ancora para
#    quem o cadastro nao reconhece (IDN-10), e ai a leitura emprestaria confianca.
#
# ⚠️ IDN-09 (2026-09-13): a posse provada e a MESMA pergunta com outro campo — a
#    confianca da ancora e fato do par (ancora, CLIENTE). O upsert mantinha
#    `possessed` e `verified_at` quando a ancora mudava de cliente, no Postgres,
#    enquanto o indice Redis ja zerava. O ramo C passou a exigir que PG e Redis
#    CONCORDEM depois da reatribuicao.
#
# ⚠️ O que ele NAO prova: as rotas irmas seguem sem credencial (IDN-06), e a
#    leitura da procedencia pelo OTP ainda nao existe (PID-10). Aqui se prova que
#    ninguem ALEM da importacao escreve `authoritative`, nao que alguem o leia.
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
AUTH="${AUTH_URL:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"
OP_EMAIL="${OP_EMAIL:-operator@plughub.local}"; OP_PASS="${OP_PASS:-changeme_operator}"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " \`authoritative\` tem UMA porta, e ela exige credencial?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
SAIDA=$(python3 - <<'PYEOF'
import ast, io, os
B = "packages/channel-gateway/src/plughub_channel_gateway"
erros, usos = [], 0
for raiz, dirs, fs in os.walk(B):
    dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__")]
    for f in fs:
        if not f.endswith(".py"):
            continue
        p = os.path.join(raiz, f)
        arv = ast.parse(io.open(p, encoding="utf-8").read())
        dono = {}
        for fn in ast.walk(arv):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(fn):
                    dono.setdefault(id(n), fn.name)
        definicao = set()
        for n in ast.walk(arv):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PROVENANCE_AUTHORITATIVE" for t in n.targets):
                definicao.add(id(n.value))
        for n in ast.walk(arv):
            if isinstance(n, ast.Constant) and n.value == "authoritative" and id(n) not in definicao:
                erros.append("%s:%d literal `authoritative` fora da constante" % (p, n.lineno))
            if isinstance(n, ast.Call):
                for a in list(n.args) + [k.value for k in n.keywords]:
                    if isinstance(a, ast.Name) and a.id == "PROVENANCE_AUTHORITATIVE":
                        usos += 1
                        if dono.get(id(n)) != "import_customers":
                            erros.append("%s:%d `authoritative` passado fora de import_customers (em %s)"
                                         % (p, n.lineno, dono.get(id(n), "<modulo>")))
if usos == 0:
    erros.append("nenhuma chamada carimba `authoritative` — a porta de importacao sumiu")
print(("ERRO " + " ; ".join(erros)) if erros else "OK %d" % usos)
PYEOF
)
case "$SAIDA" in
  OK*) ok "\`authoritative\` so e passado dentro de import_customers (${SAIDA#OK } uso)" ;;
  *)   falha "$SAIDA" ;;
esac

# ── B/C/D ────────────────────────────────────────────────────────────────────
login() {
  curl -s -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
    | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("access_token",""))
except Exception: print("")'
}
T_ADM=$(login "$AD_EMAIL" "$AD_PASS")
T_OPE=$(login "$OP_EMAIL" "$OP_PASS")

exercicio() {
  docker exec -i -e T_ADM="$T_ADM" -e T_OPE="$T_OPE" "$GW" python - "$@" \
    < infra/test/_identity_provenance_exercise.py 2>/dev/null | tail -1
}
julga() {
  printf '%s' "$1" | python3 -c '
import json, sys
nomes = sys.argv[1].split(",")
try:
    c = json.loads(sys.stdin.read())["casos"]
except Exception:
    print("SEM"); sys.exit()
faltam = [n for n in nomes if n not in c]
if faltam:
    print("SEM " + ",".join(faltam)); sys.exit()
ruins = [n for n in nomes if not c[n]]
print("OK" if not ruins else "FALHA " + ",".join(ruins))' "$2"
}

if [ -z "$T_ADM" ] || [ -z "$T_OPE" ]; then
  echo ""
  incon "login falhou (admin=${#T_ADM} operador=${#T_OPE} chars) — B, C e D nao medidos"
else
  JSON=$(exercicio)
  echo ""
  echo "── B · PORTAO AO VIVO ─────────────────────────────────────────────────"
  V=$(julga "$JSON" "rota_401,rota_403,import_cria")
  case "$V" in
    OK)   ok "sem Bearer 401 · operador 403 · admin importa (controle positivo)" ;;
    SEM*) incon "exercicio sem veredicto: ${V#SEM} — $JSON" ;;
    *)    falha "${V#FALHA } — $JSON" ;;
  esac

  echo ""
  echo "── C · SEMANTICA NO POSTGRES ──────────────────────────────────────────"
  V=$(julga "$JSON" "import_cria,resolve_casa,reimport_idem,conflito_recusa,reatribui_zera,escritor_recusa,posse_antes,reatribui_posse")
  case "$V" in
    OK)   ok "cria e carimba · resolve casa · reimporta sem duplicar · conflito recusa · reatribuicao zera procedencia E posse (PG e Redis concordam) · escritor comum levanta" ;;
    SEM*) incon "exercicio sem veredicto: ${V#SEM}" ;;
    *)    falha "${V#FALHA } — $JSON" ;;
  esac
  V=$(julga "$JSON" "resolve_expoe,le_via_redis,le_via_duravel,divergencia_nao_empresta,anchor_provenance_confere")
  case "$V" in
    OK)   ok "LEITURA (IDN-07): resposta HTTP expoe · Redis quente e cadastro frio devolvem a procedencia · Redis divergente NAO empresta · a pergunta da PID-10 responde por (ancora, cliente)" ;;
    SEM*) incon "exercicio sem veredicto: ${V#SEM}" ;;
    *)    falha "${V#FALHA } — $JSON" ;;
  esac

  echo ""
  echo "── D · MUTACAO ────────────────────────────────────────────────────────"
  JSON=$(exercicio --mutar-sql)
  V=$(printf '%s' "$JSON" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    print("SEM"); sys.exit()
if not d.get("mutacao_aplicada"):
    print("SEM a mutacao nao casou com o SQL"); sys.exit()
c = d["casos"]
print("OK" if c.get("reatribui_zera") is False and c.get("import_cria") else "FALHA %s" % c)')
  case "$V" in
    OK)   ok "com o upsert que mantem a procedencia, a reatribuicao HERDA \`authoritative\` e o caso reprova" ;;
    SEM*) incon "${V#SEM }" ;;
    *)    falha "a mutacao passou — o caso da reatribuicao nao mede a regra: ${V#FALHA }" ;;
  esac

  JSON=$(exercicio --mutar-posse)
  V=$(printf '%s' "$JSON" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    print("SEM"); sys.exit()
if not d.get("mutacao_posse_aplicada"):
    print("SEM a mutacao da posse nao casou com o SQL"); sys.exit()
c = d["casos"]
print("OK" if c.get("posse_antes") and c.get("reatribui_posse") is False and c.get("reatribui_zera") else "FALHA %s" % c)')
  case "$V" in
    OK)   ok "com a regra antiga do \`possessed\`, a reatribuicao HERDA a posse e o caso reprova (IDN-09)" ;;
    SEM*) incon "${V#SEM }" ;;
    *)    falha "a mutacao da posse passou — o caso nao mede a regra: ${V#FALHA }" ;;
  esac

  JSON=$(exercicio --mutar-leitura)
  V=$(printf '%s' "$JSON" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    print("SEM"); sys.exit()
if not d.get("mutacao_leitura_aplicada"):
    print("SEM a mutacao da leitura nao foi aplicada"); sys.exit()
c = d["casos"]
print("OK" if c.get("le_via_redis") and c.get("divergencia_nao_empresta") is False else "FALHA %s" % c)')
  case "$V" in
    OK)   ok "sem conferir o cliente, a leitura EMPRESTA \`authoritative\` ao prospect e o caso reprova (IDN-07)" ;;
    SEM*) incon "${V#SEM }" ;;
    *)    falha "a mutacao da leitura passou — o caso nao mede a regra: ${V#FALHA }" ;;
  esac
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " RESULTADO: FALHA em $FALHA verificacao(oes)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " RESULTADO: $INCONCL INCONCLUSIVO(s), nenhum vermelho"; exit 3; fi
echo " RESULTADO: OK"
exit 0
