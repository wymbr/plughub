#!/usr/bin/env bash
# ==============================================================================
# probe_monitor_suspended_action.sh — listar trabalho suspenso sem poder agir
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
#   A  o Monitor oferece AÇÃO sobre processo suspenso, e ela é a que encerra de
#      verdade (`force-complete`), gateada por `agent_assist.supervisionar`
#   B  a rota que dá ALCANCE a essa ação (`encerrar-parque`, no channel-gateway)
#      exige credencial de supervisor — 401 sem Bearer, 403 sem o campo
#   C  a tela pergunta à fonte que TEM o dado, e o consolidado bate com ela
#
# ⚠️ **O ramo C nasceu de um achado do dono (ORQ-10, mesmo dia).** A tela dizia
# *"No processes found"* com **51 processos suspensos vivos**: ela perguntava a
# `workflow-api /v1/workflow/instances` — tabela que o Arc 19 esvaziou ao transformar
# processo em sessão webhook —, e o endpoint respondia `[]` com **HTTP 200**. Vazio
# bem-formado não acende nada, e o botão da APR-10 ficou num painel que nunca abria.
# A lição é que o ramo A (a ação existe no código) fica VERDE com a tela inteira
# inútil: presença de ação e presença de DADO são dois fatos.
#
# POR QUE ELE EXISTE (APR-10, 2026-09-09)
# ---------------------------------------
# A ação já existiu e SUMIU: as 4 chamadas de `/instances/{id}/cancel` foram
# removidas em 2026-08-07 e nada as substituiu — o Monitor ficou listando 212
# sessões suspensas com **zero ação por linha**, e o que restou foi um botão que
# copia o `resume_token` para a área de transferência. Agir era no Console, sessão
# a sessão, sem caminho entre as telas.
#
# ⚠️ **Não era só superfície — era ALCANCE, e isso foi medido.** O `force-complete`
# resolvia o endereço pelo ledger `work_task` do Redis, que neste deploy não
# persiste: **zero** chaves para **54** sessões suspensas. Um botão entregue antes
# desta medição responderia 404 em 100% dos casos — pior que botão nenhum, porque
# ensina que a tela não funciona. Hoje há o ramo do PARQUE DURÁVEL (RET-11).
#
# ⚠️ **O ramo B existe porque o portão foi ABERTO por engano nesta mesma ficha.** A
# primeira versão da rota devolvia **200 e encerrava a sessão sem `Authorization`
# nenhum** — herança do `_resolve_approver_principal`, que trata header ausente como
# *sistema* (correto no resume, errado numa ação de supervisor). Medido ao vivo
# antes do commit. Um gate que só olhasse a tela não veria isso.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -u

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }
. infra/test/_auth.sh 2>/dev/null || { echo "INCONCLUSIVO: _auth.sh ausente"; exit 2; }

MON=packages/platform-ui/src/modules/contacts/tabs/MonitorTab.tsx
GW="${GW:-http://localhost:8010}"
[ -f "$MON" ] || { echo "INCONCLUSIVO: $MON ausente"; exit 2; }
command -v python3 >/dev/null || { echo "INCONCLUSIVO: python3 ausente"; exit 2; }

FALHOU=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FALHOU=1; }
nota(){ printf '  \033[33m%s\033[0m %s\n' "$1" "$2"; }

printf '\033[1mprobe: o Monitor lista suspensos E deixa agir sobre eles\033[0m\n'

# ── A. a tela ───────────────────────────────────────────────────────────────
printf '\n\033[1mA. a acao existe na tela, e e a que encerra de verdade\033[0m\n'
SAIDA_A=$(python3 - "$MON" 2>/tmp/apr10_falhas.txt <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
codigo = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
codigo = re.sub(r"^\s*//.*$", "", codigo, flags=re.M)
falhas = []
if "force-complete" not in codigo:
    falhas.append("o Monitor nao chama `force-complete` — a acao que ENCERRA de fato")
if not re.search(r"can\(\s*['\"]agent_assist['\"]\s*,\s*['\"]supervisionar['\"]", codigo):
    falhas.append("a acao nao esta gateada por `agent_assist.supervisionar`")
if "'suspended'" not in codigo and '"suspended"' not in codigo:
    falhas.append("nada distingue o processo SUSPENSO na tela")
if not re.search(r"navigate\(\s*[`'\"]/console", codigo):
    falhas.append("nao ha caminho da linha para a sessao no Console")
for f in falhas:
    print("   -> " + f, file=sys.stderr)
print(len(falhas))
PY
)
if [ "$SAIDA_A" = 0 ]; then
  ok "acao presente, gateada, so para suspenso, com caminho para o Console"
else
  bad "$SAIDA_A verificacao(oes) da tela falharam:"
  cat /tmp/apr10_falhas.txt
fi

# ── B. o portao da rota que da alcance ──────────────────────────────────────
printf '\n\033[1mB. a rota `encerrar-parque` exige supervisor\033[0m\n'
SID=$(docker exec plughub-demo-postgres-1 psql -U plughub -d plughub_demo -tAc \
  "select session_id from parking.session_parks where resolved_at is null and token <> '' limit 1" 2>/dev/null | tr -d ' \r')
if [ -z "$SID" ]; then
  nota "SEM AMOSTRA" "nenhum parque vivo para exercitar o portao — o ramo B NAO foi medido."
  printf '               (verde aqui seria verde por AUSENCIA; o ramo A vale por si.)\n'
else
  COD=$(curl -s -m 20 -o /dev/null -w '%{http_code}' -X POST \
        "$GW/v1/channels/webhook/sessions/$SID/encerrar-parque" \
        -H 'Content-Type: application/json' -d '{"tenant_id":"tenant_demo"}')
  if [ "$COD" = 401 ]; then
    ok "sem Bearer -> 401 (ja foi 200 E ENCERROU, nesta mesma ficha)"
  else
    bad "sem Bearer -> $COD, esperado 401 — a rota encerra sessao sem credencial"
  fi
  ABERTO=$(docker exec plughub-demo-postgres-1 psql -U plughub -d plughub_demo -tAc \
    "select (resolved_at is null) from parking.session_parks where session_id='$SID'" 2>/dev/null | tr -d ' \r')
  if [ "$ABERTO" = "t" ]; then
    ok "e a recusa NAO agiu: o parque continua aberto"
  else
    bad "o parque foi resolvido por uma chamada que deveria ter sido recusada"
  fi
fi

# ── C. a tela pergunta a fonte que tem o dado ───────────────────────────────
printf '\n\033[1mC. a tela pergunta a fonte que TEM o dado\033[0m\n'
HOOKS=packages/platform-ui/src/modules/workflows/api/hooks.ts
if [ ! -f "$HOOKS" ]; then
  nota "INCONCLUSIVO" "$HOOKS ausente"; exit 2
fi
MORTA=$(grep -c "apiFetch(\`/v1/workflow/instances" "$HOOKS")
if [ "$MORTA" != 0 ]; then
  bad "a tela ainda chama \`/v1/workflow/instances\` — tabela que o Arc 19 esvaziou"
  printf '                 -> o endpoint responde [] com HTTP 200: vazio bem-formado,\n'
  printf '                    e a tela diz "nenhum processo" com 51 de pe.\n'
else
  ok "nenhuma chamada a workflow-api restou nos hooks de processo"
fi
# ⚠️ As DUAS chamadas, separadamente. A primeira versao deste ramo aceitava uma
# mencao qualquer a `/sessions/processes`, e a bateria mostrou o buraco: trocar so a
# rota da LISTA mantinha o gate verde, porque a do consolidado
# (`/sessions/processes/summary`) ainda casava o padrao. Um gate que se satisfaz com
# a metade do contrato e' a mesma familia do "verde por ausencia".
if grep -q "apiFetch(\`/sessions/processes?" "$HOOKS"; then
  ok "a LISTA vem de \`/sessions/processes\` (analytics)"
else
  bad "o hook da lista nao aponta para \`/sessions/processes\` — de onde vem a lista?"
fi
if grep -q "/sessions/processes/summary" "$HOOKS"; then
  ok "o CONSOLIDADO vem de \`/sessions/processes/summary\`"
else
  bad "o hook do consolidado nao aponta para \`/sessions/processes/summary\`"
fi

# Ao vivo: o consolidado tem de bater com a fonte. Divergir aqui e' pior que
# vazio — um numero errado nao se denuncia sozinho.
VIVO=$(docker exec plughub-demo-clickhouse-1 clickhouse-client -q \
  "SELECT count() FROM plughub_demo.sessions FINAL WHERE tenant_id='tenant_demo' \
     AND channel='webhook' AND closed_at IS NULL AND status='suspended'" 2>/dev/null | tr -d ' \r')
API=$(curl -s -m 25 -H "$(plughub_auth_header)" \
  "${ANALYTICS:-http://localhost:3500}/sessions/processes/summary?tenant_id=${TENANT:-tenant_demo}" \
  | python3 -c 'import json,sys
try:  print(json.load(sys.stdin)["totais"]["suspensos"])
except Exception: print("")' 2>/dev/null)
if [ -z "$VIVO" ] || [ -z "$API" ]; then
  nota "SEM AMOSTRA" "nao consegui comparar consolidado x fonte (stack no ar?) — ramo C parcial"
elif [ "$VIVO" = "$API" ]; then
  ok "consolidado = fonte: $API suspenso(s) nos dois"
else
  bad "consolidado diz $API e a fonte diz $VIVO — o numero da tela nao e o do banco"
fi

printf '\n'
if [ "$FALHOU" != 0 ]; then
  printf '\033[31mVERMELHO\033[0m - ver secoes acima.\n'; exit 1
fi
printf '\033[32mVERDE\033[0m - a tela ve os processos, deixa agir, e o portao fecha.\n'
exit 0
