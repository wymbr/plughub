#!/usr/bin/env bash
#
# probe_mcp_rest_surface.sh — CAP-10, a medição que destrava (2026-09-01)
#
# A PERGUNTA QUE ELE RESPONDE
# ==========================
# A CAP-10 estava BLOQUEADA por *"falta medir a superfície de rede do mcp-server"*.
# Esta é a medição, e ela inverteu a prioridade do item.
#
# ACHADOS QUE ELE TRAVA
# =====================
# 1. **A borda existe e é versionada** — o nginx é gerado dentro do
#    `packages/platform-ui/Dockerfile`, e é por isso que procurar `nginx*.conf` no
#    repositório não acha nada. 21 `location`, 14 upstreams.
# 2. **A borda NÃO publica o transporte MCP.** Pela borda, `/sse` devolve `text/html`
#    (o fallback SPA); direto na 3100 devolve `text/event-stream` com o handshake. Ou
#    seja: as 48 dívidas de tool da CAP-09 estão atrás de uma porta que a única borda
#    do repositório não expõe. ⚠️ `200` ali é o **valor plausível** do catálogo — o
#    código HTTP é idêntico nos dois casos e só o `content-type` separa.
#    ⚠️ **"A borda não publica" era METADE da resposta** — a porta publicava sozinha.
#    Medido em 2026-09-01 (CAP-13): com `"3100:3100"`, `Test-NetConnection` sobre o IP
#    de LAN desta máquina respondia **ACEITA**, isto é, qualquer aparelho no mesmo
#    Wi-Fi alcançava o transporte anônimo. Hoje o bind é `127.0.0.1:3100:3100` e o
#    ramo F trava as duas metades: a DECLARAÇÃO nos composes e o bind VIVO.
# 3. **A borda PUBLICA `/api/*`** — 20 rotas, e eram **9 sem credencial**. Estas são
#    a superfície realmente exposta do mcp-server. ✅ **As 9 fecharam na CAP-12
#    (2026-09-01)**: a linha delas migrou para `gateada` e o ramo D INVERTEU de sinal
#    — ele agora reprova se alguma voltar a responder sem credencial.
# 4. **Exposição ≠ dano** (D14.1): duas das abertas devolveram vazio com a stack
#    ociosa, mas `conversation_history` lê `session:{id}:messages`, que tem **três
#    produtores** (`session.ts:529`, `orchestrator-bridge/main.py:7340`,
#    `channel-gateway/session_registry.py:144`). Zero agora não é inócuo.
#
# DUAS METADES QUE NÃO SE SUBSTITUEM
# ==================================
#   A · censo AST de `server.ts`  — quais rotas EXIGEM que alguém decida
#   B · medição ATRAVÉS DA BORDA  — o que responde de fato, com CONTROLE POSITIVO
# A sozinha não vê o que o deploy publica; B sozinha não distingue "sem credencial" de
# "credencial que eu não sei apresentar" — por isso o controle positivo é obrigatório:
# sem ele, um 401 universal (serviço fora do ar, nginx quebrado) passaria por proteção.
#
# ⚠️ DÍVIDA DE ESCOPO — DECLARADA, não esquecida
# ==============================================
# A CAP-12 fechou **CREDENCIAL**, não **LINHA**. São dois fatos, e a analytics-api já
# pagou para aprender que são: *"EXIGIR CREDENCIAL e RECORTAR LINHA são dois fatos"*.
# Hoje um operador autenticado de QUALQUER pool lê a conversa de QUALQUER sessão por
# `/api/conversation_history/{id}` — e o agravante é que a chave lida
# (`session:{id}:messages`) **não tem sequer prefixo de tenant**, então nem o
# isolamento por tenant existe ali. O irmão gateado E escopado do mesmo dado é
# `analytics-api /v1/transcript/sessions/{id}`, fechado em 2026-08-30 — de novo duas
# portas para o mesmo dado, e agora só uma delas recorta.
# Gatilho para pagar: o primeiro tenant com operadores que não devem se ver.
# Registrado como CAP-14 no `pending.md` — não é achado a redescobrir.
#
# ⚠️ ESTE PROBE NÃO FECHA ROTA. Ele trava o censo.
#
# Uso:  bash infra/test/probe_mcp_rest_surface.sh
# Env:  BORDA=http://localhost:5174   DIRETO=http://localhost:3100
#
# Veredicto de TRÊS estados: 0 = OK · 1 = REPROVOU · 3 = INCONCLUSIVO
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

BORDA="${BORDA:-http://localhost:5174}"
DIRETO="${DIRETO:-http://localhost:3100}"
# O positivo de aceitação (ramo D2) precisa de um token REAL — portão medido só pelo
# negativo não se distingue de um handler quebrado.
AUTH="${AUTH:-http://localhost:3202}"     # 3202 = auth-api no host (3200 é o ai-gateway)
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"
TENANT="${TENANT:-tenant_demo}"
# Sessão que NÃO existe: o ramo mede o PORTÃO, nunca o conteúdo. Nenhuma leitura de
# conversa real acontece neste probe.
SESSAO="sess_probe_cap12_inexistente"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CENSO="$ROOT/infra/test/_mcp_rest_census.mjs"

FAIL=0; INCONCL=0
ok()  { echo "  ✓ $*"; }
bad() { echo "  ❌ $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ⏭️  $* (INCONCLUSIVO)"; INCONCL=$((INCONCL+1)); }

# ── DECLARAÇÃO ────────────────────────────────────────────────────────────────
# rota|postura|motivo     · postura ∈ { gateada, aberta-divida, aberta-isenta }
#
# `aberta-divida` ≠ `aberta-isenta` pelo mesmo motivo do par `_SCOPE_EXEMPT`/
# `_SCOPE_DEBT`: *"decidimos que não precisa"* e *"ainda não decidimos"* são fatos
# diferentes, e juntá-los faz a dívida herdar a tranquilidade da decisão.
#
# ⚠️ Rota nova SEM linha aqui reprova. É o ponto do arquivo.
DECLARADO=$(cat <<'TABELA'
GET /api/agent-state|gateada|requireJwtRole
GET /api/approval_audit/:sessionId|gateada|verifyJwtPayload
GET /api/conversation_history/:sessionId|gateada|requireJwtRole leitura (CAP-12). EXIGE credencial e NAO recorta linha — ver DIVIDA DE ESCOPO no cabecalho
GET /api/copilot_state/:sessionId|gateada|requireJwtRole leitura (CAP-12)
GET /api/instances|gateada|verifyJwtPayload
GET /api/supervisor_capabilities/:sessionId|gateada|requireJwtRole leitura (CAP-12)
GET /api/supervisor_state/:sessionId|gateada|requireJwtRole
GET /api/work_queue/list|gateada|requireJwtRole leitura (CAP-12)
GET /api/work_queue/pending|gateada|requireJwtRole leitura (CAP-12)
GET /health|aberta-isenta|liveness do compose; exigir credencial acopla o boot da stack ao do emissor de token (mesma isencao do analytics-api)
GET /internal/context-audit|gateada|x-service-token contra MCP_INTERNAL_SERVICE_TOKEN, e FALHA FECHADA (503 sem env)
GET /sse|aberta-divida|transporte MCP, anonimo por construcao; NAO publicado pela borda (CAP-09/CAP-10)
POST /api/agent_done/:sessionId|gateada|requireJwtRole escrita (CAP-12)
POST /api/force-complete/:sessionId|gateada|requireJwtRole
POST /api/inject-context/:sessionId|gateada|requireJwtRole
POST /api/menu_submit/:sessionId|gateada|requireJwtRole escrita (CAP-12)
POST /api/session_transfer/:sessionId|gateada|verifyJwtPayload
POST /api/work_queue/claim/:sessionId|gateada|requireJwtRole escrita (CAP-12)
POST /api/work_queue/expire/:sessionId|gateada|requireJwtRole
POST /api/work_queue/release/:sessionId|gateada|requireJwtRole escrita (CAP-12)
POST /internal/context-snapshot|gateada|x-service-token contra MCP_INTERNAL_SERVICE_TOKEN, e FALHA FECHADA (503 sem env)
POST /messages|aberta-divida|canal de escrita do transporte MCP; NAO publicado pela borda (CAP-09/CAP-10)
PUT /api/agent-pause|gateada|requireJwtRole
PUT /api/agent-resume|gateada|requireJwtRole
POST /api/agent-clear-pause|gateada|requireJwtRole
TABELA
)

echo "══ superfície REST do mcp-server-plughub ══"
echo "   borda=$BORDA  direto=$DIRETO"

command -v node >/dev/null 2>&1 || { echo "  ❌ node ausente"; exit 3; }
[ -f "$CENSO" ] || { echo "  ❌ ausente: $CENSO"; exit 3; }

echo
echo "── A · censo AST de server.ts ──"
AST="$(node "$CENSO" 2>&1)"
if ! echo "$AST" | head -1 | grep -q '^{'; then
  echo "$AST" | head -5 | sed 's/^/     /'; inc "censo AST não produziu JSON"; AST=""
else
  echo "$AST" | python3 -c "
import sys, json
r = json.load(sys.stdin)['routes']
pub = [x for x in r if x['published']]
print(f'  ✓ {len(r)} rotas · {len(pub)} publicadas pela borda (^/api)')
print(f\"     publicadas SEM credencial: {sum(1 for x in pub if not x['credentials'])}/{len(pub)}\")
print(f\"     total    SEM credencial: {sum(1 for x in r if not x['credentials'])}/{len(r)}\")
"
fi

echo
echo "── B · o transporte MCP atravessa a BORDA? ──"
CT_BORDA=$(curl -s -m 6 -o /dev/null -w '%{content_type}' "$BORDA/sse" 2>/dev/null || echo "")
CT_DIR=$(curl -s -m 3 -o /dev/null -w '%{content_type}' "$DIRETO/sse" 2>/dev/null || echo "")
if [ -z "$CT_DIR" ]; then
  inc "mcp-server inalcançável em $DIRETO — sem testemunha de que /sse existe"
elif ! echo "$CT_DIR" | grep -qi "event-stream"; then
  bad "TESTEMUNHA FALHOU: $DIRETO/sse não é event-stream (é '$CT_DIR') — o resto do ramo não vale"
elif [ -z "$CT_BORDA" ]; then
  inc "borda inalcançável em $BORDA"
elif echo "$CT_BORDA" | grep -qi "event-stream"; then
  bad "a BORDA publica o transporte MCP (content-type '$CT_BORDA') — mudou desde 2026-09-01"
else
  ok "borda devolve '$CT_BORDA' e não event-stream ⇒ o transporte NÃO é publicado"
  echo "     (o código HTTP é 200 nos dois; só o content-type separa — não julgar por status)"
fi

echo
echo "── C · CONTROLE POSITIVO · rota gateada recusa sem credencial ──"
GATE_OK=0
for r in "/api/instances" "/api/agent-state"; do
  c=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$BORDA$r" 2>/dev/null || echo 000)
  if [ "$c" = "401" ] || [ "$c" = "403" ]; then ok "$r → $c"; GATE_OK=$((GATE_OK+1))
  else bad "$r → $c (esperado 401/403; sem este ramo, um 401 universal passaria por proteção)"; fi
done

echo
echo "── D · as NOVE da CAP-12 recusam anônimo E aceitam credencial ──"
#
# Duas metades obrigatórias, e a segunda é a que quase ninguém escreve: *"ao fechar um
# portão, escreva o caso que prova que ele DEIXA ALGUÉM PASSAR — o negativo sozinho
# passa pelo motivo errado"* (§ Security). Handler quebrado, rota renomeada ou nginx
# caído produzem 401 em tudo, e um probe só-negativo chamaria isso de segurança.
#
# ⚠️ O positivo NÃO exige 200: `menu_submit`, `claim` e `release` validam corpo e
# devolvem 400 mesmo com credencial válida. O que se afirma é *"passou do portão"* —
# logo qualquer coisa que não seja 401/403/503. Exigir 200 acoplaria este ramo à
# validação de corpo de cada handler, e ele reprovaria por motivo alheio.
NOVE_GET="/api/conversation_history/$SESSAO
/api/copilot_state/$SESSAO
/api/supervisor_capabilities/$SESSAO
/api/work_queue/list
/api/work_queue/pending"
NOVE_POST="/api/agent_done/$SESSAO
/api/menu_submit/$SESSAO
/api/work_queue/claim/$SESSAO
/api/work_queue/release/$SESSAO"

if [ "$GATE_OK" -eq 0 ]; then
  inc "sem controle positivo, este ramo não distingue fechado de serviço fora do ar"
else
  # ── D1 · NEGATIVO — anônimo é recusado ──────────────────────────────────────
  D1=0
  while read -r r; do
    [ -n "$r" ] || continue
    c=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$BORDA$r" 2>/dev/null || echo 000)
    case "$c" in
      401|403) D1=$((D1+1)) ;;
      200)     bad "REGRESSÃO: $r → 200 SEM credencial — a CAP-12 foi desfeita" ;;
      503)     bad "$r → 503: serviço sem PLUGHUB_JWT_SECRET (deploy, não credencial)" ;;
      *)       inc "$r → $c (investigar)" ;;
    esac
  done <<< "$NOVE_GET"
  while read -r r; do
    [ -n "$r" ] || continue
    c=$(curl -s -m 8 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
        -d '{}' "$BORDA$r" 2>/dev/null || echo 000)
    case "$c" in
      401|403) D1=$((D1+1)) ;;
      200|400) bad "REGRESSÃO: POST $r → $c SEM credencial — a CAP-12 foi desfeita" ;;
      503)     bad "POST $r → 503: serviço sem PLUGHUB_JWT_SECRET (deploy, não credencial)" ;;
      *)       inc "POST $r → $c (investigar)" ;;
    esac
  done <<< "$NOVE_POST"
  [ "$D1" -eq 9 ] && ok "as 9 recusam anônimo (401/403)"

  # ── D2 · POSITIVO DE ACEITAÇÃO ──────────────────────────────────────────────
  TOKEN=""
  if command -v jq >/dev/null 2>&1; then
    TOKEN=$(curl -s -m 8 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
      -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}" \
      2>/dev/null | jq -r '.access_token // empty')
  fi
  if [ -z "$TOKEN" ]; then
    inc "sem token do auth-api ($AUTH) — não dá para provar que o portão DEIXA passar"
  else
    PASSOU=0; TOTAL=0
    while read -r r; do
      [ -n "$r" ] || continue
      TOTAL=$((TOTAL+1))
      c=$(curl -s -m 8 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
          "$BORDA$r" 2>/dev/null || echo 000)
      case "$c" in
        401|403|503) bad "com credencial VÁLIDA, $r → $c (o portão fechou para quem PODIA)" ;;
        *)           PASSOU=$((PASSOU+1)) ;;
      esac
    done <<< "$NOVE_GET"
    while read -r r; do
      [ -n "$r" ] || continue
      TOTAL=$((TOTAL+1))
      c=$(curl -s -m 8 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
          -H "Authorization: Bearer $TOKEN" -d '{}' "$BORDA$r" 2>/dev/null || echo 000)
      case "$c" in
        401|403|503) bad "com credencial VÁLIDA, POST $r → $c (o portão fechou para quem PODIA)" ;;
        *)           PASSOU=$((PASSOU+1)) ;;
      esac
    done <<< "$NOVE_POST"
    [ "$PASSOU" -eq "$TOTAL" ] && ok "as $TOTAL passam com credencial válida (fecha, não quebra)"
  fi
fi

echo
echo "── E · toda rota CLASSIFICADA, e a classificação bate com a medição ──"
if [ -n "$AST" ]; then
  SAIDA=$(python3 - "$AST" <<PY
import sys, json
medido = {x['key']: x for x in json.loads(sys.argv[1])['routes']}
decl = {}
for l in """$DECLARADO""".strip().splitlines():
    k, postura, motivo = l.split("|", 2)
    decl[k] = (postura, motivo)

erros = []
for k in sorted(set(medido) - set(decl)):
    erros.append(f"SEM LINHA: '{k}' — classifique (gateada/aberta-divida/aberta-isenta)")
for k in sorted(set(decl) - set(medido)):
    erros.append(f"linha ÓRFÃ: '{k}' não existe mais em server.ts — remova")
for k in sorted(set(medido) & set(decl)):
    real = "gateada" if medido[k]['credentials'] else "aberta"
    espera = decl[k][0]
    if espera == "gateada" and real != "gateada":
        erros.append(f"REGRESSÃO em '{k}': declarada gateada, medida ABERTA")
    if espera.startswith("aberta") and real == "gateada":
        erros.append(f"MELHORIA não declarada em '{k}': agora tem credencial — mova para 'gateada'")

from collections import Counter
c = Counter(v[0] for v in decl.values())
print(f"CONTAGEM gateada={c['gateada']} aberta-divida={c['aberta-divida']} aberta-isenta={c['aberta-isenta']} total={len(decl)}")
for e in erros: print("ERRO " + e)
PY
)
  echo "$SAIDA" | grep '^CONTAGEM' | sed 's/^CONTAGEM /     /'
  if echo "$SAIDA" | grep -q '^ERRO '; then
    echo "$SAIDA" | grep '^ERRO ' | sed 's/^ERRO //' | while read -r l; do echo "  ❌ $l"; done
    FAIL=$((FAIL + $(echo "$SAIDA" | grep -c '^ERRO ')))
  else ok "as 25 estão classificadas e nenhuma postura mudou"; fi
else
  inc "sem o censo AST, não há o que classificar"
fi

echo
echo "── F · a 3100 publica em LOOPBACK, e os composes declaram isso ──"
#
# Duas metades que não se substituem, pelo mesmo motivo do par A/B: a DECLARAÇÃO é o
# que viaja para outro deploy (num host Linux com Docker nativo, `0.0.0.0` é alcance
# de LAN de verdade), e o BIND VIVO é o que esta máquina realmente faz — um pode estar
# certo com o outro errado, e é o segundo que morde hoje.
#
# ⚠️ Este ramo NÃO afirma "recusa a partir da LAN". Seria tentador e seria um teste que
# não pode reprovar AQUI: nesta máquina o firewall do Hyper-V já recusa a rota
# WSL→host, então a asserção ficaria verde antes e depois da correção. O fato
# falseável é o ENDEREÇO DO BIND, e é sobre ele que o ramo decide.
F_ESPERADO="127.0.0.1:3100:3100"
for arq in "$ROOT/docker-compose.demo.yml" "$ROOT/docker-compose.full.yml"; do
  if [ ! -f "$arq" ]; then inc "ausente: $(basename "$arq")"; continue; fi
  linha=$(grep -E '^\s*-\s*"[0-9.:]*3100:3100"' "$arq" | head -1 | tr -d ' "-')
  case "$linha" in
    "$F_ESPERADO") ok "$(basename "$arq") declara $F_ESPERADO" ;;
    "")            inc "$(basename "$arq"): nenhuma publicação de 3100 encontrada — a porta saiu de vez? atualize este ramo" ;;
    *)             bad "$(basename "$arq") declara '$linha' — a 3100 voltou a publicar fora do loopback (CAP-13)" ;;
  esac
done
if command -v docker >/dev/null 2>&1; then
  BIND=$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
         | grep 'mcp-server-plughub' | head -1 | grep -oE '[0-9.]+:3100->3100' | head -1)
  if [ -z "$BIND" ]; then
    inc "mcp-server sem publicação de 3100 no \`docker ps\` (parado, ou porta já removida)"
  elif [ "${BIND%%:*}" = "127.0.0.1" ]; then
    ok "bind VIVO em ${BIND%%:*} — não escuta em interface externa"
  else
    bad "bind VIVO em ${BIND%%:*} (esperado 127.0.0.1) — a stack corrente expõe o transporte MCP"
  fi
else
  inc "docker ausente — sem o bind vivo, só a declaração foi conferida"
fi

echo
echo "──────────────────────────────────────────────────────────"
echo "  FAIL=$FAIL  INCONCLUSIVO=$INCONCL"
if [ "$INCONCL" -gt 0 ]; then echo "⏭️  INCONCLUSIVO — não mediu tudo; isto NÃO é verde"; exit 3; fi
if [ "$FAIL" -eq 0 ]; then
  echo "✅ superfície estável — transporte fora da borda E fora da LAN; as 9 da CAP-12 fechadas"
  exit 0
fi
echo "❌ a superfície MUDOU sem a declaração acompanhar"
exit 1
