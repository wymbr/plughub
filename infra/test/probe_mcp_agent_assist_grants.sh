#!/usr/bin/env bash
# ==============================================================================
# probe_mcp_agent_assist_grants.sh — as rotas do Console decidem por GRANT, não por PAPEL
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# As 17 rotas REST do `mcp-server-plughub` que servem o Console/Agent-Assist
# autorizam pelo campo ABAC que cada uma declara (`agent_assist.atender` ou
# `agent_assist.supervisionar`), e **não** por uma allowlist de papéis.
#
# POR QUE ELE EXISTE (AUT-38, 2026-09-08)
# ---------------------------------------
# O guard anterior (`requireJwtRole`) lia `payload["role"] ?? roles[0]`. O primeiro
# é um claim que a auth-api NÃO emite (ela emite `roles`, array), então ele decidia
# sempre pelo PRIMEIRO papel da lista — a autorização dependia da ORDEM em que os
# papéis foram digitados. Medido ao vivo antes da troca, mesma rota, mesmos papéis:
#
#     roles=["admin","devops"]  -> passa
#     roles=["devops","admin"]  -> 403 Insufficient role
#
# `admin@` tem `{admin,developer}` e passava por sorte de ordenação. E o modo de
# falha é o pior: quem perde acesso lê *"Insufficient role"* nomeando um papel que
# ele TEM.
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   A  o arquivo volta a decidir por PAPEL (qualquer leitor de `role`/`roles[0]`
#      numa decisão de autorização);
#   B  o campo declarado NÃO abre a rota (regressão da troca);
#   C  o campo VIZINHO abre a rota (o split `atender` × `supervisionar` seria só
#      declaração);
#   D  papel graúdo SEM grant abre a rota (o bypass por papel voltou);
#   E  a ORDEM dos papéis muda o veredicto (o defeito original).
#
# ⚠️ O ramo B é o que impede a "correção" mais barata e mais errada: remover o guard.
# Sem ele, um `mcp-server` com as 17 rotas apenas atrás de credencial passaria em
# A, C, D e E — todos os negativos — e o probe ficaria verde sobre um buraco.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
MCP="${MCP:-http://localhost:3100}"
TENANT="${TENANT:-tenant_demo}"
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
DC="${DC:-docker compose -f docker-compose.demo.yml}"
SERVER_TS="$ROOT/packages/mcp-server-plughub/src/server.ts"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; fail=1; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }
sec()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

printf '\033[1mprobe: as rotas do Console decidem por GRANT, nao por PAPEL\033[0m\n'
printf '  mcp-server: %s   tenant: %s\n' "$MCP" "$TENANT"

# ── A. ESTÁTICA — o arquivo não volta a decidir por papel ────────────────────
sec "A. o server.ts nao le PAPEL para autorizar"
[ -f "$SERVER_TS" ] || { inc "server.ts nao encontrado em $SERVER_TS"; exit 2; }

# `roles` como ARRAY continua legítimo e é usado de propósito (mascaramento por
# papel do VISUALIZADOR, que é outra pergunta). O que não pode voltar é o
# `roles[0]` — o índice zero é a assinatura do defeito.
LEITORES=$(grep -n 'roles"\] as string\[\]\( | undefined\)\?)\?\??\.\[0\]' "$SERVER_TS" || true)
RESTOS=$(grep -n 'requireJwtRole(' "$SERVER_TS" | grep -v '^\s*[0-9]*: *\*' | grep -v '//' || true)
if [ -n "$LEITORES" ]; then
  bad "voltou a existir leitor de \`roles[0]\` em decisao de autorizacao:"
  printf '%s\n' "$LEITORES" | sed 's/^/                 /'
else
  ok "nenhum \`roles[0]\` no arquivo (o array inteiro, para mascaramento, e legitimo)"
fi
if [ -n "$RESTOS" ]; then
  bad "\`requireJwtRole\` voltou a ser CHAMADO:"
  printf '%s\n' "$RESTOS" | sed 's/^/                 /'
else
  ok "\`requireJwtRole\` nao tem chamador (foi removido, nao aposentado)"
fi

# Cobertura: toda chamada do guard novo declara um campo `agent_assist.*`.
N_GUARD=$(grep -c 'requireJwtGrant(req.headers.authorization' "$SERVER_TS" || true)
N_CAMPO=$(grep -c 'requireJwtGrant(req.headers.authorization, "agent_assist"' "$SERVER_TS" || true)
if [ "$N_GUARD" -lt 17 ]; then
  bad "so $N_GUARD chamadas do guard — eram 17 rotas; alguma perdeu o portao?"
elif [ "$N_GUARD" != "$N_CAMPO" ]; then
  bad "$((N_GUARD - N_CAMPO)) chamada(s) do guard fora do modulo agent_assist — classificar"
else
  ok "as $N_GUARD chamadas do guard declaram campo de \`agent_assist\`"
fi

# ── setup ao vivo ───────────────────────────────────────────────────────────
mint() {  # $1 = roles JSON ; $2 = module_config JSON
  $DC exec -T auth-api python - "$JWT_SECRET" "$1" "$2" <<'PY' 2>/dev/null | tr -d '\r' | tail -n1
import sys, json, time, hmac, hashlib, base64
def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
secret, roles, mc = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
now = int(time.time())
h = b64(json.dumps({"alg":"HS256","typ":"JWT"}, separators=(",",":")).encode())
p = b64(json.dumps({"sub":"probe_aut38","tenant_id":"tenant_demo","roles":roles,
                    "module_config": mc, "accessible_pools": ["sac_ia"],
                    "iat": now, "exp": now+3600}, separators=(",",":")).encode())
sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
print(f"{h}.{p}.{b64(sig)}")
PY
}

ATENDER='{"agent_assist":{"atender":{"access":"read_write","scope":[]}}}'
SUPERV='{"agent_assist":{"supervisionar":{"access":"read_write","scope":[]}}}'
AMBOS='{"agent_assist":{"atender":{"access":"read_write","scope":[]},"supervisionar":{"access":"read_write","scope":[]}}}'

T_ATENDE=$(mint '["operator"]' "$ATENDER")
T_SUPER=$(mint  '["supervisor"]' "$SUPERV")
T_AMBOS1=$(mint '["admin","devops"]' "$AMBOS")
T_AMBOS2=$(mint '["devops","admin"]' "$AMBOS")
T_SOPAPEL=$(mint '["admin","supervisor"]' '{}')
for v in T_ATENDE T_SUPER T_AMBOS1 T_AMBOS2 T_SOPAPEL; do
  eval "t=\$$v"
  case "$t" in *.*.*) ;; *) inc "nao consegui cunhar $v (auth-api no ar?)"; exit 2;; esac
done

SID="probe_aut38_sessao_inexistente"
code() { curl -s -o /tmp/_aut38_body -w '%{http_code}' --max-time 15 "$@"; }
# 401/403 = barrado pelo portao. Qualquer outro codigo (404, 200, 400) = ATRAVESSOU —
# a sonda e uma sessao que nao existe, entao o handler responde sem efeito colateral.
passou() { case "$1" in 401|403) return 1 ;; *) return 0 ;; esac; }

R_LIST="$MCP/api/work_queue/list?tenant_id=$TENANT"
R_FC="$MCP/api/force-complete/$SID"
post() { code -X POST "$1" -H 'Content-Type: application/json' -H "Authorization: Bearer $2" -d '{}'; }

# ── B/C — cada rota responde ao SEU campo, e recusa o vizinho ───────────────
sec "B/C. cada rota responde ao SEU campo (e recusa o vizinho)"
c=$(code "$R_LIST" -H "Authorization: Bearer $T_ATENDE")
passou "$c" && ok "work_queue/list com \`atender\`: passou ($c)" \
             || bad "work_queue/list RECUSOU quem tem \`atender\` ($c)"
c=$(code "$R_LIST" -H "Authorization: Bearer $T_SUPER")
passou "$c" && bad "work_queue/list aceitou \`supervisionar\` — o split e so declaracao ($c)" \
             || ok "work_queue/list recusa \`supervisionar\` (o vizinho nao serve): $c"
c=$(post "$R_FC" "$T_SUPER")
passou "$c" && ok "force-complete com \`supervisionar\`: passou ($c)" \
             || bad "force-complete RECUSOU quem tem \`supervisionar\` ($c)"
c=$(post "$R_FC" "$T_ATENDE")
passou "$c" && bad "force-complete aceitou \`atender\` — o split e so declaracao ($c)" \
             || ok "force-complete recusa \`atender\` (o vizinho nao serve): $c"

# ── D — papel graúdo SEM grant não abre nada ────────────────────────────────
sec "D. papel graudo com ZERO grants nao abre nada (grant-first)"
c=$(code "$R_LIST" -H "Authorization: Bearer $T_SOPAPEL")
passou "$c" && bad "roles=[admin,supervisor] SEM grants entrou em work_queue/list ($c)" \
             || ok "roles=[admin,supervisor] sem grants: recusado ($c)"
DET=$(cat /tmp/_aut38_body 2>/dev/null)
case "$DET" in
  *agent_assist.atender*) ok "a recusa NOMEIA o campo que falta" ;;
  *) bad "a recusa nao nomeia o campo: $(printf '%s' "$DET" | head -c 90)" ;;
esac

# ── E — a ordem dos papéis não decide mais ──────────────────────────────────
sec "E. a ORDEM dos papeis nao decide mais (era o defeito original)"
c1=$(post "$R_FC" "$T_AMBOS1")
c2=$(post "$R_FC" "$T_AMBOS2")
if [ "$c1" = "$c2" ]; then
  ok "roles=[admin,devops] e [devops,admin] dao o MESMO veredicto ($c1)"
else
  bad "a ordem ainda decide: [admin,devops]=$c1 x [devops,admin]=$c2"
  info "Era exatamente isto que o \`roles[0]\` fazia."
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m - as rotas do Console decidem por grant, e o papel nao abre porta.\n'
else
  printf '\033[31mVERMELHO\033[0m - ver secoes acima.\n'
fi
exit "$fail"
