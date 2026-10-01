#!/usr/bin/env bash
# probe_aas01_a2a_pool.sh — 2026-10-01  (AAS-01 · adr-a2a-server-binding D1, D3, fase A0)
#
# PERGUNTA: `a2a` é canal da plataforma, e um pool só o expõe com CONTRATO — o descritor
# que o AgentCard da AAS-03 vai projetar — e nunca o contrato sem o canal?
#
# POR QUE AS DUAS DIREÇÕES
#   "Pool exposto por A2A" tem de ter UMA resposta. Se o descritor pudesse existir sem o canal,
#   o card (que lê o descritor) e o roteamento (que lê o canal) responderiam diferente — a
#   mesma pergunta em duas casas. Se o canal pudesse existir sem o descritor, o card listaria
#   um agente que não diz o que faz, o que recebe nem quem pode chamá-lo.
#
# QUATRO RAMOS
#   A  CONTRATO — o canal está nos cinco enumeradores obrigatórios (zod, tabela de capacidades
#      TS e Py, Literal do routing, VALID_CHANNELS do mcp-server) e na tela; a capacidade é
#      VAZIA (o adapter é a AAS-06: canal sem implementação não declara o que não faz).
#   B  REGISTRY AO VIVO — pool de contato com `a2a` sem descritor é recusado nomeando o campo
#      (POST e PUT que limpa); descritor sem o canal é recusado (POST e PUT); descritor inválido
#      é recusado; o válido persiste e volta no GET; tirar o canal limpando o descritor PASSA
#      (controle positivo — senão o portão prende o pool no canal para sempre).
#   C  ESPELHO — a fila interna `-int` herda o canal sem descritor e é aceita (é `internal`,
#      o card não a lista).
#   D  IMAGENS — o routing-engine e o gateway que ESTÃO rodando aceitam o canal (pergunta à
#      imagem, não à árvore), com o controle de que um canal inventado continua recusado.
#
# Fixtures fixas e idempotentes (a API não tem DELETE de pool).
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
ROUTING="${ROUTING_CONTAINER:-plughub-demo-routing-engine-1}"
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
POOL="probe_aas01_a2a"            # pool a2a de contato (fixture fixa)
POOL_TXT="probe_aas01_texto"      # pool sem a2a (fixture fixa)
POOL_SAI="probe_aas01_sai"        # pool que entra e sai do canal (fixture fixa)
# id NOVO a cada rodada: no verde ele nunca passa a existir
POOL_NUNCA="probe_aas01_sem_contrato_$(date +%s)"
D1='{"display_name":"Segunda via (probe)","description":"Emite a segunda via de um boleto.","input_schema":{"type":"object","properties":{"cpf":{"type":"string"}}},"output_schema":{"type":"object"},"skills":[{"id":"segunda_via","name":"Segunda via","description":"Emite a segunda via."}],"principal_kinds":["partner"]}'
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
tally() {
  # saída vazia é ramo que não rodou — nunca verde por ausência
  [ -z "$1" ] && { incon "ramo sem saida (o medidor nao rodou)"; return; }
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
      *) [ -n "$l" ] && incon "saida inesperada: $l";; esac
  done <<< "$1"
}

echo "════════════════════════════════════════════════════════════════════"
echo " a2a é canal — e só se expõe com contrato, nunca o contrato sem ele?"
echo "════════════════════════════════════════════════════════════════════"

for dep in jq curl python3 docker; do
  command -v "$dep" >/dev/null || { echo "INCONCLUSIVO — falta '$dep'"; exit 2; }
done

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CONTRATO ───────────────────────────────────────────────────────"
A_OUT=$(python3 - <<'PYEOF'
import io, re
def r(ok, txt): print(("OK " if ok else "FALHA ") + txt)
def src(p): return io.open(p, encoding="utf-8").read()
def nc_ts(s): return re.sub(r"(//[^\n]*|/\*.*?\*/)", "", s, flags=re.S)
def nc_py(s): return re.sub(r"#[^\n]*", "", s)

common = nc_ts(src("packages/schemas/src/common.ts"))
m = re.search(r"ChannelSchema\s*=\s*z\.enum\(\[(.*?)\]\)", common, re.S)
r(bool(m) and '"a2a"' in m.group(1), "A1 ChannelSchema (zod) declara a2a")

caps_ts = nc_ts(src("packages/schemas/src/channel-capabilities.ts"))
r(re.search(r"\ba2a\s*:\s*\[\s*\]", caps_ts) is not None, "A2 CHANNEL_CAPABILITIES (TS): a2a com capacidade VAZIA")
caps_py = nc_py(src("packages/channel-gateway/src/plughub_channel_gateway/channel_capability_registry.py"))
r(re.search(r'"a2a"\s*:\s*frozenset\(\s*\)', caps_py) is not None, "A3 CHANNEL_CAPABILITIES (Py): a2a com capacidade VAZIA")

models = nc_py(src("packages/routing-engine/src/plughub_routing/models.py"))
m = re.search(r"channel:\s*Literal\[(.*?)\]", models, re.S)
r(bool(m) and '"a2a"' in m.group(1), "A4 routing ConversationInboundEvent.channel aceita a2a")

srv = nc_ts(src("packages/mcp-server-plughub/src/server.ts"))
m = re.search(r"VALID_CHANNELS\s*=\s*\[(.*?)\]", srv, re.S)
r(bool(m) and '"a2a"' in m.group(1), "A5 mcp-server VALID_CHANNELS aceita a2a (sem cair para webchat)")

ui = nc_ts(src("packages/platform-ui/src/modules/config-recursos/PoolsPage.tsx"))
r("value: 'a2a'" in ui and "A2ADescriptorEditor" in ui and "a2aProblems" in ui,
  "A6 tela de pools: canal a2a, editor do descritor e validacao antes do salvar")
PYEOF
)
tally "$A_OUT"

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B · REGISTRY AO VIVO ───────────────────────────────────────────────"
C="curl -s --max-time 20"
TOKEN=$($C -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
if [ -z "$TOKEN" ]; then
  incon "B/C: login do admin falhou em $AUTH — registry nao medido"
else
  BODY=$(mktemp)
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  req() {
    local m=$1 p=$2 extra=()
    [ -n "${3:-}" ] && extra=(-d "$3")
    $C -o "$BODY" -w '%{http_code}' -X "$m" "${H[@]}" "${extra[@]}" "$REG$p"
  }
  body()  { cat "$BODY"; }
  campo() { body | jq -r '.details.field // empty' 2>/dev/null; }
  base()  { echo "\"pool_id\":\"$1\",\"sla_target_ms\":60000,\"agent_kind\":\"ai\""; }
  # o que o registry devolve tem os defaults aplicados — compara-se contra o D1 com eles
  D1_LIDO=$(echo "$D1" | jq -cS '.discoverable=false | .skills |= map(.tags=[] | .examples=[])')

  # resíduo de rodada da bateria de mutação: com o portão desligado, B1/B4 CRIAM pools de id
  # novo e gravam descritor na fixture sem canal. O resíduo sai do canal e perde o descritor —
  # fica INERTE, não some: a API não apaga pool e o PUT não aceita `status` (medido 2026-10-01).
  # A fixture volta ao estado de partida antes de medir.
  req GET "/v1/pools?limit=500" >/dev/null   # devolve {"pools":[...]}
  for velho in $(body | jq -r '.pools[]?.pool_id // empty' 2>/dev/null | grep '^probe_aas01_sem_contrato_'); do
    req PUT "/v1/pools/$velho" '{"channel_types":["webchat"],"a2a":null}' >/dev/null
  done
  [ "$(req GET "/v1/pools/$POOL_TXT")" = 200 ] && req PUT "/v1/pools/$POOL_TXT" '{"channel_types":["webchat"],"a2a":null}' >/dev/null

  # B1 — POST de contato com a2a e sem descritor: recusado, nomeando o campo, e não cria
  st=$(req POST /v1/pools "{$(base "$POOL_NUNCA"),\"channel_types\":[\"a2a\"]}"); cp=$(campo)
  gst=$(req GET "/v1/pools/$POOL_NUNCA")
  [ "$st" = 422 ] && [ "$cp" = a2a ] && [ "$gst" = 404 ] \
    && ok "B1 POST a2a sem descritor -> 422 details.field=a2a; pool nao criado" \
    || falha "B1 POST a2a sem descritor -> $st campo='$cp'; GET -> $gst"

  # B2 — fixture com descritor: cria ou atualiza; o GET devolve o gravado (com defaults)
  st=$(req GET "/v1/pools/$POOL")
  if [ "$st" = 404 ]; then st=$(req POST /v1/pools "{$(base "$POOL"),\"channel_types\":[\"a2a\"],\"a2a\":$D1}"); want=201
  else st=$(req PUT "/v1/pools/$POOL" "{\"channel_types\":[\"a2a\"],\"a2a\":$D1}"); want=200; fi
  req GET "/v1/pools/$POOL" >/dev/null; lido=$(body | jq -cS '.a2a')
  [ "$st" = "$want" ] && [ "$lido" = "$D1_LIDO" ] \
    && ok "B2 descritor persiste e volta no GET (discoverable=false por default)" \
    || falha "B2 gravar descritor -> $st (esperado $want); GET devolveu $lido"

  # B3 — PUT que limpa o descritor de pool a2a: recusado, e nada muda
  st=$(req PUT "/v1/pools/$POOL" '{"a2a":null}'); cp=$(campo)
  req GET "/v1/pools/$POOL" >/dev/null; lido=$(body | jq -cS '.a2a')
  [ "$st" = 422 ] && [ "$cp" = a2a ] && [ "$lido" = "$D1_LIDO" ] \
    && ok "B3 PUT a2a=null em pool a2a -> 422; descritor intacto" \
    || falha "B3 PUT limpando -> $st campo='$cp'; GET devolveu $lido"

  # B4 — descritor sem o canal: recusado no POST (id novo) e no PUT de pool existente
  st1=$(req POST /v1/pools "{$(base "${POOL_NUNCA}_b"),\"channel_types\":[\"webchat\"],\"a2a\":$D1}"); cp1=$(campo)
  gst=$(req GET "/v1/pools/$POOL_TXT")
  [ "$gst" = 404 ] && req POST /v1/pools "{$(base "$POOL_TXT"),\"channel_types\":[\"webchat\"]}" >/dev/null
  st2=$(req PUT "/v1/pools/$POOL_TXT" "{\"a2a\":$D1}"); cp2=$(campo)
  req GET "/v1/pools/$POOL_TXT" >/dev/null; lido=$(body | jq -c '.a2a')
  [ "$st1" = 422 ] && [ "$cp1" = a2a ] && [ "$st2" = 422 ] && [ "$cp2" = a2a ] && [ "$lido" = null ] \
    && ok "B4 descritor sem o canal -> 422 no POST e no PUT; pool sem descritor" \
    || falha "B4 descritor sem canal: POST -> $st1 ($cp1); PUT -> $st2 ($cp2); GET a2a=$lido"

  # B5 — descritor inválido: schema sem type, chave a mais, skill fora de snake_case
  s1=$(req PUT "/v1/pools/$POOL" "{\"a2a\":$(echo "$D1" | jq -c '.input_schema={}')}")
  s2=$(req PUT "/v1/pools/$POOL" "{\"a2a\":$(echo "$D1" | jq -c '.card_url="x"')}")
  s3=$(req PUT "/v1/pools/$POOL" "{\"a2a\":$(echo "$D1" | jq -c '.skills[0].id="Segunda Via"')}")
  req GET "/v1/pools/$POOL" >/dev/null; lido=$(body | jq -cS '.a2a')
  if [ "${s1:0:1}" = 4 ] && [ "${s2:0:1}" = 4 ] && [ "${s3:0:1}" = 4 ] && [ "$lido" = "$D1_LIDO" ]; then
    ok "B5 descritor invalido recusado: schema vazio -> $s1; chave extra -> $s2; skill id -> $s3"
  else
    falha "B5 descritor invalido: schema vazio -> $s1; chave extra -> $s2; skill id -> $s3; GET=$lido"
  fi

  # B7 — PUT que tira o canal SEM tocar no descritor: o estado resultante teria descritor sem
  #      canal, então é recusado (o portão julga o ESTADO, não só o corpo)
  st=$(req PUT "/v1/pools/$POOL" '{"channel_types":["webchat"]}'); cp=$(campo)
  req GET "/v1/pools/$POOL" >/dev/null; canais=$(body | jq -c '.channel_types')
  [ "$st" = 422 ] && [ "$cp" = a2a ] && [ "$canais" = '["a2a"]' ]     && ok "B7 PUT tirando o canal com descritor gravado -> 422; canais intactos"     || falha "B7 PUT tirando o canal -> $st campo='$cp'; canais=$canais"

  # B6 — controle positivo: sair do canal LIMPANDO o descritor passa (e entrar de novo também)
  gst=$(req GET "/v1/pools/$POOL_SAI")
  if [ "$gst" = 404 ]; then e1=$(req POST /v1/pools "{$(base "$POOL_SAI"),\"channel_types\":[\"webchat\",\"a2a\"],\"a2a\":$D1}")
  else e1=$(req PUT "/v1/pools/$POOL_SAI" "{\"channel_types\":[\"webchat\",\"a2a\"],\"a2a\":$D1}"); fi
  e2=$(req PUT "/v1/pools/$POOL_SAI" '{"channel_types":["webchat"],"a2a":null}')
  req GET "/v1/pools/$POOL_SAI" >/dev/null; canais=$(body | jq -c '.channel_types'); lido=$(body | jq -c '.a2a')
  [ "${e1:0:1}" = 2 ] && [ "$e2" = 200 ] && [ "$canais" = '["webchat"]' ] && [ "$lido" = null ] \
    && ok "B6 controle: entrar com contrato -> $e1; sair limpando -> 200 (canais=$canais, a2a=null)" \
    || falha "B6 controle positivo: entrar -> $e1; sair -> $e2; canais=$canais a2a=$lido"

  # ── C ──────────────────────────────────────────────────────────────────────
  echo ""
  echo "── C · ESPELHO ────────────────────────────────────────────────────────"
  st=$(req PUT "/v1/pools/$POOL" '{"internal_queue_enabled":true}')
  gst=$(req GET "/v1/pools/${POOL}-int"); esp=$(body | jq -c '{c:.channel_types,a:.a2a,p:.purpose,s:.status}')
  [ "$st" = 200 ] && [ "$gst" = 200 ] && [ "$esp" = '{"c":["a2a"],"a":null,"p":"internal","s":"active"}' ] \
    && ok "C1 espelho ${POOL}-int herda o canal sem descritor e e aceito ($esp)" \
    || falha "C1 ligar fila interna -> $st; espelho GET -> $gst $esp"
  # C2 — editar o espelho pela API (a tela edita pools `internal`) não exige contrato
  st=$(req PUT "/v1/pools/${POOL}-int" '{"description":"Fila interna (probe AAS-01)"}')
  [ "$st" = 200 ] && ok "C2 PUT no espelho (internal, com a2a e sem descritor) -> 200"     || falha "C2 PUT no espelho -> $st $(body | jq -c '.details // .error' 2>/dev/null)"
  req PUT "/v1/pools/$POOL" '{"internal_queue_enabled":false}' >/dev/null
fi

# ── D ────────────────────────────────────────────────────────────────────────
echo ""
echo "── D · IMAGENS EM EXECUCAO ────────────────────────────────────────────"
D_OUT=$(docker exec -i "$ROUTING" python - 2>&1 <<'PYEOF'
from pydantic import ValidationError
from plughub_routing.models import ConversationInboundEvent as E
# julga só o campo `channel`: os demais obrigatórios mudam com o tempo e não são a pergunta
def erros_de_canal(ch):
    try:
        E(session_id="s", tenant_id="t", customer_id="c", channel=ch)
    except ValidationError as e:
        return [x for x in e.errors() if x["loc"][:1] == ("channel",)], len(e.errors())
    return [], 0
c, n = erros_de_canal("a2a")
print(("FALHA D1 routing-engine recusa a2a: " + c[0]["msg"]) if c
      else f"OK D1 routing-engine em execucao aceita channel=a2a ({n} erro(s) em outros campos)")
c, _ = erros_de_canal("a2a_inventado")
print("OK D2 controle: canal inventado continua recusado" if c
      else "FALHA D2 controle: canal inventado ACEITO — o Literal virou str")
PYEOF
)
tally "$D_OUT"
D_OUT=$(docker exec -i "$GW" python - 2>&1 <<'PYEOF'
from plughub_channel_gateway.channel_capability_registry import CHANNEL_CAPABILITIES as C, channel_satisfies as s
print(("OK " if C.get("a2a") == frozenset() else "FALHA ") + f"D3 gateway em execucao: a2a com capacidade {sorted(C.get('a2a', {'AUSENTE'}))}")
print(("OK " if (not s("a2a", ["text"])) and s("webchat", ["text"]) else "FALHA ") + "D4 a2a nao satisfaz 'text'; webchat satisfaz (controle)")
PYEOF
)
tally "$D_OUT"

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo "VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo "INCONCLUSIVO — $INCONCL ramo(s) nao medido(s)"; exit 2; fi
echo "VERDE — a2a e canal, e o pool so o expoe com contrato (e nunca o contrato sem ele)."
exit 0
