#!/usr/bin/env bash
# gate_supervisor_tenant_guard.sh — 2026-08-21
#   TODO § "`session:{id}:meta` — partição de propriedade não declarada", defeito 2
#
# PERGUNTA: o `/supervisor/join` do serviço RODANDO recusa quem não pode ter o
# tenant conferido?
#
# Existe separado do unit test porque eles respondem coisas diferentes: o unit
# test julga a FUNÇÃO, este julga o DEPLOY. Um serviço rodando imagem antiga passa
# no primeiro e reprova no segundo — e é exatamente o modo de falha que este
# repositório já pagou ("`build` verde NÃO recria container").
#
# TRÊS pontos, e o primeiro é o que dá poder de reprovar:
#   P0  meta COM tenant_id correto        → 200   ← CONTROLE
#   P1  meta SEM tenant_id                → 403   ← o caminho que o guard antigo
#                                                    deixava passar
#   P2  meta com tenant_id DIVERGENTE     → 403   ← a metade que sempre funcionou
#
# Sem P0 e P2, um guard que recusasse tudo ficaria verde em P1 sem provar nada.
#
# ESCOPO DA ESCRITA: o gate cria DUAS chaves de rascunho (`session:__gate_*__:meta`)
# e apaga tudo o que tocou no `trap`, inclusive o `supervisor:*:active` e o stream
# que um join bem-sucedido cria. Nenhuma sessão real é tocada.
#
# USO:   bash infra/test/gate_supervisor_tenant_guard.sh
# SAÍDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO

set -u

# Credencial (2026-08-27). `/supervisor/join` passou a exigir token no passo T2 —
# sem ele TODO probe volta 401 e o gate lia isso como "aceito", invertendo o
# veredicto.
#
# CORRIGIDO EM 2026-09-07 (GAT-03). Esta secao dizia: *"usa o principal IRRESTRITO
# de proposito: com escopo irrestrito o `_authorize_live_session` retorna cedo e a
# dimensao POOL sai do caminho"*. A premissa foi REFUTADA por duas mudancas que
# nao passaram por aqui:
#   . `unrestricted` foi REMOVIDO em 2026-09-01 (AUT-15) — todo escopo hoje e
#     ENUMERADO, entao nao existe mais o retorno cedo em que o gate se apoiava;
#   . desde 2026-08-30 o conteudo RECUSA o escopo INDETERMINAVEL, e a sessao de
#     rascunho deste gate nao tem pool em lugar nenhum.
# Medido: o P0 (o CONTROLE) recusava com `session_pools_undeterminable` — a
# dimensao POOL passou a decidir ANTES do tenant e o gate reprovava sem nunca
# alcancar a proposicao que existe para medir. Produto CERTO, gate vermelho.
#
# Hoje a sessao de rascunho carrega um `pool_id` tirado do PROPRIO token (nunca um
# literal): a dimensao de pool passa por construcao, e quem decide volta a ser o
# tenant. Token que nao enumere pool nenhum deixa o gate INCONCLUSIVO — verde por
# ausencia de amostra seria a mesma mentira ao contrario.
PLUGHUB_TEST_EMAIL="${PLUGHUB_TEST_EMAIL:-probe@plughub.local}"
PLUGHUB_TEST_PASS="${PLUGHUB_TEST_PASS:-changeme_probe}"
export PLUGHUB_TEST_EMAIL PLUGHUB_TEST_PASS
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_auth.sh"
# CAP-12: supervisor_capabilities e copilot_state passaram a exigir credencial.
plughub_auth_curl_shim

TENANT="${TENANT:-tenant_demo}"
OUTRO="${OUTRO:-tenant_outro}"
COMPOSE="${COMPOSE_FILE:-docker-compose.demo.yml}"
AN="${AN:-http://localhost:3500}"

DC="docker compose -f $COMPOSE"
CURL="curl -s -o /dev/null -w %{http_code} --max-time 15"
JSON='-H Content-Type:application/json'
r() { $DC exec -T redis redis-cli "$@" < /dev/null; }

S_OK="__gate_tenant_ok__"
S_NO="__gate_tenant_missing__"

cleanup() {
  for s in "$S_OK" "$S_NO"; do
    r DEL "session:${s}:meta" "supervisor:${s}:active" "session:${s}:stream" > /dev/null 2>&1
  done
}
trap cleanup EXIT

inconclusivo() { echo; echo "VEREDICTO: INCONCLUSIVO — $1"; exit 2; }

[ "$(r PING)" = "PONG" ] || inconclusivo "redis mudo"

echo "══ gate: /supervisor/join confere o tenant contra o meta? ══"
echo "   analytics-api: $AN   tenant: $TENANT"

# Preflight: o serviço responde? Sem isto, um 000 de conexão recusada seria lido
# como "recusou por tenant" — o veredicto certo pelo motivo errado.
# `/v1/health` — o `/health` sem prefixo devolve 404 nesta API (medido: o único
# `@app.get` de saúde é `/v1/health`, main.py:163). O preflight recusou em vez de
# adivinhar, que é o comportamento desejado: um 000/404 lido como "recusou por
# tenant" daria o veredicto certo pelo motivo errado.
PING_HTTP="$($CURL "$AN/v1/health")"
[ "$PING_HTTP" = "200" ] || inconclusivo "analytics-api não respondeu /v1/health (HTTP $PING_HTTP)"

# ── Escopo de POOL — a dimensao que passou a decidir ANTES do tenant ─────────
# O pool sai do PROPRIO token. Fixar `demo_ia` no fonte faria o gate reprovar no
# dia em que o escopo de `probe@` mudasse, e reprovar dizendo "tenant" sobre um
# defeito de pool — que e exatamente o modo de falha que esta correcao fecha.
TOK_PRE="$(plughub_token 2>/dev/null)"
[ -n "$TOK_PRE" ] || inconclusivo "sem token (plughub_token): nada abaixo mede o guard"
POOL="$(printf '%s' "$TOK_PRE" | python3 -c 'import base64,json,sys
t = sys.stdin.read().strip().split(".")[1]
t += "=" * (-len(t) % 4)
ap = json.loads(base64.urlsafe_b64decode(t)).get("accessible_pools") or []
print(ap[0] if ap else "")')"
[ -n "$POOL" ] || inconclusivo "o token de $PLUGHUB_TEST_EMAIL nao enumera pool nenhum — sem pool alcancavel a recusa vem do ESCOPO, nunca do tenant, e o gate mediria a proposicao vizinha"
META_OK="{\"tenant_id\":\"$TENANT\",\"channel\":\"webchat\",\"pool_id\":\"$POOL\"}"
META_NO="{\"channel\":\"webchat\",\"contact_id\":\"c1\",\"pool_id\":\"$POOL\"}"

# O CODIGO SOZINHO DEIXOU DE DISCRIMINAR. Ate 2026-08-30 o unico 403 desta rota era
# o guard de tenant; hoje o recorte por POOL recusa com o MESMO 403. Um gate que so
# olhasse o numero ficaria VERDE com o guard de tenant REMOVIDO, desde que o de pool
# recusasse — logo ele afere o `detail`, que nomeia qual guard falou. Uma chamada so:
# o P0 ESCREVE, e repeti-la cairia no ramo idempotente com outro corpo.
join() { # session_id  tenant_declarado → "CODIGO detail"
  local out code body
  out="$(curl -s -w '\n%{http_code}' --max-time 15 -X POST "$AN/supervisor/join" $JSON \
         -H "$(plughub_auth_header)" \
         -d "{\"tenant_id\":\"$2\",\"session_id\":\"$1\",\"operator_id\":\"gate\"}")"
  code="${out##*$'\n'}"
  body="${out%$'\n'*}"
  printf '%s %s' "$code" "$(printf '%s' "$body" | sed -n 's/.*"detail":"\([^"]*\)".*/\1/p')"
}

# Testemunha: a MESMA chamada SEM credencial tem de ser 401. Sem ela, o dia em que
# o header parar de ser enviado devolve 401 em tudo e o gate volta a reportar as
# falhas de tenant de antes — veredicto certo pelo motivo errado, outra vez.
#
# `command curl`, e isto e o conserto de um defeito MEDIDO: o topo deste arquivo
# chama `plughub_auth_curl_shim`, que sombreia `curl` e anexa a credencial a TUDO
# que vai para :3500 — inclusive a esta chamada, cujo proposito e nao ter nenhuma.
# A testemunha ficava desarmada pelo mecanismo instalado no MESMO arquivo, e o PA
# passava a medir o recorte de pool em vez da camada de autenticacao. O shim nasceu
# depois (CAP-12, 2026-09-01) e nao tinha como saber que havia um call site que
# precisava ficar de fora: quando um shim decide por TODO mundo, quem precisa da
# excecao tem de pedi-la EXPLICITAMENTE.
noauth_join() {
  command curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
    -X POST "$AN/supervisor/join" $JSON \
    -d "{\"tenant_id\":\"$TENANT\",\"session_id\":\"$1\",\"operator_id\":\"gate\"}"
}

FAIL=""

# ── PA — TESTEMUNHA: a camada de autenticacao esta na frente ────────────────
r SET "session:${S_OK}:meta" "$META_OK" EX 300 > /dev/null
CA="$(noauth_join "$S_OK")"
echo "   PA sem credencial ............................... HTTP $CA   (esperado 401)"
[ "$CA" = "401" ] || FAIL="$FAIL\n   · PA devolveu $CA — a rota nao recusou por FALTA DE TOKEN, e 403 aqui nao"$'\n'"     e a mesma coisa que 401: significa que a credencial CHEGOU (shim?) e"$'\n'"     quem recusou foi outro guard — entao PA nao testemunha nada sobre a"$'\n'"     camada de autenticacao, que e a unica coisa que ele existe para provar."

# ── P0 — CONTROLE: meta com tenant certo e pool DENTRO do escopo ────────────
r SET "session:${S_OK}:meta" "$META_OK" EX 300 > /dev/null
R0="$(join "$S_OK" "$TENANT")"; C0="${R0%% *}"; D0="${R0#* }"
echo "   P0 meta COM tenant_id, declarado igual .......... HTTP $C0   (esperado 200)"
if [ "$C0" != "200" ]; then
  FAIL="$FAIL\n   · P0 devolveu $C0 ($D0) — o guard está recusando quem PODE entrar,"$'\n'"     e nesse estado o P1 abaixo ficaria verde sem provar nada."$'\n'"     Se o detail for \`session_pools_undeterminable\`, quem recusou foi o"$'\n'"     escopo de POOL e nao o tenant: o pool usado foi \`$POOL\`."
fi

# ── P1 — o caminho que o guard antigo deixava passar ────────────────────────
r SET "session:${S_NO}:meta" "$META_NO" EX 300 > /dev/null
R1="$(join "$S_NO" "$TENANT")"; C1="${R1%% *}"; D1="${R1#* }"
echo "   P1 meta SEM tenant_id ........................... HTTP $C1 $D1   (esperado 403 tenant_unverifiable)"
if [ "$C1" != "403" ]; then
  FAIL="$FAIL\n   · P1 devolveu $C1 — meta sem \`tenant_id\` foi ACEITO. É o fail-open:"$'\n'"     \`meta.get(\"tenant_id\", body.tenant_id) != body.tenant_id\` compara o"$'\n'"     valor com ele mesmo. Serviço rodando imagem antiga também dá isto."
elif [ "$D1" != "tenant_unverifiable" ]; then
  FAIL="$FAIL\n   · P1 recusou com \`$D1\`, e nao \`tenant_unverifiable\` — o 403 veio de"$'\n'"     OUTRO guard. O de tenant pode ter sumido sem que este numero mudasse:"$'\n'"     e por isso que o gate afere o MOTIVO, nao so o codigo."
fi

# ── P2 — divergencia de tenant ──────────────────────────────────────────────
# Desde o T2 este probe recusa por um motivo MELHOR do que o original: o tenant vem
# do TOKEN, entao declarar outro no corpo e `tenant_mismatch_token` — a recusa
# acontece ANTES de olhar o meta. O codigo esperado nao mudou (403); a razao, sim.
R2="$(join "$S_OK" "$OUTRO")"; C2="${R2%% *}"; D2="${R2#* }"
echo "   P2 meta com tenant DIVERGENTE ................... HTTP $C2 $D2   (esperado 403 tenant_mismatch_token)"
if [ "$C2" != "403" ]; then
  FAIL="$FAIL\n   · P2 devolveu $C2 — tenant divergente aceito (regressão na metade que já funcionava)"
elif [ "$D2" != "tenant_mismatch_token" ]; then
  FAIL="$FAIL\n   · P2 recusou com \`$D2\` — o 403 veio de outro guard, e a divergencia de"$'\n'"     tenant declarada no corpo pode estar passando sem que nada mude aqui."
fi

# ── P3..P5 — os três sites do mcp-server que resolviam tenant da MESMA chave ──
# `supervisor_capabilities` e `copilot_state` LEEM usando o tenant como prefixo
# (config de pool no agent-registry; `{tenant}:ctx:{session}` no ContextStore) —
# um tenant inventado devolve dado de OUTRO tenant ao Console.
# `session_transfer` ESCREVE (publica roteamento).
MCP="${MCP:-http://localhost:3100}"
BODY="curl -s --max-time 15"
echo
echo "── mcp-server: identidade sem fallback nos três sites ──"
MH="$($CURL "$MCP/health")"
if [ "$MH" != "200" ]; then
  echo "   ⚠ mcp-server não respondeu /health (HTTP $MH) — seção NÃO exercitada."
else
  CAP_NO="$($BODY "$MCP/api/supervisor_capabilities/${S_NO}")"
  CAP_OK="$($BODY "$MCP/api/supervisor_capabilities/${S_OK}")"
  COP_NO="$($BODY "$MCP/api/copilot_state/${S_NO}")"
  COP_OK="$($BODY "$MCP/api/copilot_state/${S_OK}")"
  echo "   P3 supervisor_capabilities  sem tenant → $(printf '%s' "$CAP_NO" | head -c 90)"
  echo "      CONTROLE   com tenant → $(printf '%s' "$CAP_OK" | head -c 90)"
  echo "   P4 copilot_state            sem tenant → $(printf '%s' "$COP_NO" | head -c 90)"
  echo "      CONTROLE   com tenant → $(printf '%s' "$COP_OK" | head -c 90)"
  case "$CAP_NO" in *tenant_unknown*) : ;; *) FAIL="$FAIL\n   · P3 sem tenant NÃO recusou — leitura cross-tenant de config de pool" ;; esac
  case "$CAP_OK" in *tenant_unknown*) FAIL="$FAIL\n   · P3 CONTROLE recusou com tenant presente — recusa indiscriminada" ;; esac
  case "$COP_NO" in *tenant_unknown*) : ;; *) FAIL="$FAIL\n   · P4 sem tenant NÃO recusou — leitura cross-tenant do ContextStore" ;; esac
  case "$COP_OK" in *tenant_unknown*) FAIL="$FAIL\n   · P4 CONTROLE recusou com tenant presente — recusa indiscriminada" ;; esac

  # P5 — o caminho de ESCRITA. Só a RECUSA é exercitada: o ramo positivo
  # publicaria evento de roteamento e XADD para uma sessão de rascunho, e um gate
  # não deve deixar rastro num tópico de produção. Declarado, não escondido.
  # Token pelo helper unico. Antes havia um login proprio aqui, e ele quebrou no dia
  # em que o gate passou a usar `_auth.sh`: os dois definem `AUTH`, mas com
  # significados diferentes (o helper ja inclui `/auth`), e a concatenacao virou
  # `.../auth/auth/login` → 404 → token vazio → P5 silenciosamente nao exercitado.
  # Duas implementacoes de "obter um token" divergem no primeiro ajuste.
  TOK="$(plughub_token 2>/dev/null)"
  if [ -z "$TOK" ]; then
    echo "   ⚠ P5 (session_transfer) NÃO exercitado: sem credencial (plughub_token)."
  else
    C5="$($CURL -X POST "$MCP/api/session_transfer/${S_NO}" $JSON \
          -H "Authorization: Bearer $TOK" -d '{"target_pool":"retencao_humano"}')"
    echo "   P5 session_transfer         sem tenant → HTTP $C5   (esperado 409)"
    [ "$C5" = "409" ] || FAIL="$FAIL\n   · P5 devolveu $C5 — a transferência foi executada (ou falhou por outro motivo)"$'\n'"     com tenant desconhecido: contato re-roteado para namespace inventado"
    echo "   ⚠ o ramo POSITIVO do P5 não é exercitado de propósito — publicaria"
    echo "     evento de roteamento real para uma sessão de rascunho."
  fi
fi

if [ -n "$FAIL" ]; then
  echo; echo "FALHAS:"; printf "$FAIL\n"
  echo; echo "VEREDICTO: VERMELHO"
  exit 1
fi
echo
echo "VEREDICTO: VERDE — PA 401 · P0 200 (pool=$POOL) · P1 403 tenant_unverifiable · P2 403 tenant_mismatch_token · mcp-server P3/P4/P5 recusam sem tenant"
exit 0
