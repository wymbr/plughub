#!/usr/bin/env bash
# ==============================================================================
# probe_ts_scope_resolvers.sh — as duas copias TS do resolvedor de escopo
# ==============================================================================
#
# POR QUE ESTE PORTAO EXISTE, e por que o `probe_accessible_pools_scope.sh` nao
# bastava: aquele e um censo AST sobre PYTHON. As duas copias TypeScript do
# resolvedor de escopo de pool nao aparecem nele, nem no
# `probe_authz_single_verifier.sh` (que conta quem DECODIFICA JWT — estas duas
# consomem claims ja decodificados). Terceiro eixo, terceiro censo cego.
#
# Custo medido dessa cegueira (2026-08-31, AUT-23): a AUT-03 virou o py-authz para
# `[] = NENHUM pool` e deixou as duas copias TS no ramo legado por um dia. Python e
# TypeScript passaram a discordar sobre o que `accessible_pools: []` significa — a
# mesma conta recebia 0 linhas no analytics e os 36 pools do tenant no Monitor.
# Junto, um segundo ramo: no `agent-registry` o early-return de "sem header
# Authorization" ficava ANTES do `try` e devolvia irrestrito, CALADO — e por isso o
# ramo "sem header" da mensagem de log era INALCANCAVEL. Log que nao pode imprimir
# e da mesma familia do teste que nao pode reprovar.
#
# DUAS METADES, e nenhuma substitui a outra:
#   (A) VIVA — mede o comportamento nas duas rotas. E a unica que prova o que o
#       binario faz. Exige a stack de pe; SEM ela o veredicto e INCONCLUSIVO,
#       nunca verde (passar por ausencia de amostra e o defeito do catalogo).
#   (B) ESTATICA — o marcador do ramo legado nao pode voltar ao FONTE. A metade
#       viva so pegaria isso depois de um rebuild; a estatica pega no commit.
#
# CONTROLE POSITIVO OBRIGATORIO: se `admin@` (36 pools) nao enxergar nada, os zeros
# dos ramos negativos nao provam escopo — provam servico fora do ar. Por isso o
# controle positivo decide INCONCLUSIVO antes de qualquer ramo negativo rodar.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
# ⚠️ UTF-8 explicito na SAIDA do python. No Windows o `stdout` decodifica com cp1252 e
# um `print` de texto acentuado estoura `UnicodeEncodeError`, derrubando o probe por
# motivo de bancada — ou, pior, mutila o texto que o shell vai comparar.
#
# ⚠️ E o que esta linha NAO conserta, porque o diagnostico foi REFEITO em 2026-09-02:
# a corrupcao que motivou a CNS-12 nao vinha do `sys.stdin` (medido: `curl | python3 ->
# arquivo` preserva `Almoco`/`Reuniao` intactos). Vinha da VARIAVEL DE SHELL — passar
# JSON nao-ASCII por `VAR=$(…)` o mutila, medido 321 bytes contra 325. Contra isso a
# unica defesa e nao passar por variavel: producao e consumo por ARQUIVO.
export PYTHONIOENCODING=utf-8

set -uo pipefail

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

AUTH=${AUTH:-http://localhost:3202/auth}
TENANT=${TENANT:-tenant_demo}
REGISTRY=${REGISTRY:-http://localhost:3300}
MCP=${MCP:-http://localhost:3100}

ADMIN_EMAIL=${ADMIN_EMAIL:-admin@plughub.local}
ADMIN_PASS=${ADMIN_PASS:-changeme_admin}
# Fixture de escopo VAZIO. Precisa existir e precisa mesmo ter `[]` — conferido
# abaixo, senao um probe@ com pools faria os ramos negativos passarem pelo motivo
# errado.
VAZIO_EMAIL=${VAZIO_EMAIL:-probe@plughub.local}
VAZIO_PASS=${VAZIO_PASS:-changeme_probe}

FAIL=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FAIL=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }

command -v python3 >/dev/null || { echo "INCONCLUSIVO: python3 ausente"; exit 2; }

_j() { python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: print(''); raise SystemExit(0)
print($1)" 2>/dev/null; }

_tok() {
  curl -s --max-time 10 -X POST "$AUTH/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
    | _j 'd.get("access_token","")'
}

# numero de pools devolvido por GET /v1/operational/pools (agent-registry)
_pools() {
  curl -s --max-time 10 "$REGISTRY/v1/operational/pools" -H "x-tenant-id: $TENANT" "$@" \
    | _j 'len(d.get("pools") or d.get("items") or [])'
}
# numero de instancias devolvido por GET /api/instances (mcp-server-plughub)
_inst() {
  curl -s --max-time 10 "$MCP/api/instances" -H "x-tenant-id: $TENANT" "$@" \
    | _j 'len(d.get("instances") or d.get("items") or (d if isinstance(d,list) else []))'
}

echo "== probe_ts_scope_resolvers =="

# ─────────────────────────────────────────── metade B (estatica): o fonte
# Roda primeiro porque nao depende de stack: um repo com o ramo legado de volta ja
# reprova, mesmo sem ambiente.
echo
echo "B. FONTE — o ramo legado nao pode voltar"
SITES="packages/agent-registry/src/routes/operational.ts packages/mcp-server-plughub/src/server.ts"
for f in $SITES; do
  [ -f "$f" ] || inc "sitio ausente: $f (arquivo renomeado? o censo perdeu o alvo)"
  # `LEGADO_POOLS_VAZIO` era a marca do ramo `[] -> irrestrito`. Sumiu com a AUT-23.
  if grep -q "LEGADO_POOLS_VAZIO" "$f"; then
    bad "$f ainda carrega LEGADO_POOLS_VAZIO — o ramo '[] = todos' voltou ao fonte"
  else
    ok "$f sem o marcador do ramo legado"
  fi
  # A contrapartida positiva: o sitio tem de DIZER que dominio vazio e nenhum pool.
  # Sem esta linha, apagar o marcador acima passaria verde por remocao, nao por conserto.
  if grep -q "dominio VAZIO" "$f"; then
    ok "$f declara o dominio vazio (AUT-03) em log"
  else
    bad "$f nao declara mais o dominio vazio — degradacao voltou a ser MUDA"
  fi
done

# ─────────────────────────────────────────── metade A (viva): o comportamento
echo
echo "A. VIVO — controle positivo antes de qualquer negativo"
T_ADMIN="$(_tok "$ADMIN_EMAIL" "$ADMIN_PASS")"
[ "${#T_ADMIN}" -lt 20 ] && inc "login de $ADMIN_EMAIL falhou — auth-api fora? sem controle positivo nao ha veredicto"

N_ADMIN_P="$(_pools -H "authorization: Bearer $T_ADMIN")"
N_ADMIN_I="$(_inst  -H "authorization: Bearer $T_ADMIN")"
[ -z "$N_ADMIN_P" ] && inc "agent-registry nao respondeu ($REGISTRY)"
[ -z "$N_ADMIN_I" ] && inc "mcp-server nao respondeu ($MCP)"
[ "$N_ADMIN_P" -gt 0 ] 2>/dev/null || inc "controle positivo VAZIO em /v1/operational/pools (admin@ ve $N_ADMIN_P) — os zeros abaixo nao provariam escopo"
[ "$N_ADMIN_I" -gt 0 ] 2>/dev/null || inc "controle positivo VAZIO em /api/instances (admin@ ve $N_ADMIN_I)"
ok "controle positivo: admin@ ve $N_ADMIN_P pools e $N_ADMIN_I instancias"

T_VAZIO="$(_tok "$VAZIO_EMAIL" "$VAZIO_PASS")"
[ "${#T_VAZIO}" -lt 20 ] && inc "login de $VAZIO_EMAIL falhou — a fixture de escopo vazio nao existe"
# A fixture precisa mesmo ter escopo vazio, senao o ramo passa pelo motivo errado.
CLAIM="$(printf '%s' "$T_VAZIO" | cut -d. -f2 | base64 -d 2>/dev/null \
  | _j 'json.dumps(d.get("accessible_pools"))')"
[ "$CLAIM" = "[]" ] || inc "fixture $VAZIO_EMAIL nao tem accessible_pools=[] (tem: $CLAIM)"
ok "fixture conferida: $VAZIO_EMAIL declara accessible_pools=[]"

echo
echo "   escopo legitimamente vazio -> 200 com lista vazia"
P="$(_pools -H "authorization: Bearer $T_VAZIO")"
I="$(_inst  -H "authorization: Bearer $T_VAZIO")"
if [ "$P" = "0" ] && [ "$I" = "0" ]; then
  ok "AUT-03: claim [] -> 0 pools / 0 instancias (e 200, nao 401 — config VALIDA)"
else
  bad "AUT-03: claim [] -> $P pools / $I instancias (deveria ser 0/0) — escopo degradando ABERTO"
fi

# ─────────────────────────────────────────── AUT-19: credencial nao e escopo
# Ate 2026-08-31 estes dois casos devolviam 200 com lista vazia, e por isso `[]`
# carregava DUAS causas que a tela nao sabia separar: "sua sessao expirou" e "voce nao
# tem pool nenhum". A decisao foi ELIMINAR a ambiguidade (401), nao rotula-la — logo o
# que se mede aqui e o STATUS, e o teste do corpo acima e o que prova que os dois casos
# tomam saidas diferentes. Sem os dois lados, um servico que devolvesse 401 para TUDO
# passaria: e o controle positivo la em cima que impede isso.
_status() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$1" -H "x-tenant-id: $TENANT" "${@:2}"; }
echo
echo "   credencial ausente/invalida -> 401 (AUT-19)"
for caso in "sem header|" "token invalido|-H|authorization: Bearer nao.e.um.jwt"; do
  nome="${caso%%|*}"; resto="${caso#*|}"
  if [ -z "$resto" ]; then
    SP="$(_status "$REGISTRY/v1/operational/pools")"; SI="$(_status "$MCP/api/instances")"
    SQ="$(_status "$REGISTRY/v1/operational/pools/sac_ia/queue")"
  else
    h="${resto#*|}"
    SP="$(_status "$REGISTRY/v1/operational/pools" -H "$h")"; SI="$(_status "$MCP/api/instances" -H "$h")"
    SQ="$(_status "$REGISTRY/v1/operational/pools/sac_ia/queue" -H "$h")"
  fi
  if [ "$SP" = "401" ] && [ "$SI" = "401" ] && [ "$SQ" = "401" ]; then
    ok "$nome -> 401 nas tres rotas (pools, instances, queue)"
  else
    bad "$nome -> pools=$SP instances=$SI queue=$SQ (esperado 401/401/401)"
  fi
done

# A rota de FILA nao tinha verificacao NENHUMA ate a AUT-19 — servia os `session_id` da
# fila de qualquer pool do tenant. Era a irma DESCOBERTA da que ja escopava, e servia o
# conteudo mais sensivel das duas: "duas portas para o mesmo dado e so uma trancada".
echo
echo "   fila: pool fora do escopo -> 403 (AUT-19)"
SQV="$(_status "$REGISTRY/v1/operational/pools/sac_ia/queue" -H "authorization: Bearer $T_VAZIO")"
SQA="$(_status "$REGISTRY/v1/operational/pools/sac_ia/queue" -H "authorization: Bearer $T_ADMIN")"
if [ "$SQV" = "403" ] && [ "$SQA" = "200" ]; then
  ok "queue: escopo vazio -> 403 · admin@ -> 200 (controle positivo no mesmo teste)"
else
  bad "queue: escopo vazio=$SQV (esperado 403) · admin@=$SQA (esperado 200)"
fi

echo
echo "=============================="
[ "$FAIL" -eq 0 ] && { echo "VERDE"; exit 0; }
echo "VERMELHO"; exit 1
